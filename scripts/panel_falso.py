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
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent

# Un PNG de 1x1: vale como azulejo del mapa, que aquí no se mira.
PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

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
    "leyenda": {"0": {"id": "minecraft:plains", "es": "Llanura", "en": "Plains", "c": "#79c05a"}},
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
            return self._json(self.estado)
        if p.startswith("/api/mapa2/azulejo"):
            return {"status": 200, "content_type": "image/png", "body": PNG_1PX}
        if p == "/api/mapa2/estructuras":
            self.pedidos["estructuras"].append(ruta)
            e = list(self.estructuras) + (self.gens if "gens=1" in ruta else [])
            return self._json({"ok": True, "estructuras": e, "demasiado": False,
                               "lejos_densas": False})
        if p == "/api/mapa2/hechas":
            return self._json({"ok": True, "hechas": self.hechas})
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
        return ruta.fulfill(**self._api(u))

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
