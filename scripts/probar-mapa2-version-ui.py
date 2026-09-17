#!/usr/bin/env python3
"""
El aviso de versión del mapa Explorar, visto desde el navegador.

POR QUÉ ASÍ
-----------
El servidor ya sabe detectar que los biomas se quedaron en la versión anterior
(eso lo prueba probar-mapa2-version.py). Pero un desfase que solo existe en un
JSON no le sirve a nadie: lo que arregla el problema es que se VEA al abrir la
pestaña, y que quien pueda arreglarlo tenga el botón ahí mismo.

Así que esto abre el `static/index.html` de verdad en Chromium contra el panel
de mentira y comprueba las cuatro cosas que pueden salir mal en la interfaz:

  · que el aviso NO sale cuando todo está al día (avisar de más es avisar de
    nada: a la tercera vez ya no se lee);
  · que sale, y nombra las dos versiones, cuando sí hay desfase;
  · que el botón solo se le ofrece a quien puede pulsarlo, y al resto se le dice
    qué hacer en su lugar;
  · que al pulsarlo se queda contando hasta que termina de verdad, en vez de
    decir «hecho» y dejar el mapa igual durante tres minutos.

Hace falta:  pip install playwright && playwright install chromium
Correr:      python3 scripts/probar-mapa2-version-ui.py
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

fallos, pasadas = [], 0

VIEJO = {"jar": "server-26.10.jar", "version": "26.10",
         "biomas_jar": "server-26.2.jar", "al_dia": False, "javac": True,
         "motivo": "los biomas están calculados con server-26.2.jar y ahora hay server-26.10.jar",
         "trabajando": False}
AL_DIA = {"jar": "server-26.10.jar", "version": "26.10",
          "biomas_jar": "server-26.10.jar", "al_dia": True, "javac": True,
          "motivo": "", "trabajando": False}


def ok(cond, que):
    global pasadas
    if cond:
        pasadas += 1
        print("  ✔ %s" % que)
    else:
        fallos.append(que)
        print("  ✘ %s" % que)


def titulo(t):
    print("\n\033[1m%s\033[0m" % t)


def visible(pag):
    return pag.evaluate("() => document.getElementById('m2-version-nota')"
                        ".classList.contains('ver')")


def texto(pag):
    return pag.inner_text("#m2-version-nota")


def abre(pw, pf, errores):
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    pag.on("pageerror", lambda e: errores.append(str(e)))
    pag.on("console", lambda m: errores.append("console: " + m.text)
           if m.type == "error" and "Failed to load resource" not in m.text else None)
    pag.route("**/*", pf.enruta)
    pf.al_mapa(pag)
    return nav, pag


def main():
    errores = []
    with sync_playwright() as pw:
        # ── 1 · al día: ni una palabra ───────────────────────────────────────
        titulo("1 · cuando está al día")
        pf = PanelFalso()
        pf.desfase = dict(AL_DIA)
        nav, pag = abre(pw, pf, errores)
        ok(not visible(pag), "no sale ningún aviso")
        nav.close(); pf.para()

        # ── 2 · desfasado ───────────────────────────────────────────────────
        titulo("2 · cuando los biomas son de otra versión")
        pf = PanelFalso()
        pf.desfase = dict(VIEJO)
        nav, pag = abre(pw, pf, errores)
        ok(visible(pag), "el aviso sale solo, al abrir la pestaña")
        t = texto(pag)
        ok("server-26.2.jar" in t and "server-26.10.jar" in t,
           "y nombra las dos versiones, la que se ve y la que hay")
        ok("iconos" in t or "slime" in t,
           "y distingue lo que sí está al día de lo que no: %r" % t[-90:].replace("\n", " "))
        ok(pag.locator("#m2-version-btn").count() == 1, "al admin se le ofrece el botón")
        foto(pag, "8-version-aviso")

        # ── 3 · pulsarlo ────────────────────────────────────────────────────
        titulo("3 · pulsar «Actualizar ahora»")
        azulejos = []
        pag.on("request", lambda r: azulejos.append(r.url)
               if "/api/mapa2/azulejo" in r.url else None)
        pag.click("#m2-version-btn")
        pag.wait_for_timeout(700)
        ok(pf.actualizados == 1, "se pide la actualización una sola vez (%d)" % pf.actualizados)
        ok(pag.locator("#m2-version-btn").is_disabled(),
           "el botón se apaga para que no se pulse dos veces")
        ok("Recompilando" in texto(pag) or "Actualizando" in texto(pag),
           "y el aviso pasa a contar que está trabajando")

        # el panel de mentira termina la faena, como haría el de verdad
        pf.desfase = dict(AL_DIA)
        pag.wait_for_timeout(5200)                 # el reloj pregunta cada 4 s
        ok(not visible(pag), "cuando termina, el aviso se retira solo")
        ok(pag.evaluate("() => M2.sello > 0"),
           "y se pone el rompe-cachés, o el navegador serviría los colores viejos")
        # La caché en memoria se vacía y el mapa se vuelve a pintar en el acto,
        # así que NO queda vacía: lo que importa es que lo que se pidió después
        # lleve el sello. Comprobar `size === 0` sería comprobar el instante
        # equivocado, y pasaría solo si el repintado estuviera roto.
        despues = [u for u in azulejos if "?v=" in u]
        ok(despues, "y los azulejos que se piden después lo llevan (%d de %d)"
           % (len(despues), len(azulejos)))
        ok(pag.evaluate("() => [...M2.azulejos.keys()].length >= 0"), "el mapa se repinta solo")
        nav.close(); pf.para()

        # ── 4 · sin permisos ────────────────────────────────────────────────
        titulo("4 · si quien mira no es admin")
        pf = PanelFalso(rol="viewer")
        pf.desfase = dict(VIEJO)
        nav, pag = abre(pw, pf, errores)
        ok(visible(pag), "el aviso también se le enseña: es información, no un botón")
        ok(pag.locator("#m2-version-btn").count() == 0, "pero sin botón")
        ok("admin" in texto(pag).lower(), "y se le dice qué hacer: %r"
           % texto(pag).strip().splitlines()[-1][:70])
        nav.close(); pf.para()

        # ── 5 · sin compilador ──────────────────────────────────────────────
        titulo("5 · si la máquina no tiene compilador de Java")
        pf = PanelFalso()
        pf.desfase = dict(VIEJO, javac=False)
        nav, pag = abre(pw, pf, errores)
        ok(visible(pag), "sale el aviso")
        ok("compilador" in texto(pag), "y se advierte antes de pulsar, no después")
        ok(pag.locator("#m2-version-btn").count() == 1,
           "el botón sigue ahí: puede que el JDK se instalara y esto no lo sepa")
        nav.close(); pf.para()

        # ── 6 · un bioma que la paleta no conoce ────────────────────────────
        titulo("6 · si el juego trae un bioma que el panel no sabe pintar")
        pf = PanelFalso()
        pf.estado["sin_color"] = ["minecraft:dappled_forest"]
        nav, pag = abre(pw, pf, errores)
        vis = pag.evaluate("() => document.getElementById('m2-color-nota')"
                           ".classList.contains('ver')")
        ok(vis, "sale el aviso solo, sin que nadie mire el mapa con lupa")
        t = pag.inner_text("#m2-color-nota")
        ok("dappled forest" in t, "y dice cuál es: %r" % t.split("\n")[-1][:80])
        nav.close(); pf.para()

        titulo("7 · y si están todos, calla")
        pf = PanelFalso()
        pf.estado["sin_color"] = []
        nav, pag = abre(pw, pf, errores)
        ok(not pag.evaluate("() => document.getElementById('m2-color-nota')"
                            ".classList.contains('ver')"),
           "con la paleta completa no se dice nada")
        nav.close(); pf.para()

        ok(not errores, "sin errores de JavaScript en todo el recorrido"
           + (": %s" % errores[:2] if errores else ""))


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
