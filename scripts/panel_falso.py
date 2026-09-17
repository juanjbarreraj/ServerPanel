#!/usr/bin/env python3
"""
Un panel de mentira para probar la pestaña Explorar en un navegador de verdad
sin montar el panel, ni el lector de biomas, ni un Minecraft.

POR QUÉ EXISTE
--------------
Las pruebas de la pestaña Explorar (probar-explorar.py, probar-mapa2.py) piden
el panel corriendo y el servicio de biomas encendido. Eso está bien para la
prueba final, pero deja fuera todo lo demás: en cualquier máquina que no sea el
server no se puede comprobar nada de lo que se ve, que es justo donde más se
rompen las cosas — el CSS que no gana, la vista que no vuelve al recargar, el
cartel que no sale.

Esto sirve `static/` tal cual y contesta las llamadas a `/api/` desde el propio
navegador de prueba. El index.html que se prueba es el que se va a desplegar, sin
tocar una línea; lo único de mentira son las respuestas. Y como las respuestas
las controlamos nosotros, se pueden provocar en segundos casos que en el server
de verdad pasan una vez al año.

No sustituye a probar-explorar.py contra el panel bueno: eso comprueba que los
datos son ciertos. Esto comprueba que la pantalla se comporta.

Uso:
    from panel_falso import PanelFalso, foto
    pf = PanelFalso()
    pag.route("**/*", pf.enruta)
    pag.goto(pf.BASE)
"""
import base64
import io
import json
import os
import re
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Un PNG de 1x1: vale como azulejo del mapa cuando no se miran los píxeles.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def azulejo_png(colores, lado=256):
    """Un azulejo de verdad, en franjas verticales de esos colores.

    Hace falta para probar lo que dice el mapa bajo el ratón: ese nombre sale de
    LEER EL COLOR del píxel ya dibujado y buscarlo en la leyenda. Con un azulejo
    de 1x1 esa parte no se puede probar, y es justo donde se escondió un fallo.
    """
    try:
        from PIL import Image
    except ImportError:
        return PNG_1PX
    im = Image.new("RGB", (lado, lado))
    px = im.load()
    n = len(colores)
    for x in range(lado):
        c = colores[min(n - 1, x * n // lado)]
        for y in range(lado):
            px[x, y] = c
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()

# Las capas, como las manda /api/mapa2/estado del panel de verdad.
TIPOS = [
    {"k": "village", "icono": "village", "es": "Aldeas", "en": "Villages",
     "oculto": False, "paso": 544, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "desert_pyramid", "icono": "desert_pyramid", "es": "Pirámides", "en": "Pyramids",
     "oculto": False, "paso": 512, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "monument", "icono": "monument", "es": "Monumentos", "en": "Monuments",
     "oculto": False, "paso": 512, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "igloo", "icono": "igloo", "es": "Iglús", "en": "Igloos",
     "oculto": False, "paso": 512, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "shipwreck", "icono": "shipwreck", "es": "Naufragios", "en": "Shipwrecks",
     "oculto": False, "paso": 384, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "ocean_ruin", "icono": "ocean_ruin", "es": "Ruinas oceánicas", "en": "Ocean ruins",
     "oculto": False, "paso": 320, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "buried_treasure", "icono": "buried_treasure", "es": "Tesoros", "en": "Treasure",
     "oculto": False, "paso": 16, "densa": True, "cerca": 24576, "delMundo": False},
    {"k": "mineshaft", "icono": "mineshaft", "es": "Minas", "en": "Mineshafts",
     "oculto": False, "paso": 16, "densa": True, "cerca": 24576, "delMundo": False},
    {"k": "spawner", "icono": "spawner", "es": "Generadores", "en": "Spawners",
     "oculto": False, "paso": 3000, "densa": False, "cerca": 0, "delMundo": True},
]

ESTADO = {
    "ok": True, "semilla": "1244994422874902852", "y": 63,
    "niveles": [4, 8, 16, 32, 64, 128, 256, 512], "tam": 256,
    # Dos biomas, no uno: con uno solo no se puede comprobar que el nombre que
    # sale bajo el ratón es el del sitio donde está el ratón.
    "leyenda": {
        "0": {"id": "minecraft:plains", "es": "Llanura", "en": "Plains", "c": "#8db360"},
        "8": {"id": "minecraft:dappled_forest", "es": "Bosque moteado",
              "en": "Dappled forest", "c": "#a55f16"},
        # Los dos colores MÁS JUNTOS de la paleta de verdad (distancia 5,74).
        # Están aquí para que la prueba del margen se haga contra el caso peor.
        "20": {"id": "minecraft:windswept_hills", "es": "Colinas ventosas",
               "en": "Windswept Hills", "c": "#606060"},
        "21": {"id": "minecraft:basalt_deltas", "es": "Deltas de basalto",
               "en": "Basalt Deltas", "c": "#645f64"},
    },
    "tipos": TIPOS, "aparicion": [0, 64, 0], "version": "26.2",
    "dimension": "Overworld", "fortalezas": [],
    "variantes": {"spawner_zombie": {"icono": "spawner_zombie",
                                     "es": "Generador de zombis", "en": "Zombie spawner"}},
}


class _Silencio(SimpleHTTPRequestHandler):
    """El servidor de ficheros del panel: `/` es index.html y `/static/x` es x."""

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        elif self.path.startswith("/static/"):
            self.path = self.path[len("/static"):]
        return SimpleHTTPRequestHandler.do_GET(self)


class PanelFalso:
    """Sirve static/ y contesta /api/. Todo lo demás se toca desde fuera."""

    def __init__(self, estado=None, rol="admin"):
        self.web = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(_Silencio, directory=str(RAIZ / "static")))
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.BASE = "http://127.0.0.1:%d" % self.web.server_address[1]
        self.estado = json.loads(json.dumps(estado or ESTADO))   # copia, no la de todos
        self.rol = rol
        self.estructuras = [["village", 300, 300, None], ["monument", -900, 700, None]]
        self.gens = []                 # los que salen solo si se piden con gens=1
        self.hechas = {}
        self.pedidos = {"estructuras": [], "estado": []}
        # subida de mundos: se apunta lo que llega para poder comprobarlo
        self.subidas = {}              # id → bytes recibidos
        self.mundo_info = {"ok": True, "legible": True, "version": "26.2",
                           "version_servidor": "26.2", "bytes": 0,
                           "mas_nueva": False, "activo_nombre": "mundo de ahora"}
        # Desfase de versión del mapa Explorar. Por defecto, al día: así las
        # pruebas de siempre no ven un aviso que no esperaban.
        self.desfase = {"jar": "server-26.2.jar", "version": "26.2",
                        "biomas_jar": "server-26.2.jar", "al_dia": True,
                        "javac": True, "motivo": "", "trabajando": False}
        self.actualizados = 0          # cuántas veces se pulsó el botón
        # Biomas que el servicio conoce y la paleta del panel no sabe pintar.
        self.estado.setdefault("sin_color", [])
        self.estado.setdefault("huella", "abc12345")
        # Mitad izquierda llanura, mitad derecha bosque moteado — los mismos
        # colores que la leyenda de arriba.
        self.azulejo = azulejo_png([(141, 179, 96), (165, 95, 22)])

    # ---------------------------------------------------------------- rutas
    @staticmethod
    def _json(cuerpo):
        return {"status": 200, "content_type": "application/json",
                "body": cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)}

    def _api(self, ruta):
        p = ruta.split("?")[0]
        if p == "/api/branding":
            return self._json({"name": "Califree"})
        if p == "/api/me":
            return self._json({"user": {"name": "juan", "role": self.rol, "perms": {}}})
        if p == "/api/status":
            return self._json({"ok": True, "online": True, "players": [], "state": "active",
                               "metrics": {"mc_mem_mb": 2048, "cpu": 12, "tps": 20},
                               "version": "26.2", "motd": "rig"})
        if p == "/api/players":
            return self._json({"players": []})
        if p == "/api/mapa2/estado":
            self.pedidos["estado"].append(ruta)
            return self._json(dict(self.estado, desfase=dict(self.desfase)))
        if p.startswith("/api/mapa2/azulejo"):
            return {"status": 200, "content_type": "image/png", "body": self.azulejo}
        if p == "/api/mapa2/estructuras":
            self.pedidos["estructuras"].append(ruta)
            e = list(self.estructuras) + (self.gens if "gens=1" in ruta else [])
            return self._json({"ok": True, "estructuras": e, "demasiado": False,
                               "lejos_densas": False})
        if p == "/api/mapa2/hechas":
            return self._json({"ok": True, "hechas": self.hechas})
        if p == "/api/mapa2/version":
            return self._json(dict(self.desfase))
        if p == "/api/mundos":
            return self._json({"ok": True, "mundos": [], "activo": "world",
                               "trabajo": {"estado": "quieto"}, "papelera": []})
        return self._json({"ok": True})

    def enruta(self, ruta):
        """El manejador para pag.route("**/*", pf.enruta)."""
        url = ruta.request.url
        # Fuera de este servidor no hay nada. Se contesta vacío en vez de cortar:
        # la hoja de estilos de las fuentes de Google, cortada a lo bruto, deja a
        # la página esperando fuentes para siempre y las capturas caducan.
        if not url.startswith(self.BASE):
            return ruta.fulfill(status=200, body="", content_type="text/css")
        u = url[len(self.BASE):]
        if not u.startswith("/api/"):
            return ruta.continue_()
        p = u.split("?")[0]
        if p == "/api/mundos/trozo" and ruta.request.method == "POST":
            # Hay que contar los bytes EXACTOS, no aproximarlos. El navegador
            # sigue subiendo hasta que el servidor le dice que ya tiene el
            # archivo entero; si aquí se cuenta de menos, la subida no termina
            # nunca. (Así se descubrió que el panel tampoco se protegía de eso.)
            crudo = ruta.request.post_data_buffer or b""
            campos = self._formulario(crudo, ruta.request.headers.get("content-type", ""))
            ident = (campos.get("id") or b"?").decode("utf-8", "replace")
            self.subidas[ident] = self.subidas.get(ident, 0) + len(campos.get("trozo") or b"")
            return ruta.fulfill(**self._json({"ok": True, "recibido": self.subidas[ident]}))
        if p == "/api/mapa2/actualizar" and ruta.request.method == "POST":
            self.actualizados += 1
            self.desfase = dict(self.desfase, trabajando=True)
            return ruta.fulfill(**self._json({"ok": True, "output": "Actualizando el mapa."}))
        if p == "/api/mundos/inspeccionar" and ruta.request.method == "POST":
            cuerpo = json.loads(ruta.request.post_data or "{}")
            info = dict(self.mundo_info)
            info["bytes"] = self.subidas.get(cuerpo.get("id"), 0)
            return ruta.fulfill(**self._json(info))
        return ruta.fulfill(**self._api(u))

    @staticmethod
    def _formulario(crudo, tipo):
        """{nombre: bytes} de un multipart/form-data, sin librerías."""
        m = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', tipo or "")
        if not m or not crudo:
            return {}
        sep = b"--" + (m.group(1) or m.group(2)).strip().encode()
        fuera = {}
        for parte in crudo.split(sep):
            i = parte.find(b"\r\n\r\n")
            if i < 0:
                continue
            cab = parte[:i].decode("utf-8", "replace")
            n = re.search(r'name="([^"]*)"', cab)
            if n:
                fuera[n.group(1)] = parte[i + 4:].rstrip(b"\r\n")
        return fuera

    # ---------------------------------------------------------------- ayudas
    def al_mapa(self, pag):
        """Entra al panel y abre la pestaña Explorar, ya cargada."""
        pag.goto(self.BASE, wait_until="domcontentloaded")
        pag.wait_for_selector("#tabs button", timeout=15000)
        pag.click("#tabbtn-mapa2")
        pag.wait_for_function("() => M2.listo && M2.est", timeout=20000)
        pag.wait_for_timeout(500)

    def para(self):
        self.web.shutdown()


# ---------------------------------------------------------------------- fotos
# FOTOS=/una/carpeta deja capturas: es la única forma de mirar si algo está
# torcido sin montar el panel entero.
FOTOS = Path(os.environ["FOTOS"]) if os.environ.get("FOTOS") else None
if FOTOS:
    FOTOS.mkdir(parents=True, exist_ok=True)


def foto(pag, nombre):
    """Captura por CDP, no con page.screenshot().

    El index.html pide dos fuentes que aquí no existen (mcfont.ttf vive en el
    server y la de Google no se puede salir a buscar). Playwright espera a que
    las fuentes acaben antes de disparar y se queda esperando para siempre; la
    captura de Chrome a pelo no pregunta por fuentes.
    """
    if not FOTOS:
        return
    try:
        cdp = pag.context.new_cdp_session(pag)
        datos = cdp.send("Page.captureScreenshot", {"format": "png"})
        (FOTOS / (nombre + ".png")).write_bytes(base64.b64decode(datos["data"]))
    except Exception as e:
        print("    (no pude sacar la foto %s: %s)" % (nombre, e))
