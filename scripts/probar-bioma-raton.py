#!/usr/bin/env python3
"""
Que el mapa diga bien qué bioma hay debajo del ratón.

POR QUÉ ASÍ
-----------
El nombre que sale al pasar el ratón no viene del servidor: se LEE EL COLOR del
píxel ya dibujado y se busca en la leyenda. Es exacto y no cuesta ni una
petición... mientras el color que hay en el lienzo sea exactamente uno de los de
la leyenda.

Si no lo encuentra, el código enseña «lo último que se supo» mientras le
pregunta al servidor. Eso está bien pensado para cuando el azulejo aún no ha
llegado, pero tiene un efecto feo: cuando la búsqueda falla de verdad, no se ve
un hueco — se ve **el nombre del bioma anterior**. O sea que un fallo de
búsqueda se disfraza de dato correcto, y solo se nota si te fijas en que el
nombre no cambia donde debería.

Las otras pruebas de la pestaña servían un azulejo de 1x1, así que esta parte
nunca se probó. Aquí el panel de mentira sirve azulejos de 256x256 con dos
biomas de colores conocidos, y se comprueba que el nombre que sale es el del
sitio donde está el ratón.

Hace falta:  pip install playwright && playwright install chromium
Correr:      python3 scripts/probar-bioma-raton.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_falso import PanelFalso                          # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright:  pip install playwright && playwright install chromium")
    sys.exit(2)

fallos, pasadas = [], 0


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


def main():
    errores = []
    pf = PanelFalso()
    with sync_playwright() as pw:
        nav = pw.chromium.launch(args=["--no-proxy-server"])
        # DPR 2 emula una pantalla Retina como la del Mac de Juan: el lienzo
        # tiene el doble de píxeles que de puntos CSS, y la lectura del color
        # bajo el ratón tiene que seguir cuadrando.
        dpr = float(sys.argv[1]) if len(sys.argv) > 1 else 1
        print("(pantalla con dpr=%g)" % dpr)
        pag = nav.new_page(viewport={"width": 1280, "height": 900},
                           device_scale_factor=dpr)
        pag.on("pageerror", lambda e: errores.append(str(e)))
        pag.route("**/*", pf.enruta)
        pf.al_mapa(pag)
        pag.wait_for_timeout(1500)               # que lleguen los azulejos

        titulo("1 · la leyenda llega al navegador")
        n = pag.evaluate("() => M2.porColor.size")
        ok(n >= 2, "el mapa conoce %d colores de bioma" % n)
        ok(pag.evaluate("() => !!M2.porColor.get(0xa55f16)"),
           "incluido el ámbar del bosque moteado (0xa55f16)")
        ok(pag.evaluate("() => M2.porColor.get(0xa55f16).es") == "Bosque moteado",
           "con su nombre en español")

        titulo("2 · lo que hay bajo el ratón")
        caja = pag.locator("#m2-lienzo").bounding_box()

        def sondea(fx, fy):
            """Mueve el ratón ahí y devuelve (color que hay, nombre que dice).

            El color se lee EXACTAMENTE igual que lo lee el panel, para que la
            prueba compruebe el invariante de verdad —«el nombre que sale es el
            del color que hay debajo»— y no una suposición mía sobre dónde caen
            las franjas del azulejo.
            """
            pag.mouse.move(caja["x"] + caja["width"] * fx,
                           caja["y"] + caja["height"] * fy)
            pag.wait_for_timeout(240)
            col = pag.evaluate("""([fx, fy]) => {
                const c = document.getElementById('m2-bio');
                const r = c.getBoundingClientRect();
                const g = c.getContext('2d', {willReadFrequently: true});
                const d = g.getImageData(Math.round(r.width * fx * M2.dpr),
                                         Math.round(r.height * fy * M2.dpr), 1, 1).data;
                return [d[0], d[1], d[2]];
            }""", [fx, fy])
            return tuple(col), pag.inner_text("#m2-donde").replace("\n", " ")

        ley = pag.evaluate("() => [...M2.porColor.entries()].map(([k, b]) => [k, b.es])")
        por_color = {k: n for k, n in ley}

        malos, mezclas, vistos = [], [], set()
        for fx in [i / 20 for i in range(1, 20)]:
            for fy in (0.3, 0.7):
                col, dicho = sondea(fx, fy)
                clave = (col[0] << 16) | (col[1] << 8) | col[2]
                esperado = por_color.get(clave)
                if esperado is None:
                    mezclas.append((col, dicho))
                    continue
                vistos.add(esperado)
                if esperado not in dicho:
                    malos.append((col, esperado, dicho))

        ok(not mezclas,
           "todo píxel del mapa es un color EXACTO de la leyenda (%d mezclas%s)"
           % (len(mezclas), "" if not mezclas else ": %s" % mezclas[:3]))
        ok(len(vistos) >= 2,
           "se han pisado los dos biomas del azulejo (%s)" % ", ".join(sorted(vistos)))
        ok(not malos,
           "y el nombre que sale es siempre el del color que hay debajo%s"
           % ("" if not malos else " — %d fallos: %s" % (len(malos), malos[:3])))

        titulo("3 · colores desviados por la pantalla")
        # En una pantalla de gama amplia el navegador convierte de espacio de
        # color y algunos valores vuelven con ±1 en algún canal. Buscando el
        # color EXACTO esos biomas no se encontraban nunca y el panel enseñaba
        # el nombre del anterior: parecía que se equivocaba, iba un paso atrás.
        def cual(r, g, b):
            return pag.evaluate("([r,g,b]) => { const x = m2BiomaDeColor(r,g,b);"
                                " return x ? x.es : null; }", [r, g, b])

        ok(cual(0xa5, 0x5f, 0x16) == "Bosque moteado", "el color exacto, claro")
        ok(cual(0xa4, 0x5e, 0x16) == "Bosque moteado",
           "y el mismo desviado −1 en dos canales (el caso real de tu Mac)")
        ok(cual(0x8c, 0xb3, 0x61) == "Llanura",
           "la llanura desviada también (#8cb361 en vez de #8db360)")
        ok(cual(0xa6, 0x60, 0x17) == "Bosque moteado", "y desviado hacia arriba")

        # El caso peor de toda la paleta: dos colores a distancia 5,74.
        ok(cual(0x60, 0x60, 0x60) == "Colinas ventosas", "las colinas ventosas, exactas")
        ok(cual(0x64, 0x5f, 0x64) == "Deltas de basalto", "los deltas de basalto, exactos")
        ok(cual(0x5f, 0x61, 0x61) == "Colinas ventosas",
           "y las colinas desviadas NO se confunden con los deltas, que es el par "
           "más peligroso que hay")

        ok(cual(0x80, 0x00, 0x80) is None,
           "un color que no se parece a nada no se fuerza a ningún bioma")
        ok(cual(0x00, 0x00, 0x00) is None, "ni el fondo del lienzo")

        titulo("4 · el X y la Z también")
        t = sondea(0.5, 0.5)[1]
        ok("X " in t and "Z " in t, "salen las coordenadas: %r" % t)

        titulo("5 · donde todavía no hay azulejo")
        # Con el lienzo vacío no hay color que buscar. Ahí SÍ vale enseñar lo
        # último que se supo mientras se le pregunta al servidor: lo que no vale
        # es que eso tape un fallo de búsqueda, que es lo que se comprueba arriba.
        pag.evaluate("() => { M2.azulejos.clear(); m2Dibuja(); }")
        t = sondea(0.5, 0.35)[1]
        ok("X " in t, "se siguen dando las coordenadas aunque no haya dibujo: %r" % t)

        ok(not errores, "sin errores de JavaScript" + (": %s" % errores[:2] if errores else ""))
        nav.close()
    pf.para()


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
