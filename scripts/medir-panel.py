#!/usr/bin/env python3
"""
Cuánto cuesta usar el panel, en milisegundos y en un navegador de verdad.

POR QUÉ ASÍ
-----------
«Va fluido» y «va a tirones» son opiniones, y con opiniones se optimiza a ciegas:
se toca lo que llama la atención en vez de lo que cuesta. Esto mide.

No mide lo que tarda el servidor —para eso está la pestaña Sistema— sino lo que
tarda el NAVEGADOR en responder a lo que hace la gente: mover el ratón por el
mapa, cambiar de pestaña, encender una capa. Son las tres cosas que se hacen
cien veces al día y las tres que se notan cuando van mal.

El truco de medir el ratón: se disparan 200 `pointermove` seguidos y se cronometra
el bucle. Como los manejadores son síncronos, ese tiempo ES lo que el navegador
gasta en atenderlos, sin ruido de red ni de pintado.

Correr:  python3 scripts/medir-panel.py
         GUARDAR=/tmp/antes.json python3 scripts/medir-panel.py   (guarda la medida)
         COMPARAR=/tmp/antes.json python3 scripts/medir-panel.py  (dice cuánto cambió)
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_falso import PanelFalso                            # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright:  pip install playwright && playwright install chromium")
    sys.exit(2)

VUELTAS = 5          # cada medida se toma varias veces y se queda la mediana
MOVIMIENTOS = 200


def mediana(xs):
    xs = sorted(xs)
    return xs[len(xs) // 2]


RATON = """(n) => {
  const caja = document.getElementById('m2-lienzo');
  const r = caja.getBoundingClientRect();
  const t0 = performance.now();
  for (let i = 0; i < n; i++){
    caja.dispatchEvent(new PointerEvent('pointermove', {
      clientX: r.left + 40 + (i * 7) % Math.max(1, r.width - 80),
      clientY: r.top  + 40 + (i * 11) % Math.max(1, r.height - 80),
      pointerId: 1, bubbles: true}));
  }
  return performance.now() - t0;
}"""

CAPAS = """() => {
  const t0 = performance.now();
  for (let i = 0; i < 20; i++) m2Capas();
  return performance.now() - t0;
}"""

ICONOS = """() => {
  const t0 = performance.now();
  for (let i = 0; i < 20; i++) m2Iconos();
  return performance.now() - t0;
}"""

PESTANAS = """() => {
  const t0 = performance.now();
  for (let i = 0; i < 10; i++){ show('dash'); show('mapa2'); }
  return performance.now() - t0;
}"""


def medir(pag):
    fuera = {}
    fuera["raton"] = mediana([pag.evaluate(RATON, MOVIMIENTOS) for _ in range(VUELTAS)]) / MOVIMIENTOS
    fuera["capas"] = mediana([pag.evaluate(CAPAS) for _ in range(VUELTAS)]) / 20
    fuera["iconos"] = mediana([pag.evaluate(ICONOS) for _ in range(VUELTAS)]) / 20
    fuera["pestanas"] = mediana([pag.evaluate(PESTANAS) for _ in range(VUELTAS)]) / 20
    return fuera


NOMBRES = {
    "raton": "mover el ratón por el mapa (por movimiento)",
    "capas": "repintar la tira de capas (una vez)",
    "iconos": "repintar los iconos del mapa (una vez)",
    "pestanas": "cambiar de pestaña (una vez)",
}

pf = PanelFalso()
with sync_playwright() as pw:
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    pag.route("**/*", pf.enruta)
    pf.al_mapa(pag)
    pag.wait_for_timeout(800)

    # una vuelta en vacío para que el navegador termine de calentar
    pag.evaluate(RATON, 50)
    ahora = medir(pag)

    # cuántas peticiones y cuántos bytes pide la página al arrancar
    pedidos = []
    pag.on("request", lambda r: pedidos.append(r.url))
    # `load` no vale aquí: la página pide una fuente que en el banco de pruebas
    # no existe y el evento no llega nunca.
    pag.goto(pf.BASE, wait_until="domcontentloaded")
    pag.wait_for_timeout(2500)
    ahora["peticiones"] = len([u for u in pedidos if u.startswith(pf.BASE)])
    nav.close()
pf.para()

antes = None
if os.environ.get("COMPARAR"):
    try:
        antes = json.loads(Path(os.environ["COMPARAR"]).read_text())
    except Exception as e:
        print("no pude leer la medida anterior: %s" % e)

print()
for k, texto in NOMBRES.items():
    linea = "  %-44s %7.3f ms" % (texto, ahora[k])
    if antes and k in antes:
        d = ahora[k] - antes[k]
        pc = (d / antes[k] * 100) if antes[k] else 0
        linea += "   (antes %.3f, %+.0f%%)" % (antes[k], pc)
    print(linea)
linea = "  %-44s %7d" % ("peticiones al cargar", ahora["peticiones"])
if antes and "peticiones" in antes:
    linea += "   (antes %d)" % antes["peticiones"]
print(linea)
print()

if os.environ.get("GUARDAR"):
    Path(os.environ["GUARDAR"]).write_text(json.dumps(ahora, indent=1))
    print("medida guardada en %s" % os.environ["GUARDAR"])
