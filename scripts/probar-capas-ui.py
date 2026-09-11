#!/usr/bin/env python3
"""
La tira de capas del mapa: que siempre es la vista de entrada, y que al pasar
por encima de un icono se sabe cuál es.

POR QUÉ ASÍ
-----------
Son dos cosas que solo existen en la pantalla:

  · La tira de iconos tiene que volver SOLA. Se abre la lista con nombres para
    mirar algo, se cambia de pestaña o se recarga, y lo que debe aparecer es la
    tira otra vez, aunque el navegador tuviera guardada la otra vista de antes.
  · En la tira hay veinte cuadraditos sin nombre. Al pasar por encima tiene que
    salir el nombre y encenderse el icono; al hacer clic se queda encendido.

Nada de eso se comprueba llamando funciones: es CSS que puede perder contra otra
regla, un `title` del navegador que sale tarde y duplicado, y un estado que se
guardaba donde no debía. Así que se carga el index.html de verdad contra el
`panel_falso.py` y se miran los píxeles.

Hace falta:  pip install playwright && playwright install chromium
Correr:      python3 scripts/probar-capas-ui.py
             FOTOS=/tmp/fotos python3 scripts/probar-capas-ui.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_falso import PanelFalso, foto                     # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright:  pip install playwright && playwright install chromium")
    sys.exit(2)

FALLOS = []


def ok(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


pf = PanelFalso()
MODO = "() => document.getElementById('m2-capas').className"
OPACIDAD = """(id) => { const im=document.querySelector('#'+id+' img');
    return im ? parseFloat(getComputedStyle(im).opacity) : -1; }"""
CARTEL = """() => { const t=document.getElementById('mctip');
    return {visible:getComputedStyle(t).display!=='none', texto:t.innerText.trim()}; }"""

with sync_playwright() as pw:
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    errores = []
    pag.on("pageerror", lambda e: errores.append(str(e)))
    pag.on("console", lambda m: errores.append("console: " + m.text)
           if m.type == "error" and "Failed to load resource" not in m.text else None)
    pag.route("**/*", pf.enruta)

    # ── la tira es la casa ────────────────────────────────────────────────
    print("── la vista de entrada ──")
    pf.al_mapa(pag)
    ok("tira" in pag.evaluate(MODO), "arranca en la tira de iconos")
    foto(pag, "1-tira")

    pag.click("#m2-modo")
    pag.wait_for_timeout(300)
    ok("lista" in pag.evaluate(MODO), "el botón de la derecha abre la lista con nombres")
    nombres = pag.evaluate("""() => { const e=document.querySelector('#m2-capas .m2-cap .et');
        return e ? getComputedStyle(e).display : 'no hay'; }""")
    ok(nombres != "none", "y ahí sí se leen los nombres (display %s)" % nombres)
    foto(pag, "2-lista")

    # Recargar con la lista abierta: tiene que volver la tira.
    pag.reload(wait_until="domcontentloaded")
    pag.wait_for_selector("#tabs button", timeout=15000)
    pag.click("#tabbtn-mapa2")
    pag.wait_for_function("() => M2.listo", timeout=20000)
    pag.wait_for_timeout(400)
    ok("tira" in pag.evaluate(MODO), "al recargar vuelve la tira, no la lista")

    # Irse a otra pestaña del panel y volver: lo mismo, y sin recargar nada.
    pag.click("#m2-modo")
    pag.wait_for_timeout(250)
    ok("lista" in pag.evaluate(MODO), "(se deja la lista abierta a propósito)")
    pag.click("#tabbtn-dash")
    pag.wait_for_timeout(400)
    pag.click("#tabbtn-mapa2")
    pag.wait_for_timeout(400)
    ok("tira" in pag.evaluate(MODO), "yendo a otra pestaña y volviendo, también vuelve la tira")

    # El caso de verdad: un navegador que YA tenía guardada la lista de antes de
    # este cambio. Si se respetara, Juan seguiría viendo la lista para siempre.
    pag.evaluate("""() => { const g=JSON.parse(localStorage.getItem('panel_m2')||'{}');
        g.modo='lista'; localStorage.setItem('panel_m2', JSON.stringify(g)); }""")
    pag.reload(wait_until="domcontentloaded")
    pag.wait_for_selector("#tabs button", timeout=15000)
    pag.click("#tabbtn-mapa2")
    pag.wait_for_function("() => M2.listo", timeout=20000)
    pag.wait_for_timeout(400)
    ok("tira" in pag.evaluate(MODO), "y una preferencia vieja guardada ya no manda")
    # y en cuanto el panel vuelve a guardar, la clave vieja desaparece del todo
    pag.click("#m2cap-monument")
    pag.wait_for_timeout(250)
    guardado = pag.evaluate("() => localStorage.getItem('panel_m2')")
    ok('"modo"' not in (guardado or ""),
       "y a la primera que guarda, la clave vieja desaparece")

    # ── pasar por encima ──────────────────────────────────────────────────
    print("── al pasar por encima de un icono ──")
    apagado = pag.evaluate("""() => { const b=document.getElementById('m2cap-village');
        return b && b.classList.contains('on'); }""")
    if apagado:                       # se quiere una capa APAGADA para la prueba
        pag.click("#m2cap-village")
        pag.wait_for_timeout(250)
    ok(not pag.evaluate("() => document.getElementById('m2cap-village').classList.contains('on')"),
       "partimos de una capa apagada")
    pag.mouse.move(640, 20)                      # el ratón, fuera de la tira
    pag.wait_for_timeout(300)
    quieto = pag.evaluate(OPACIDAD, "m2cap-village")
    ok(quieto < 0.6, "apagada, el icono está a media luz (%.2f)" % quieto)
    ok(not pag.evaluate(CARTEL)["visible"], "y con el ratón fuera no hay ningún cartel")

    pag.hover("#m2cap-village")
    pag.wait_for_timeout(350)
    cartel = pag.evaluate(CARTEL)
    ok(cartel["visible"], "al pasar por encima sale el cartel")
    ok("Aldeas" in cartel["texto"], "con el nombre de la capa: %r" % cartel["texto"])
    encendido = pag.evaluate(OPACIDAD, "m2cap-village")
    ok(encendido > 0.95, "y el icono se enciende (%.2f → %.2f)" % (quieto, encendido))
    foto(pag, "3-encima")

    # el `title` del navegador saldría además del cartel, y un segundo más tarde
    ok(not pag.get_attribute("#m2cap-village", "title"),
       "sin el cartelito del navegador encima, que saldrían dos")
    ok(pag.get_attribute("#m2cap-village", "aria-label") == "Aldeas",
       "pero el nombre sigue estando para quien no ve la pantalla")

    pag.mouse.move(640, 20)
    pag.wait_for_timeout(350)
    ok(not pag.evaluate(CARTEL)["visible"], "al apartarse, el cartel se va")
    ok(pag.evaluate(OPACIDAD, "m2cap-village") < 0.6, "y el icono vuelve a media luz")

    # ── y al hacer clic se queda ──────────────────────────────────────────
    print("── al hacer clic ──")
    pag.click("#m2cap-village")
    pag.wait_for_timeout(300)
    pag.mouse.move(640, 20)
    pag.wait_for_timeout(300)
    ok(pag.evaluate("() => document.getElementById('m2cap-village').classList.contains('on')"),
       "la capa queda encendida")
    ok(pag.evaluate(OPACIDAD, "m2cap-village") > 0.95,
       "y el icono se queda iluminado aunque el ratón se haya ido")

    # el aro verde del clic no lo puede pisar el de pasar por encima
    aro = pag.evaluate("""() => { const b=document.getElementById('m2cap-village');
        return getComputedStyle(b).boxShadow; }""")
    pag.hover("#m2cap-village")
    pag.wait_for_timeout(300)
    ok(pag.evaluate("""() => getComputedStyle(document.getElementById('m2cap-village')).boxShadow""")
       == aro, "y pasar por encima de una encendida no la cambia")

    # ── los otros dos, que no son estructuras ─────────────────────────────
    print("── el punto de aparición y los slimes ──")
    for id_, nombre in (("m2cap-slime", "slime"), ("m2cap-aparicion", "aparición")):
        pag.hover("#" + id_)
        pag.wait_for_timeout(300)
        c = pag.evaluate(CARTEL)
        ok(c["visible"] and nombre.lower() in c["texto"].lower(),
           "%s también se presenta: %r" % (id_, c["texto"].replace("\n", " · ")))

    # ── en la lista con nombres ───────────────────────────────────────────
    print("── en la lista con nombres ──")
    pag.click("#m2-modo")
    pag.wait_for_timeout(300)
    pag.hover("#m2cap-monument")
    pag.wait_for_timeout(300)
    ok(pag.evaluate(CARTEL)["visible"], "el cartel también sale en la lista")
    ok(pag.evaluate(OPACIDAD, "m2cap-monument") > 0.95, "y el icono se enciende igual")

    # ── el aviso de la capa de generadores ────────────────────────────────
    # Es la única capa que no cubre el mundo entero: lee los generadores que ya
    # existen en el terreno visitado. Sin decirlo, en medio mapa parece rota.
    print("── el aviso de los generadores ──")
    pf.al_mapa(pag)
    encendida = pag.evaluate("() => !M2.apagados.has('spawner')")
    if not encendida:
        pag.click("#m2cap-spawner")
        pag.wait_for_timeout(300)
    ok(pag.locator("#m2-gens-nota").is_visible(),
       "con la capa encendida, el aviso está a la vista")
    txt = pag.inner_text("#m2-gens-nota")
    for trozo in ("ya visitado", "no sale nada", "estimación", "una vez al día"):
        ok(trozo in txt, "dice «%s»" % trozo)
    foto(pag, "5-aviso")

    pag.click("#m2cap-spawner")
    pag.wait_for_timeout(300)
    ok(not pag.locator("#m2-gens-nota").is_visible(), "apagada la capa, el aviso se va")
    pag.click("#m2cap-spawner")
    pag.wait_for_timeout(300)
    ok(pag.locator("#m2-gens-nota").is_visible(), "y vuelve al encenderla")

    # El aviso es para todos, no solo para quien manda: el botón de generar la
    # zona era de admin, pero saber qué enseña la capa lo necesita cualquiera.
    pf.rol = "viewer"
    pf.al_mapa(pag)
    ok(pag.locator("#m2-gens-nota").is_visible(), "y un observador también lo ve")
    pf.rol = "admin"

    ok(not pag.locator("#m2-zona").count(), "el botón de buscar en la zona ya no está")
    ok(not pag.evaluate("() => typeof m2Zona"). startswith("function"),
       "ni su código suelto por ahí")

    ok(not errores, "sin errores de JavaScript" + (": %s" % errores[:2] if errores else ""))

    # ── en el móvil, nada de esto ─────────────────────────────────────────
    # En una pantalla táctil el navegador deja el :hover pegado en lo último que
    # se tocó. Sin cortarlo, una capa apagada se quedaría iluminada como si
    # estuviera encendida, que es justo la señal que aquí importa.
    print("── en una pantalla táctil ──")
    ctx = nav.new_context(viewport={"width": 390, "height": 780},
                          has_touch=True, is_mobile=True,
                          user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                                     "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
    mov = ctx.new_page()
    mov.route("**/*", pf.enruta)
    pf.al_mapa(mov)
    ok(not mov.evaluate("() => matchMedia('(hover:hover)').matches"),
       "el navegador dice que aquí no se pasa por encima")
    if mov.evaluate("() => document.getElementById('m2cap-village').classList.contains('on')"):
        mov.click("#m2cap-village")
        mov.wait_for_timeout(250)
    mov.hover("#m2cap-village")
    mov.wait_for_timeout(350)
    ok(mov.evaluate(OPACIDAD, "m2cap-village") < 0.6,
       "así que una capa apagada NO se ilumina al tocarla (%.2f)"
       % mov.evaluate(OPACIDAD, "m2cap-village"))
    ok(not mov.evaluate(CARTEL)["visible"], "y no sale un cartel suelto por la pantalla")
    mov.click("#m2cap-village")
    mov.wait_for_timeout(300)
    ok(mov.evaluate(OPACIDAD, "m2cap-village") > 0.95, "pero al encenderla sí se ilumina")
    foto(mov, "4-movil")
    ctx.close()

    nav.close()

pf.para()
print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
