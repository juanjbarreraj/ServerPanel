#!/usr/bin/env python3
"""
Que el panel conteste al ratón, y que se esté quieto cuando toca.

POR QUÉ ASÍ
-----------
Las micro-animaciones son de las pocas cosas que no se pueden revisar leyendo el
código: o el navegador las aplica o no, y basta una regla más específica en otro
sitio de la hoja para que una transición no llegue nunca. Aquí se miran los
estilos YA CALCULADOS por el navegador y los píxeles del lienzo, no el CSS.

Y hay tres cosas que se comprueban porque son las que se rompen en silencio:

  · Que con «reducir movimiento» puesto en el sistema no quede ni una animación.
    Hay gente a la que el movimiento en pantalla le sienta mal de verdad.
  · Que el icono de debajo del ratón crezca, que es lo que convierte el mapa en
    algo que responde en vez de un dibujo.
  · Que los relojes que preguntan al servidor se paren con la pestaña al fondo.
    Eso no se ve nunca mirando la pantalla, y es batería del móvil y CPU del
    servidor tirados a la basura.

Hace falta:  pip install playwright && playwright install chromium
Correr:      python3 scripts/probar-movimiento-ui.py
             FOTOS=/tmp/fotos python3 scripts/probar-movimiento-ui.py
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


# píxeles pintados en el lienzo de los iconos: sube si algo crece
TINTA = """() => {
  const c=document.getElementById('m2-ico');
  const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;
  let n=0;
  for (let i=3;i<d.length;i+=4) if (d[i]>16) n++;
  return n; }"""

DURACION = """(sel) => {
  const e=document.querySelector(sel); if(!e) return -1;
  const v=getComputedStyle(e).transitionDuration || '0s';
  return Math.max(...v.split(',').map(x=>parseFloat(x)*(x.includes('ms')?0.001:1)||0)); }"""

pf = PanelFalso()
# un generador en un sitio conocido, para poder ponerle el ratón encima
pf.gens = [["spawner", 96, 96, "spawner_zombie"]]

with sync_playwright() as pw:
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    errores = []
    pag.on("pageerror", lambda e: errores.append(str(e)))
    pag.on("console", lambda m: errores.append("console: " + m.text)
           if m.type == "error" and "Failed to load resource" not in m.text else None)
    peticiones = []
    pag.on("request", lambda r: peticiones.append(r.url))
    pag.route("**/*", pf.enruta)

    # ── las variables del movimiento ──────────────────────────────────────
    print("── el sistema de movimiento ──")
    pf.al_mapa(pag)
    tokens = pag.evaluate("""() => { const c=getComputedStyle(document.documentElement);
        return ['--t-rapido','--t-medio','--t-lento','--curva']
               .map(k=>c.getPropertyValue(k).trim()); }""")
    ok(all(tokens), "las cuatro variables están puestas: %s" % ", ".join(tokens))
    ok(all(float(t.replace("s", "")) <= 0.4 for t in tokens[:3]),
       "y ninguna dura más de 400 ms")

    # Una fila de tabla de mentira, puesta en la página: así se mide la regla tal
    # y como la aplica el navegador, no como está escrita en la hoja.
    pag.evaluate("""() => { const t=document.createElement('table');
        t.id='prueba-fila'; t.innerHTML='<tbody><tr><td>x</td></tr></tbody>';
        document.body.appendChild(t); }""")
    for sel, que in ((".tile", "las tarjetas del inicio"),
                     (".m2-cap", "las capas del mapa"),
                     (".m2-zoom button", "los botones de acercar"),
                     ("#prueba-fila tbody tr", "las filas de las tablas"),
                     (".btn", "los botones")):
        d = pag.evaluate(DURACION, sel)
        ok(d > 0, "%s tienen transición (%.2f s)" % (que, d))
    pag.evaluate("() => document.getElementById('prueba-fila').remove()")

    # ── el ratón sobre el mapa ────────────────────────────────────────────
    print("── el icono de debajo del ratón ──")
    pag.evaluate("() => m2Volar(96, 96, 1/2)")
    pag.wait_for_timeout(600)
    pag.evaluate("""() => { M2.apagados.delete('spawner'); M2.forzados.add('spawner');
                            m2Capas(); m2PideEstructuras(); }""")
    pag.wait_for_timeout(800)
    sitio = pag.evaluate("""() => { const e=(M2.enPantalla||[]).find(p=>p[0]==='spawner');
        if (!e) return null;
        const r=document.getElementById('m2-lienzo').getBoundingClientRect();
        return {x:r.left+e[3], y:r.top+e[4]}; }""")
    ok(bool(sitio), "hay un generador dibujado donde ponerse encima")
    if sitio:
        pag.mouse.move(sitio["x"] + 200, sitio["y"] + 200)
        pag.wait_for_timeout(300)
        antes = pag.evaluate(TINTA)
        cursor_lejos = pag.evaluate("() => getComputedStyle(document.getElementById('m2-lienzo')).cursor")

        pag.mouse.move(sitio["x"], sitio["y"])
        pag.wait_for_timeout(400)
        despues = pag.evaluate(TINTA)
        ok(pag.evaluate("() => !!M2.encima"), "el mapa sabe qué icono tiene debajo")
        ok(despues > antes, "y el icono crece de verdad (%d → %d píxeles)" % (antes, despues))
        cursor_encima = pag.evaluate("() => getComputedStyle(document.getElementById('m2-lienzo')).cursor")
        ok(cursor_encima == "pointer" and cursor_lejos != "pointer",
           "el puntero avisa de que se puede tocar (%s → %s)" % (cursor_lejos, cursor_encima))
        foto(pag, "6-icono-encima")

        pag.mouse.move(sitio["x"] + 200, sitio["y"] + 200)
        pag.wait_for_timeout(400)
        ok(pag.evaluate(TINTA) == antes, "y vuelve a su tamaño al apartarse")
        ok(not pag.evaluate("() => M2.encima"), "sin quedarse marcado")

        # Sacar el ratón del mapa no manda ningún «me he movido»: sin cuidarlo,
        # el último icono se queda crecido para siempre.
        pag.mouse.move(sitio["x"], sitio["y"])
        pag.wait_for_timeout(350)
        ok(pag.evaluate("() => !!M2.encima"), "(vuelvo a ponerme encima)")
        pag.mouse.move(20, 20)                      # fuera del mapa, en la cabecera
        pag.wait_for_timeout(350)
        ok(not pag.evaluate("() => M2.encima"),
           "y al salirse del mapa entero, tampoco se queda crecido")
        ok(pag.evaluate(TINTA) == antes, "el lienzo vuelve a como estaba")

    # ── los relojes, con la pestaña al fondo ──────────────────────────────
    print("── con la pestaña del navegador al fondo ──")
    pag.evaluate("""() => { Object.defineProperty(document, 'hidden',
                       {value:true, configurable:true});
                     document.dispatchEvent(new Event('visibilitychange')); }""")
    peticiones.clear()
    pag.evaluate("() => refreshStatus()")
    pag.wait_for_timeout(600)
    ok(not [u for u in peticiones if "/api/status" in u],
       "no se le pregunta al servidor (%d peticiones)"
       % len([u for u in peticiones if "/api/status" in u]))

    pag.evaluate("""() => { Object.defineProperty(document, 'hidden',
                       {value:false, configurable:true});
                     document.dispatchEvent(new Event('visibilitychange')); }""")
    pag.wait_for_timeout(700)
    ok([u for u in peticiones if "/api/status" in u],
       "y al volver se refresca en el acto, sin esperar al siguiente turno")

    # ── quien pide no moverse ─────────────────────────────────────────────
    print("── con «reducir movimiento» puesto ──")
    pag.emulate_media(reduced_motion="reduce")
    pag.wait_for_timeout(300)
    quietos = pag.evaluate("""() => {
        const malos=[];
        for (const e of document.querySelectorAll('.tile,.m2-cap,.btn,.tabslot,.card,section')){
          const c=getComputedStyle(e);
          const t=Math.max(...(c.transitionDuration||'0s').split(',')
                    .map(x=>parseFloat(x)*(x.includes('ms')?0.001:1)||0));
          const a=Math.max(...(c.animationDuration||'0s').split(',')
                    .map(x=>parseFloat(x)*(x.includes('ms')?0.001:1)||0));
          if (t>0.05 || a>0.05) malos.push(e.className+' t='+t+' a='+a);
        }
        return malos.slice(0,4); }""")
    ok(not quietos, "no queda ni una animación en pie" + (": %s" % quietos if quietos else ""))
    pag.emulate_media(reduced_motion="no-preference")

    ok(not errores, "sin errores de JavaScript" + (": %s" % errores[:2] if errores else ""))
    nav.close()

pf.para()
print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
