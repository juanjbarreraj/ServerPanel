#!/usr/bin/env python3
"""
El botón «buscar generadores en esta zona» EN PANTALLA, con un panel de mentira.

POR QUÉ ASÍ
-----------
La otra prueba (probar-zona.py) comprueba el trabajo: que se genera el cuadro,
que no quedan chunks forzados, que los generadores aparecen. Lo que no puede
comprobar es lo que ve Juan: si el cartel sale, si el botón se apaga mientras
trabaja, si la barra avanza, si el cuadro se dibuja donde toca, si al terminar
la capa se enciende sola y los iconos aparecen.

Eso solo se ve en un navegador de verdad. Y para tenerlo no hace falta el panel
entero ni el lector de biomas ni un Minecraft: hace falta el index.html que se
va a desplegar y unas respuestas creíbles. Así que aquí se sirve static/ tal
cual y se contestan las llamadas a /api/ desde el propio Playwright, con una
búsqueda simulada que avanza sola de «generando» a «listo».

Como el panel de mentira lo controlamos nosotros, se pueden provocar en tres
segundos los casos que en el server de verdad pasan una vez al año: la búsqueda
que se queda a medias, la que ya estaba en marcha cuando abres la pestaña, la
que se hizo hace media hora.

Hace falta:  pip install playwright && playwright install chromium
Correr:      python3 scripts/probar-zona-ui.py
"""
import base64
import json
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright:  pip install playwright && playwright install chromium")
    sys.exit(2)

RAIZ = Path(__file__).resolve().parent.parent
FALLOS = []
# FOTOS=/una/carpeta deja capturas de cómo queda: sirve para mirarlo sin montar
# el panel entero, que es la única forma de ver si algo está torcido.
FOTOS = Path(os.environ["FOTOS"]) if os.environ.get("FOTOS") else None
if FOTOS:
    FOTOS.mkdir(parents=True, exist_ok=True)


def foto(pag, nombre):
    """Captura por CDP, no con page.screenshot().

    El index.html pide dos fuentes que aquí no existen (mcfont.ttf vive en el
    server, y la de Google no se puede salir a buscar). Playwright espera a que
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


def ok(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


# ── servir static/ tal cual, como hace el panel ──────────────────────────────
class Silencio(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self.path = "/index.html"
        elif self.path.startswith("/static/"):
            self.path = self.path[len("/static"):]
        return SimpleHTTPRequestHandler.do_GET(self)


web = ThreadingHTTPServer(("127.0.0.1", 0),
                          partial(Silencio, directory=str(RAIZ / "static")))
threading.Thread(target=web.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:%d" % web.server_address[1]

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")

TIPOS = [
    {"k": "village", "icono": "village", "es": "Aldeas", "en": "Villages",
     "oculto": False, "paso": 544, "densa": False, "cerca": 0, "delMundo": False},
    {"k": "monument", "icono": "monument", "es": "Monumentos", "en": "Monuments",
     "oculto": False, "paso": 512, "densa": False, "cerca": 0, "delMundo": False},
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

# el panel de mentira: una búsqueda que avanza sola
TRABAJO = {"estado": "quieto", "hechos": 0, "total": 0, "mensaje": "",
           "t": 0, "hallados": None, "caja": None, "ok": True, "max_chunks": 1024}
GUION = []          # [(segundos desde el arranque, parche)] — lo que irá pasando
PEDIDOS = {"zona": [], "estructuras": []}
GENS = []           # generadores que «aparecen» al terminar
ROL = {"v": "admin"}


def ahora_toca():
    """El trabajo simulado según el guion y el tiempo que lleva."""
    if TRABAJO["estado"] in ("generando", "leyendo"):
        pasado = time.time() - TRABAJO["t"]
        for cuando, parche in GUION:
            if pasado >= cuando:
                TRABAJO.update(parche)
    return TRABAJO


def responde(ruta, cuerpo, tipo="application/json"):
    return {"status": 200, "content_type": tipo,
            "body": cuerpo if isinstance(cuerpo, (str, bytes)) else json.dumps(cuerpo)}


def panel(ruta):
    p = ruta.split("?")[0]
    if p == "/api/branding":
        return responde(p, {"name": "Califree"})
    if p == "/api/me":
        return responde(p, {"user": {"name": "juan", "role": ROL["v"], "perms": {}}})
    if p == "/api/status":
        return responde(p, {"ok": True, "online": True, "players": [], "state": "active",
                            "metrics": {"mc_mem_mb": 2048, "cpu": 12, "tps": 20},
                            "version": "26.2", "motd": "rig"})
    if p == "/api/players":
        return responde(p, {"players": []})
    if p == "/api/mapa2/estado":
        return responde(p, ESTADO)
    if p.startswith("/api/mapa2/azulejo"):
        return {"status": 200, "content_type": "image/png", "body": PNG}
    if p == "/api/mapa2/estructuras":
        PEDIDOS["estructuras"].append(ruta)
        e = [["village", 300, 300, None], ["monument", -900, 700, None]]
        if "gens=1" in ruta:
            e += GENS
        return responde(p, {"ok": True, "estructuras": e, "demasiado": False,
                            "lejos_densas": False})
    if p == "/api/mapa2/hechas":
        return responde(p, {"ok": True, "hechas": {}})
    if p == "/api/mapa2/zona":
        return responde(p, ahora_toca())
    return responde(p, {"ok": True})


def arranca(caja):
    TRABAJO.update({"estado": "generando", "hechos": 0, "total": 1024, "mensaje": "",
                    "t": time.time(), "hallados": None, "caja": caja})


with sync_playwright() as pw:
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    errores = []
    pag.on("pageerror", lambda e: errores.append(str(e)))
    pag.on("console", lambda m: errores.append("console: " + m.text)
           if m.type == "error" and "Failed to load resource" not in m.text else None)

    def enruta(ruta):
        url = ruta.request.url
        # Fuera de este servidor no hay nada. Se contesta vacío en vez de cortar:
        # la hoja de estilos de las fuentes de Google, cortada a lo bruto, deja
        # a la página esperando fuentes para siempre y las capturas caducan.
        if not url.startswith(BASE):
            return ruta.fulfill(status=200, body="", content_type="text/css")
        u = url[len(BASE):]
        if not u.startswith("/api/"):
            return ruta.continue_()
        if u.split("?")[0] == "/api/mapa2/zona" and ruta.request.method == "POST":
            cuerpo = json.loads(ruta.request.post_data or "{}")
            PEDIDOS["zona"].append(cuerpo)
            if ROL["v"] not in ("admin", "mod"):
                return ruta.fulfill(status=403, body="{}", content_type="application/json")
            caja = [cuerpo["x0"], cuerpo["z0"], cuerpo["x1"], cuerpo["z1"]]
            arranca(caja)
            return ruta.fulfill(**responde(u, {"ok": True, "chunks": 1024, "caja": caja}))
        return ruta.fulfill(**panel(u))

    pag.route("**/*", enruta)

    def al_mapa():
        pag.goto(BASE, wait_until="domcontentloaded")
        pag.wait_for_selector("#tabs button", timeout=15000)
        pag.click("#tabbtn-mapa2")
        pag.wait_for_function("() => M2.listo && M2.est", timeout=20000)
        pag.wait_for_timeout(600)

    # ── quién ve el cartel ────────────────────────────────────────────────
    print("── quién ve el cartel ──")
    al_mapa()
    ok(pag.locator("#m2-zona").is_visible(), "un admin ve el cartel de buscar")
    foto(pag, "1-cartel")
    texto = pag.inner_text("#m2-zona")
    ok("cueva" in texto and "más lento" in texto,
       "que explica por qué no se calculan y qué cuesta")

    pag.evaluate("() => { M2.apagados.add('spawner'); m2Capas(); }")
    ok(not pag.locator("#m2-zona").is_visible(),
       "con la capa de generadores apagada, el cartel se va")
    pag.evaluate("() => { M2.apagados.delete('spawner'); m2Capas(); }")
    ok(pag.locator("#m2-zona").is_visible(), "y vuelve al encenderla")

    ROL["v"] = "viewer"
    al_mapa()
    ok(not pag.locator("#m2-zona").is_visible(), "un observador no lo ve")
    ROL["v"] = "admin"

    # ── el cuadro que se va a buscar ──────────────────────────────────────
    print("── el cuadro ──")
    al_mapa()
    pag.evaluate("() => m2Volar(1000, -2000, 1/8)")     # muy alejado: no cabe entero
    pag.wait_for_timeout(300)
    c = pag.evaluate("() => m2ZonaCaja()")
    ok(c["chunks"] == 1024, "alejado, se recorta a 1024 chunks (%s)" % c["chunks"])
    ok(abs((c["x0"] + c["x1"]) / 2 - 1000) < 260 and abs((c["z0"] + c["z1"]) / 2 + 2000) < 260,
       "y queda centrado en lo que se mira (%d, %d)" % ((c["x0"] + c["x1"]) // 2,
                                                        (c["z0"] + c["z1"]) // 2))
    ok(c["x0"] % 16 == 0 and (c["x1"] + 1) % 16 == 0, "pegado a los chunks, no a medio chunk")

    # La regla es «lo que se ve, y como mucho 32x32». Se comprueba tal cual, a
    # varios zooms, en vez de dar por hecho cuánto mundo cabe en esta ventana.
    REGLA = """() => { const c=m2ZonaCaja();
        const vx=Math.floor(m2dex(M2.w)/16)-Math.floor(m2dex(0)/16)+1;
        const vz=Math.floor(m2dez(M2.h)/16)-Math.floor(m2dez(0)/16)+1;
        return {ancho:(c.x1-c.x0+1)/16, alto:(c.z1-c.z0+1)/16,
                vistaX:vx, vistaZ:vz, chunks:c.chunks}; }"""
    for esc in ("1/8", "1/2", "1"):
        pag.evaluate("(e) => m2Volar(1000, -2000, e)", eval(esc))
        pag.wait_for_timeout(250)
        r = pag.evaluate(REGLA)
        ok(r["ancho"] == min(32, r["vistaX"]) and r["alto"] == min(32, r["vistaZ"]),
           "a %s px por bloque busca lo que se ve con tope de 32 (%dx%d de %dx%d)"
           % (esc, r["ancho"], r["alto"], r["vistaX"], r["vistaZ"]))
    pag.evaluate("() => m2Volar(0, 0, 1/8)")
    pag.wait_for_timeout(300)

    AMARILLO = """() => {
      const c=document.getElementById('m2-ico');
      const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
      let n=0;
      for (let i=0;i<d.length;i+=4)
        if (d[i]>235 && d[i+1]>195 && d[i+1]<225 && d[i+2]>70 && d[i+2]<120 && d[i+3]>200) n++;
      return n; }"""
    # Se cuenta la DIFERENCIA de píxeles ámbar, no el total: en el mapa ya hay
    # algún punto de ese color (una estructura sin icono se dibuja así), y una
    # prueba que exija un lienzo limpio miente en cuanto cambie el decorado.
    fondo = pag.evaluate(AMARILLO)
    pag.hover("#m2-zona-btn")
    pag.wait_for_timeout(300)
    conCuadro = pag.evaluate(AMARILLO)
    ok(conCuadro > fondo + 200, "al pasar por encima del botón se dibuja el cuadro (+%d px)"
       % (conCuadro - fondo))
    pag.mouse.move(10, 10)
    pag.wait_for_timeout(300)
    ok(pag.evaluate(AMARILLO) == fondo, "y se quita al apartarse")

    # ── la búsqueda de principio a fin ────────────────────────────────────
    print("── la búsqueda ──")
    GENS[:] = [["spawner", 40, -40, "spawner_zombie"], ["spawner", 300, 120, "spawner_zombie"]]
    GUION[:] = [(0.0, {"hechos": 256}), (1.2, {"hechos": 640}),
                (2.4, {"estado": "leyendo", "hechos": 1024}),
                (3.6, {"estado": "listo", "hechos": 1024, "hallados": 2})]
    PEDIDOS["zona"].clear()
    PEDIDOS["estructuras"].clear()
    pag.evaluate("() => { M2.apagados.add('spawner'); m2Capas(); m2Guardar(); }")
    pag.evaluate("() => { document.getElementById('m2-zona').classList.add('ver'); }")
    pag.click("#m2-zona-btn")
    pag.wait_for_timeout(500)

    ok(len(PEDIDOS["zona"]) == 1, "se pide una sola vez (%d)" % len(PEDIDOS["zona"]))
    pedido = PEDIDOS["zona"][0]
    ok(all(k in pedido for k in ("x0", "z0", "x1", "z1")), "con las cuatro esquinas")
    ok(pedido["x1"] - pedido["x0"] + 1 == 512, "un cuadro de 512 bloques de lado")
    ok(pag.locator("#m2-zona-btn").is_disabled(), "el botón se apaga mientras trabaja")
    ok(pag.locator("#m2-zona-barra").is_visible(), "y sale la barra de avance")
    ok("generando" in pag.inner_text("#m2-zona-p"),
       "diciendo qué hace: %r" % pag.inner_text("#m2-zona-p"))
    ancho1 = pag.evaluate("() => document.getElementById('m2-zona-i').style.width")
    foto(pag, "2-buscando")

    pag.wait_for_function("() => document.getElementById('m2-zona-p').textContent.includes('leyendo')",
                          timeout=15000)
    ancho2 = pag.evaluate("() => document.getElementById('m2-zona-i').style.width")
    ok(ancho1 != ancho2, "la barra avanza de verdad (%s → %s)" % (ancho1, ancho2))
    ok(pag.evaluate(AMARILLO) > fondo + 200, "el cuadro sigue marcado mientras trabaja")

    pag.wait_for_function("() => document.getElementById('m2-zona-p').textContent.startsWith('listo')",
                          timeout=15000)
    pag.wait_for_timeout(700)
    ok("2 generadores" in pag.inner_text("#m2-zona-p"),
       "al acabar dice cuántos: %r" % pag.inner_text("#m2-zona-p"))
    foto(pag, "3-listo")
    ok(not pag.locator("#m2-zona-btn").is_disabled(), "el botón vuelve")
    ok(not pag.locator("#m2-zona-barra").is_visible(), "la barra se va")
    ok(not pag.evaluate("() => M2.apagados.has('spawner')"),
       "la capa de generadores se enciende sola")
    ok(any("gens=1" in u for u in PEDIDOS["estructuras"]),
       "y se vuelven a pedir las estructuras con los generadores")
    dibujados = pag.evaluate("() => M2.enPantalla.filter(e=>e[0]==='spawner').length")
    ok(dibujados == 2, "los dos generadores salen en el mapa (%d)" % dibujados)

    # ── una que se queda a medias ─────────────────────────────────────────
    print("── una búsqueda que se queda a medias ──")
    GUION[:] = [(0.0, {"estado": "listo", "hechos": 300, "hallados": 1})]
    pag.click("#m2-zona-btn")
    pag.wait_for_function("() => document.getElementById('m2-zona-p').textContent.startsWith('listo')",
                          timeout=15000)
    txt = pag.inner_text("#m2-zona-p")
    ok("300 de 1024" in txt, "lo dice en vez de fingir que acabó: %r" % txt)
    ok("mal" in (pag.get_attribute("#m2-zona-p", "class") or ""), "y se ve que algo no cuadró")

    # ── engancharse a una que ya estaba en marcha ─────────────────────────
    print("── al abrir la pestaña ──")
    GUION[:] = [(2.0, {"estado": "listo", "hechos": 1024, "hallados": 7})]
    arranca([0, 0, 511, 511])
    al_mapa()
    pag.wait_for_timeout(400)
    ok("generando" in pag.inner_text("#m2-zona-p") or "leyendo" in pag.inner_text("#m2-zona-p"),
       "se engancha a la búsqueda que ya había: %r" % pag.inner_text("#m2-zona-p"))
    ok(pag.locator("#m2-zona-btn").is_disabled(), "y no deja arrancar otra encima")
    pag.wait_for_function("() => document.getElementById('m2-zona-p').textContent.includes('7')",
                          timeout=20000)
    ok(True, "y ve el final aunque no fuera esta pestaña quien la lanzó")

    TRABAJO.update({"estado": "listo", "t": time.time() - 3600, "hechos": 1024,
                    "total": 1024, "hallados": 7})
    GUION[:] = []
    al_mapa()
    pag.wait_for_timeout(500)
    ok(pag.inner_text("#m2-zona-p").strip() == "",
       "un resultado de hace una hora no saluda: %r" % pag.inner_text("#m2-zona-p"))
    ok(pag.evaluate(AMARILLO) <= fondo, "ni deja el cuadro pintado")

    # ── el móvil ──────────────────────────────────────────────────────────
    print("── en una pantalla estrecha ──")
    pag.set_viewport_size({"width": 390, "height": 780})
    pag.wait_for_timeout(500)
    caja = pag.evaluate("""() => { const e=document.getElementById('m2-zona');
        const r=e.getBoundingClientRect(); return {x:r.x, ancho:r.width, alto:r.height}; }""")
    ok(caja["x"] >= 0 and caja["x"] + caja["ancho"] <= 391,
       "el cartel cabe en el ancho (%d..%d)" % (caja["x"], caja["x"] + caja["ancho"]))
    ok(caja["alto"] < 340, "y no se come la pantalla (%d px)" % caja["alto"])
    b = pag.evaluate("""() => { const r=document.getElementById('m2-zona-btn').getBoundingClientRect();
        return {alto:r.height, ancho:r.width}; }""")
    ok(b["alto"] >= 36, "el botón se puede tocar con el dedo (%d px de alto)" % b["alto"])
    foto(pag, "4-movil")

    ok(not errores, "sin errores de JavaScript" + (": %s" % errores[:2] if errores else ""))
    nav.close()

web.shutdown()
print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
