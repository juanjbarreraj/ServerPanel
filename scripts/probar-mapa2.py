#!/usr/bin/env python3
"""
Abre el panel en un navegador de verdad y comprueba el mapa del mundo entero.

POR QUÉ ASÍ
-----------
Las otras pruebas del panel montan un servidor de mentira y llaman a las
funciones. Esto no vale para un mapa: lo que puede romperse es el injerto —el
CSS que choca con otro, el trozo de JavaScript que se cuela en la función de al
lado, el lienzo que se queda de 0 píxeles— y nada de eso se ve llamando
funciones. Así que carga el index.html DE VERDAD contra el panel corriendo y
mira los píxeles que salen.

Hace falta:
    pip install playwright && playwright install chromium
    y el panel escuchando (por defecto en http://127.0.0.1:8099)

Correr:
    python3 scripts/probar-mapa2.py
    PANEL_URL=http://localhost:5000 PANEL_USER=juan PANEL_PASS=... python3 scripts/probar-mapa2.py
"""
import os
import sys

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright:  pip install playwright && playwright install chromium")
    sys.exit(2)

BASE = os.environ.get("PANEL_URL", "http://127.0.0.1:8099")
USUARIO = os.environ.get("PANEL_USER", "juan")
CLAVE = os.environ.get("PANEL_PASS", "prueba1234")
FALLOS = []


def afirmar(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


with sync_playwright() as pw:
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    errores = []
    pag.on("pageerror", lambda e: errores.append(str(e)))
    def _consola(m):
        if m.type == "error" and "Failed to load resource" not in m.text:
            errores.append("console.error: " + m.text)
    pag.on("console", _consola)

    print("── entrar ──")
    pag.goto(BASE, wait_until="domcontentloaded")
    pag.wait_for_timeout(1200)
    pag.evaluate("() => showLogin && showLogin()")
    pag.wait_for_selector("#li-user", state="visible", timeout=8000)
    pag.fill("#li-user", USUARIO)
    pag.fill("#li-pass", CLAVE)
    pag.click("#li-btn")
    pag.wait_for_selector("#tabs button", timeout=15000)
    afirmar(True, "sesión iniciada")

    print("── la pestaña ──")
    afirmar(pag.locator("#tabbtn-mapa2").count() == 1, "sale la pestaña Explorar")
    pag.click("#tabbtn-mapa2")
    pag.wait_for_timeout(700)
    afirmar(not pag.locator("#m2-caja").is_hidden(), "la caja del mapa está visible")
    afirmar(pag.locator("#m2-apagado").is_hidden(), "no sale el aviso de apagado")

    print("── que se dibuje ──")
    pag.wait_for_function("() => M2.listo && M2.pidiendo===0 && M2.azulejos.size>8", timeout=60000)
    pag.wait_for_timeout(600)
    pintado = pag.evaluate("""() => {
      const c = document.getElementById('m2-bio');
      const g = c.getContext('2d', {willReadFrequently:true});
      const d = g.getImageData(0,0,c.width,c.height).data;
      const vistos = new Set(); let noFondo = 0;
      for (let i=0;i<d.length;i+=4*97){
        const k=(d[i]<<16)|(d[i+1]<<8)|d[i+2];
        vistos.add(k);
        if (k !== 0x04060a) noFondo++;
      }
      return {colores: vistos.size, noFondo, total: Math.floor(d.length/(4*97)),
              w:c.width, h:c.height};
    }""")
    print("     ", pintado)
    afirmar(pintado["w"] > 100 and pintado["h"] > 100, "el lienzo tiene tamaño")
    afirmar(pintado["colores"] > 8, "hay muchos colores de bioma (%d)" % pintado["colores"])
    afirmar(pintado["noFondo"] > pintado["total"] * 0.9, "el mapa cubre la pantalla")

    print("── los iconos ──")
    pag.wait_for_function("() => M2.estructuras.length > 0", timeout=60000)
    pag.wait_for_timeout(400)
    iconos = pag.evaluate("() => ({n: (M2.enPantalla||[]).length, "
                          "estr: M2.estructuras.length, capas: document.querySelectorAll('.m2-cap').length})")
    print("     ", iconos)
    afirmar(iconos["estr"] > 0, "llegaron estructuras (%d)" % iconos["estr"])
    afirmar(iconos["capas"] > 0, "hay casillas de capas (%d)" % iconos["capas"])
    pintado_ico = pag.evaluate("""() => {
      const c=document.getElementById('m2-ico');
      const d=c.getContext('2d',{willReadFrequently:true}).getImageData(0,0,c.width,c.height).data;
      let n=0; for (let i=3;i<d.length;i+=4*13) if (d[i]>10) n++;
      return n;
    }""")
    afirmar(pintado_ico > 20, "los iconos están dibujados (%d muestras opacas)" % pintado_ico)

    print("── decir el bioma sin pedir nada ──")
    pag.mouse.move(640, 450)
    pag.wait_for_timeout(300)
    donde = pag.text_content("#m2-donde")
    print("      «%s»" % donde)
    afirmar("X " in donde and "Z " in donde, "salen las coordenadas")
    afirmar(len(donde.split("·")) > 1, "sale el nombre del bioma")

    print("── apagar una capa ──")
    antes = pag.evaluate("() => (M2.enPantalla||[]).length")
    pag.locator(".m2-cap.on").first.click()
    pag.wait_for_timeout(300)
    despues = pag.evaluate("() => (M2.enPantalla||[]).length")
    afirmar(despues < antes, "apagar una capa quita iconos (%d → %d)" % (antes, despues))
    pag.locator(".m2-cap").first.click()
    pag.wait_for_timeout(200)

    print("── ir a un sitio ──")
    pag.fill("#m2-ir", "17752 -14598")
    pag.click("#m2-btn-ir")
    pag.wait_for_timeout(1200)
    centro = pag.evaluate("() => [Math.round(M2.cx), Math.round(M2.cz)]")
    afirmar(centro == [17752, -14598], "el mapa se movió a la base %s" % centro)

    print("── acercar y alejar ──")
    e0 = pag.evaluate("() => M2.esc")
    pag.mouse.move(640, 450)
    pag.mouse.wheel(0, -600)
    pag.wait_for_timeout(1200)
    e1 = pag.evaluate("() => M2.esc")
    afirmar(e1 > e0, "la rueda acerca (%.4f → %.4f)" % (e0, e1))
    quieto = pag.evaluate("() => M2.est.niveles.includes(m2Nivel())")
    afirmar(quieto, "el nivel de detalle elegido es uno de los que hay")

    print("── arrastrar ──")
    c0 = pag.evaluate("() => [M2.cx, M2.cz]")
    pag.mouse.move(700, 450)
    pag.mouse.down()
    pag.mouse.move(500, 350, steps=8)
    pag.mouse.up()
    pag.wait_for_timeout(900)
    c1 = pag.evaluate("() => [M2.cx, M2.cz]")
    afirmar(c1 != c0, "arrastrar mueve el mapa")

    print("── clic en un icono ──")
    pag.wait_for_function("() => (M2.enPantalla||[]).length > 0", timeout=60000)
    hay = pag.evaluate("""() => {
      const p = (M2.enPantalla||[])[0];
      return p ? {x: Math.round(p[3]), y: Math.round(p[4])} : null;
    }""")
    if hay:
        caja = pag.locator("#m2-lienzo").bounding_box()
        pag.mouse.click(caja["x"] + hay["x"], caja["y"] + hay["y"])
        pag.wait_for_timeout(400)
        afirmar(not pag.locator("#m2-globo").is_hidden(), "el globo del icono se abre")
        print("      «%s»" % pag.text_content("#m2-globo"))
    else:
        afirmar(False, "había un icono en pantalla donde hacer clic")

    print("── el punto de aparición y los chunks de slime ──")
    ap = pag.evaluate("() => M2.est.aparicion")
    afirmar(ap is not None, "el panel sabe dónde aparece la gente %s" % ap)
    if ap:
        pag.evaluate("(a) => { M2.cx=a[0]; M2.cz=a[2]; M2.esc=1/4; M2.verSlime=true;"
                     " m2Pinta(); m2Capas(); m2PideEstructuras(); m2PideSlime(); }", ap)
        pag.wait_for_function("() => M2.slime && M2.slime.datos.length>0", timeout=30000)
        pag.wait_for_timeout(900)
        n = pag.evaluate("() => { let s=0; for (const v of M2.slime.datos) s+=v; "
                         "return {slime:s, total:M2.slime.datos.length}; }")
        afirmar(0.05 < n["slime"]/n["total"] < 0.16,
                "uno de cada diez chunks es de slime (%d de %d)" % (n["slime"], n["total"]))
        verde = pag.evaluate("""() => {
          const c=document.getElementById('m2-ico');
          const d=c.getContext('2d',{willReadFrequently:true}).getImageData(0,0,c.width,c.height).data;
          let n=0; for (let i=0;i<d.length;i+=4*7) if (d[i+3]>10 && d[i+1]>d[i]+30) n++;
          return n;
        }""")
        afirmar(verde > 50, "los chunks de slime se ven pintados de verde (%d)" % verde)
        afirmar(pag.locator("#m2cap-slime").count() == 1, "sale la casilla de slime")
        afirmar(pag.locator("#m2cap-aparicion").count() == 1, "sale la casilla de aparición")
        pag.locator("#m2cap-slime").click()
        pag.wait_for_timeout(400)
        apagado = pag.evaluate("() => M2.verSlime")
        afirmar(apagado is False, "la casilla apaga los chunks de slime")

    print("── la cabecera y la rejilla de capas ──")
    cab = pag.evaluate("""() => ({
      semilla: document.getElementById('m2-semilla').textContent,
      version: document.getElementById('m2-version').textContent,
      dim: document.getElementById('m2-dimension').textContent,
      cols: getComputedStyle(document.getElementById('m2-capas')).gridTemplateColumns.split(' ').length,
      etiquetas: document.querySelectorAll('.m2-cap .et').length
    })""")
    print("     ", cab)
    afirmar(cab["semilla"].isdigit() and len(cab["semilla"]) > 10,
            "la semilla se ve: %s" % cab["semilla"])
    afirmar("Java" in cab["version"], "la versión se ve: %s" % cab["version"])
    afirmar(cab["cols"] >= 2, "las capas van en rejilla (%d columnas)" % cab["cols"])
    afirmar(cab["etiquetas"] >= 14, "cada capa lleva su nombre (%d)" % cab["etiquetas"])

    pag.click("#m2-semilla-caja")
    pag.wait_for_timeout(400)
    afirmar("copiada" in pag.text_content("#m2-semilla-pista"),
            "pulsar la semilla la copia")

    print("── marcar y desmarcar todo ──")
    pag.click("text=Desmarcar todo")
    pag.wait_for_timeout(500)
    n0 = pag.evaluate("() => (M2.enPantalla||[]).length")
    afirmar(n0 == 0, "«desmarcar todo» deja el mapa sin iconos (%d)" % n0)
    pag.click("text=Marcar todo")
    pag.wait_for_timeout(700)
    n1 = pag.evaluate("() => (M2.enPantalla||[]).length")
    afirmar(n1 > 0, "«marcar todo» los devuelve (%d)" % n1)

    print("── que no se rompa nada ──")
    afirmar(not errores, "sin errores de JavaScript" + (" — %s" % errores[:3] if errores else ""))

    # (en el entorno de prueba faltan la fuente y los iconos del panel, así que
    #  la captura se hace del lienzo, no de la página entera)
    # La captura se saca del propio lienzo (los dos superpuestos) en vez de con
    # el navegador: en esta copia de pruebas faltan la fuente y los iconos del
    # panel y Playwright se queda esperándolos.
    import base64
    for nombre, e in (("cerca", 1/4), ("medio", 1/16), ("lejos", 1/64)):
        c0 = pag.evaluate("() => M2.ciclo||0")
        pag.evaluate("(e) => { M2.esc=e; m2Pinta(); m2Capas(); m2PideEstructuras(); }", e)
        pag.wait_for_function("(c) => (M2.ciclo||0) > c", arg=c0, timeout=180000)
        pag.wait_for_function("() => M2.pidiendo===0 && !M2.pintando", timeout=180000)
        pag.wait_for_timeout(1500)
        d = pag.evaluate("""() => {
          const a=document.getElementById('m2-bio'), b=document.getElementById('m2-ico');
          const c=document.createElement('canvas'); c.width=a.width; c.height=a.height;
          const g=c.getContext('2d'); g.drawImage(a,0,0); g.drawImage(b,0,0);
          return c.toDataURL('image/png');
        }""")
        open(os.environ.get("SALIDA", "/tmp") + "/mapa2-%s.png" % nombre, "wb").write(base64.b64decode(d.split(",",1)[1]))
        print("      mapa2-%s.png  (%d bloques de ancho, %d iconos)" % (nombre, round(1140/e), pag.evaluate("() => (M2.enPantalla||[]).length")))
    nav.close()

print()
if FALLOS:
    print("FALLAN %d:" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("todo bien")
