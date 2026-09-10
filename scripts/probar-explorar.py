#!/usr/bin/env python3
"""
La pestaña Explorar en un navegador de verdad: barra de capas, tesoros y minas,
y el tic de «ya fui aquí».

POR QUÉ ASÍ
-----------
Nada de esto se puede comprobar llamando funciones: lo que se rompe es el
injerto —el CSS que no gana, la tira que se desdobla en dos líneas, la capa que
se enciende y no pide nada al servidor— y eso solo se ve cargando el index.html
que se va a desplegar contra el panel corriendo.

Hace falta:
    pip install playwright && playwright install chromium
    el panel escuchando (por defecto http://127.0.0.1:8099)
    el lector de biomas encendido (si no, la pestaña sale apagada y esto no vale)

Correr:
    python3 scripts/probar-explorar.py
    PANEL_URL=http://localhost:8099 PANEL_USER=juan PANEL_PASS=... python3 scripts/probar-explorar.py
"""
import os
import sys, json
from playwright.sync_api import sync_playwright
BASE=os.environ.get("PANEL_URL","http://127.0.0.1:8099")
USUARIO=os.environ.get("PANEL_USER","juan")
CLAVE=os.environ.get("PANEL_PASS","prueba1234")
F=[]
def ok(c,q):
    print(("  ✔ " if c else "  ✘ ")+q)
    if not c: F.append(q)

with sync_playwright() as pw:
    nav=pw.chromium.launch(args=["--no-proxy-server"])
    pag=nav.new_page(viewport={"width":1280,"height":900})
    err=[]
    pag.on("pageerror", lambda e: err.append(str(e)))
    RUIDO=("Failed to load resource","frame-ancestors")
    pag.on("console", lambda m: err.append(m.text)
           if m.type=="error" and not any(r in m.text for r in RUIDO) else None)
    pag.goto(BASE, wait_until="domcontentloaded"); pag.wait_for_timeout(1200)
    pag.evaluate("() => showLogin && showLogin()")
    pag.wait_for_selector("#li-user", state="visible", timeout=8000)
    pag.fill("#li-user",USUARIO); pag.fill("#li-pass",CLAVE); pag.click("#li-btn")
    pag.wait_for_selector("#tabs button", timeout=15000)
    ok(True,"sesión iniciada")

    pag.click("#tabbtn-mapa2"); pag.wait_for_timeout(1500)
    ok(not pag.locator("#m2-caja").is_hidden(), "la caja del mapa está visible")
    pag.wait_for_function("() => M2.listo && M2.est", timeout=90000)

    print("── la barra de capas ──")
    ok("tira" in pag.get_attribute("#m2-capas","class"), "arranca en modo tira")
    caja = pag.evaluate("""() => { const w=document.getElementById('m2-capas');
        return {ancho:w.clientWidth, scroll:w.scrollWidth,
                alto:w.getBoundingClientRect().height,
                n:w.querySelectorAll('.m2-cap').length,
                icono:(w.querySelector('.m2-cap img')||{}).clientWidth||0,
                sobra:w.scrollWidth-w.clientWidth}; }""")
    print("    ", caja)
    ok(caja["n"] >= 10, "hay capas: %d" % caja["n"])
    ok(caja["icono"] >= 28, "los iconos son grandes (%d px)" % caja["icono"])
    ok(caja["alto"] < 80, "la tira cabe en una línea (%d px de alto)" % caja["alto"])
    # En una ventana ancha caben todas y no hay nada que deslizar (correcto).
    # Lo que hay que probar es que en una estrecha SÍ se puede.
    pag.set_viewport_size({"width":560,"height":900}); pag.wait_for_timeout(500)
    est = pag.evaluate("""() => { const w=document.getElementById('m2-capas');
        return {sobra:w.scrollWidth-w.clientWidth, alto:w.getBoundingClientRect().height}; }""")
    ok(est["sobra"] > 0, "en ventana estrecha hay más a la derecha (%d px)" % est["sobra"])
    ok(est["alto"] < 80, "y sigue siendo UNA línea (%d px)" % est["alto"])
    pag.evaluate("() => document.getElementById('m2-capas').scrollLeft = 250")
    pag.wait_for_timeout(900)
    ok(pag.evaluate("() => document.getElementById('m2-capas').scrollLeft") > 100, "se puede deslizar")
    pag.set_viewport_size({"width":1280,"height":900}); pag.wait_for_timeout(400)

    print("── el botón de la derecha ──")
    ok(pag.locator("#m2-modo").count()==1, "está el botón")
    ok(pag.locator("#m2-modo").inner_text().strip()=="▾", "en tira enseña ▾")
    pag.click("#m2-modo"); pag.wait_for_timeout(400)
    ok("lista" in pag.get_attribute("#m2-capas","class"), "pasa a icono + texto")
    ok(pag.locator("#m2-modo").inner_text().strip()=="✓", "y enseña la palomita")
    txt = pag.evaluate("""() => { const e=document.querySelector('#m2-capas .m2-cap .et');
        return e ? getComputedStyle(e).display : 'no hay'; }""")
    ok(txt!="none", "en lista se ven los nombres (display %s)" % txt)
    pag.reload(wait_until="domcontentloaded"); pag.wait_for_timeout(2000)
    pag.click("#tabbtn-mapa2")
    pag.wait_for_function("() => M2.listo", timeout=90000)
    ok("lista" in pag.get_attribute("#m2-capas","class"), "recuerda el modo tras recargar")
    pag.click("#m2-modo"); pag.wait_for_timeout(300)

    print("── tesoros y minas ──")
    tipos = pag.evaluate("() => M2.est.tipos.map(t=>[t.k,!!t.densa])")
    print("    ", tipos)
    ok(any(k=="buried_treasure" for k,_ in tipos), "sale la capa de tesoros")
    ok(any(k=="mineshaft" for k,_ in tipos), "sale la capa de minas")
    ok(all(d for k,d in tipos if k in ("buried_treasure","mineshaft")), "van marcadas como densas")
    ok(all(not d for k,d in tipos if k in ("village","mansion")), "las de rejilla no")

    # encenderlas de cerca y ver que llegan
    pag.evaluate("() => m2Volar(0,0,0.5)")
    pag.wait_for_timeout(500)
    pag.evaluate("""() => { for (const k of ['buried_treasure','mineshaft']){
        M2.apagados.delete(k); M2.forzados.add(k); } m2Capas(); m2PideEstructuras(); }""")
    pag.wait_for_function("() => M2.estructuras.some(s=>s[0]==='buried_treasure')", timeout=120000)
    n = pag.evaluate("() => ({tesoros:M2.cuenta.buried_treasure||0, minas:M2.cuenta.mineshaft||0})")
    print("    ", n)
    ok(n["tesoros"]>0, "llegan tesoros: %d" % n["tesoros"])
    ok(n["minas"]>0, "llegan minas: %d" % n["minas"])

    print("── «ya fui aquí» ──")
    uno = pag.evaluate("() => { const s=M2.estructuras.find(s=>s[0]==='village')||M2.estructuras[0]; return s; }")
    print("    marcando", uno)
    pag.evaluate("([k,x,z]) => m2Marcar(k,x,z,true)", uno)
    pag.wait_for_timeout(900)
    ok(pag.evaluate("([k,x,z]) => !!M2.hechas[m2Clave(k,x,z)]", uno), "queda marcada en el cliente")
    guardadas = pag.evaluate("async () => (await (await fetch('/api/mapa2/hechas')).json()).hechas")
    ok(len(guardadas)==1, "el servidor la guardó: %s" % guardadas)
    ok(list(guardadas.values())[0]["por"]=="juan", "y con el nombre de quien la puso")
    # recargar: tiene que seguir
    pag.reload(wait_until="domcontentloaded"); pag.wait_for_timeout(2000)
    pag.click("#tabbtn-mapa2")
    pag.wait_for_function("() => M2.listo && Object.keys(M2.hechas).length>0", timeout=90000)
    ok(True, "sigue marcada tras recargar")
    # desmarcar
    pag.evaluate("([k,x,z]) => m2Marcar(k,x,z,false)", uno)
    pag.wait_for_timeout(900)
    vac = pag.evaluate("async () => (await (await fetch('/api/mapa2/hechas')).json()).hechas")
    ok(not vac, "se puede desmarcar: %s" % vac)


    print("── variantes: cada aldea con el dibujo de su bioma ──")
    v = pag.evaluate("() => Object.keys(M2.variantes||{})")
    print("     variantes con icono propio:", v)
    if not v:
        print("  — saltada: todavía no hay ningún icono de variante en static/markers/")
    else:
        # la lista de estructuras trae la variante en el cuarto hueco
        muestra = pag.evaluate("""() => (M2.estructuras||[]).filter(s=>s[3] && s[3]!==s[0]).slice(0,4)""")
        print("     ejemplos:", muestra)
        ok(bool(muestra), "las estructuras llegan con su variante")
        usa = pag.evaluate("""() => { const s=(M2.estructuras||[]).find(s=>M2.variantes[s[3]]);
            if (!s) return null;
            return {sub:s[3], mismo: M2.iconos.get(s[3]) === M2.iconos.get(s[0])}; }""")
        if usa:
            ok(not usa["mismo"], "y dibujan con SU icono, no el de la capa (%s)" % usa["sub"])
        nombre = pag.evaluate("""() => { const s=(M2.estructuras||[]).find(s=>M2.variantes[s[3]]);
            if (!s) return null; m2Globo(s[0],s[1],s[2],s[3]);
            return document.querySelector('#m2-globo b').textContent; }""")
        if nombre:
            ok(nombre not in ("Aldea","Generadores"), "el globo dice la variante: %r" % nombre)

    print("── generadores del mundo explorado ──")
    hay = pag.evaluate("() => (M2.est.tipos||[]).some(t=>t.delMundo)")
    if not hay:
        print("  — saltada: falta static/markers/spawner.png o data/spawners.json")
    else:
        pag.evaluate("""() => { M2.apagados.delete('spawner'); M2.forzados.add('spawner');
            m2Volar(0,0,1); m2Capas(); m2PideEstructuras(); }""")
        pag.wait_for_function("() => M2.pidiendo===0", timeout=60000)
        pag.wait_for_timeout(1500)
        g = pag.evaluate("() => (M2.estructuras||[]).filter(s=>s[0]==='spawner')")
        print("     generadores a la vista:", g)
        ok(bool(g), "llegan generadores")
        ok(all(s[3].startswith('spawner_') for s in g), "cada uno dice qué bicho es")



    print("── chunks de slime ──")
    pag.evaluate("() => { M2.verSlime=false; m2Volar(0,0,1/8); }")
    pag.wait_for_timeout(600)
    antes = pag.evaluate("() => M2.esc")
    pag.evaluate("() => { const b=document.getElementById('m2cap-slime'); b.click(); }")
    pag.wait_for_function("() => M2.slime && M2.slime.datos", timeout=60000)
    pag.wait_for_timeout(1200)
    ok(pag.evaluate("() => M2.verSlime"), "el botón enciende la capa")
    esc = pag.evaluate("() => M2.esc")
    ok(esc > antes, "y acerca el mapa (%s → %s)" % (antes, esc))
    lado = 16 * esc
    ok(lado >= 8, "cada chunk mide %.0f px, que se ve" % lado)
    s = pag.evaluate("() => ({n:M2.slime.n, unos:Array.from(M2.slime.datos).filter(Boolean).length})")
    ok(s["unos"] > 0, "llegan chunks de slime: %d de %d" % (s["unos"], s["n"]*s["n"]))
    # ¿se ven de verdad? se cuentan píxeles verdes Y píxeles de la retícula oscura
    pinta = pag.evaluate("""() => { const c=document.getElementById('m2-ico');
      const g=c.getContext('2d',{willReadFrequently:true});
      const d=g.getImageData(0,0,c.width,c.height).data;
      let verde=0, borde=0;
      for (let i=0;i<d.length;i+=4){
        if (d[i+3]<10) continue;
        if (d[i+1]>d[i]+25 && d[i+1]>d[i+2]+25) verde++;
        if (d[i]<45 && d[i+1]<60 && d[i+2]<45) borde++;
      }
      return {verde, borde}; }""")
    print("     ", pinta)
    ok(pinta["verde"] > 3000, "hay relleno verde (%d px)" % pinta["verde"])
    ok(pinta["borde"] > 500, "y la retícula oscura que los separa (%d px)" % pinta["borde"])
    # apagarlos otra vez
    pag.evaluate("() => { document.getElementById('m2cap-slime').click(); }")
    pag.wait_for_timeout(700)
    ok(not pag.evaluate("() => M2.verSlime"), "se pueden apagar")

    print("── la barra de arriba se va al bajar (solo en Explorar) ──")
    pag.set_viewport_size({"width":1280,"height":700}); pag.wait_for_timeout(400)
    arriba = pag.evaluate("""() => { window.scrollTo(0,0); return 1; }""")
    pag.wait_for_timeout(700)
    ok(not pag.evaluate("() => document.getElementById('topbar').classList.contains('fuera')"),
       "arriba del todo la barra está")
    pag.evaluate("() => window.scrollTo(0, 400)")
    pag.wait_for_timeout(800)
    ok(pag.evaluate("() => document.getElementById('topbar').classList.contains('fuera')"),
       "al bajar se va")
    # esperar a que la ANIMACIÓN termine, no un tiempo fijo: con el mapa
    # recolocándose el cronómetro fallaba solo a veces, que es lo peor
    try:
        pag.wait_for_function(
            "() => document.getElementById('topbar').getBoundingClientRect().bottom <= 2",
            timeout=4000)
        fuera_ok = True
    except Exception:
        fuera_ok = False
    abajo = pag.evaluate("() => Math.round(document.getElementById('topbar').getBoundingClientRect().bottom)")
    ok(fuera_ok, "y de verdad sale de la pantalla (borde inferior %s)" % abajo)
    # a media altura NO vuelve: solo arriba del todo
    pag.evaluate("() => window.scrollTo(0, 180)")
    pag.wait_for_timeout(700)
    ok(pag.evaluate("() => document.getElementById('topbar').classList.contains('fuera')"),
       "subiendo a medias sigue escondida")
    pag.evaluate("() => window.scrollTo(0, 0)")
    pag.wait_for_timeout(800)
    ok(not pag.evaluate("() => document.getElementById('topbar').classList.contains('fuera')"),
       "y vuelve al llegar arriba del todo")
    ok(pag.evaluate("() => getComputedStyle(document.getElementById('topbar')).transitionProperty.includes('transform')"),
       "la vuelta va con animación")
    # en otra pestaña se comporta como siempre
    pag.click("#tabbtn-players"); pag.wait_for_timeout(400)
    pag.evaluate("() => window.scrollTo(0, 400)")
    pag.wait_for_timeout(800)
    ok(not pag.evaluate("() => document.getElementById('topbar').classList.contains('fuera')"),
       "en otras pestañas NO se esconde")
    pag.click("#tabbtn-mapa2"); pag.wait_for_timeout(600)
    pag.evaluate("() => window.scrollTo(0, 0)")
    pag.wait_for_timeout(500)

    print("── sin errores de JavaScript ──")
    ok(not err, "consola limpia: %r" % err[:3])
    nav.close()
print()
print("todo bien" if not F else "%d FALLO(S): %s" % (len(F), F))
sys.exit(1 if F else 0)
