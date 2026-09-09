#!/usr/bin/env python3
"""
Prueba la pestaña Mundo EN UN NAVEGADOR DE VERDAD.

Levanta el server.py real sobre un escenario de mentira, abre el
static/index.html real en Chromium y hace lo que haría Juan: entrar en la
pestaña, subir un mundo arrastrándolo, cambiar de mundo confirmando los avisos
y volver. Nada de maquetas: si el HTML tiene una `let` mal puesta o un botón
que no engancha, aquí se ve.

Correr:  python3 scripts/probar-ui-mundos.py
"""
import importlib.util, os, shutil, socket, sys, tempfile, threading, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent
spec = importlib.util.spec_from_file_location("pm", AQUI / "probar-mundos.py")
_pm = importlib.util.module_from_spec(spec); spec.loader.exec_module(_pm)

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


def puerto_libre():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def esperar_texto(page, sel, trozo, segundos=90, visible=False):
    """Espera a que un trozo de texto aparezca.

    OJO con `visible`: innerText de un elemento con display:none devuelve, por
    especificación, su textContent. O sea que buscar «Descargar (» en un botón
    escondido acierta a la primera y la prueba sigue creyendo que ya está listo
    cuando no lo está. Costó un rato entenderlo: cuando lo que importa es que
    algo se VEA, hay que decirlo.
    """
    t0 = time.time()
    while time.time() - t0 < segundos:
        try:
            loc = page.locator(sel)
            if (not visible or loc.is_visible()) and \
               trozo.lower() in (loc.inner_text() or "").lower():
                return True
        except Exception:
            pass
        page.wait_for_timeout(400)
    return False


def clic_en(page, texto, segundos=60):
    """Pulsa un botón de la lista de mundos esperando a que esté habilitado.

    Los botones se deshabilitan mientras hay un trabajo en marcha, así que un
    clic a ciegas se queda esperando sin decir por qué; si se agota el plazo,
    esto enseña lo que había en pantalla.
    """
    t0 = time.time()
    while time.time() - t0 < segundos:
        b = page.locator("#mu-lista button", has_text=texto).first
        try:
            if b.count() and b.is_enabled():
                b.click(); return True
        except Exception:
            pass
        page.wait_for_timeout(500)
    print("   ↳ no pude pulsar «%s». La lista decía: %r" % (texto,
          page.locator("#mu-lista").inner_text()[:300]))
    return False


def main():
    from playwright.sync_api import sync_playwright

    base = Path(tempfile.mkdtemp(prefix="probar-ui-"))
    print("escenario en %s" % base)
    _pm.montar(base)
    mc, panel, web = base / "minecraft", base / "panel", base / "web"
    shutil.copy(REPO / "scripts" / "mundos.py", panel / "scripts" / "mundos.py")
    # mundos.py lee el nbt.py del panel para saber la versión de un mundo; sin
    # él la comprobación se queda muda y la prueba no probaría nada
    shutil.copy(REPO / "nbt.py", panel / "nbt.py")
    shutil.copy(REPO / "scripts" / "actualizar.py", panel / "scripts" / "actualizar.py")
    # un Mojang de mentira: si apuntara al de verdad, la prueba dependería de
    # internet y de qué versión haya sacado Mojang esta semana
    import importlib.util as _iu2
    _sp = _iu2.spec_from_file_location("pa", AQUI / "probar-actualizar.py")
    _pa = _iu2.module_from_spec(_sp); _sp.loader.exec_module(_pa)
    _pa.escribir(mc / "server.jar", "JAR 26.2")
    _pa.escribir(mc / "versions" / "26.2" / "server-26.2.jar", "JAR 26.2")
    manif = _pa.publicar_version(base, "26.3")
    bmrel = _pa.publicar_bluemap(base, "5.23")
    # el panel sirve su propio static/: se le da el real
    (panel / "static").mkdir(exist_ok=True)
    for f in (REPO / "static").iterdir():
        if f.is_file():
            shutil.copy(f, panel / "static" / f.name)

    os.environ.update(
        MC_DIR=str(mc), PANEL_DIR=str(panel), BLUEMAP_WEB=str(web),
        ESTADO=str(base / "estado"), RENDER_LOG=str(base / "render.log"),
        PATH="%s:%s" % (base / "bin", os.environ["PATH"]),
        PANEL_SECRET="secreto-de-prueba-0123456789",
        BLUEMAP_DIR=str(base / "bluemap"),
        MC_MANIFIESTO=manif, BLUEMAP_RELEASES=bmrel)

    spec2 = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(srv)
    srv.save_users({"jey": {"hash": srv.hash_pw("contraseñalarga"), "role": "admin",
                            "perms": {}, "must_change": False}})

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    puerto = puerto_libre()
    threading.Thread(target=lambda: srv.app.run(host="127.0.0.1", port=puerto,
                                                threaded=True, use_reloader=False),
                     daemon=True).start()
    for _ in range(60):
        try:
            socket.create_connection(("127.0.0.1", puerto), timeout=0.5).close()
            break
        except OSError:
            time.sleep(0.2)
    raiz = "http://127.0.0.1:%d" % puerto

    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        ctx = nav.new_context(viewport={"width": 1280, "height": 900})
        # la cookie va ANTES de cargar: si no, sale la portada y tapa la app
        ctx.add_cookies([{"name": "panel_session",
                          "value": srv.sign("jey|%d" % (time.time() + 3600)),
                          "domain": "127.0.0.1", "path": "/"}])
        # Google Fonts no responde desde aquí. Se responde VACÍO en vez de
        # abortar: una petición de fuente abortada deja document.fonts esperando
        # para siempre y luego page.screenshot() se cuelga sin decir por qué.
        ctx.route("**://fonts.googleapis.com/**",
                  lambda r: r.fulfill(status=200, content_type="text/css", body=""))
        ctx.route("**://fonts.gstatic.com/**",
                  lambda r: r.fulfill(status=200, content_type="font/woff2", body=""))
        page = ctx.new_page()
        errores = []
        page.on("pageerror", lambda e: errores.append(str(e)))
        page.on("console", lambda m: errores.append("console:" + m.text)
                if m.type == "error" else None)
        page.add_init_script(
            "addEventListener('unhandledrejection',e=>{"
            "console.error('promesa sin capturar: '+(e.reason&&e.reason.stack||e.reason))})")
        avisos = []
        page.on("dialog", lambda d: (avisos.append(d.message), d.accept()))
        page.goto(raiz, wait_until="domcontentloaded")
        page.wait_for_selector("#tabs button", timeout=20000)

        # ── 1. la pestaña existe y se abre ───────────────────────────────────
        titulo("1 · la pestaña")
        ok(page.locator("#tabbtn-mundo").count() == 1, "sale la pestaña Mundo")
        ok(page.locator("#tabbtn-mundo .tlab").inner_text().strip() == "Mundo",
           "con su nombre")
        page.click("#tabbtn-mundo")
        page.wait_for_timeout(900)
        ok(not page.locator("#tab-mundo").evaluate("e=>e.classList.contains('hidden')"),
           "al pulsarla se abre")
        ok(esperar_texto(page, "#mu-activo", "mundo original"),
           "enseña el mundo activo por su nombre")
        ok(any(u in page.locator("#mu-activo").inner_text() for u in ("KB","MB","GB")),
           "y cuánto pesa")
        ok("libres" in page.locator("#mu-disco").inner_text(), "dice el disco que queda")
        ok("todavía no hay otros" in page.locator("#mu-lista").inner_text().lower(),
           "y avisa de que aún no hay otros mundos")

        # ── 2. preparar la descarga ──────────────────────────────────────────
        titulo("2 · preparar la descarga")
        page.click("#mu-prep")
        ok(esperar_texto(page, "#mu-baja", "descargar (", visible=True),
           "el botón de descarga aparece con el peso cuando está lista")
        ok(page.locator("#mu-trabajo").evaluate("e=>e.classList.contains('hidden')"),
           "y el aviso de «trabajando» se quita al acabar")

        # ── 3. subir un mundo ────────────────────────────────────────────────
        titulo("3 · subir un mundo")
        z = _pm.zip_de_mundo(base / "rojo.zip", "ROJO")
        page.set_input_files("#mu-file", str(z))
        ok(esperar_texto(page, "#mu-modal", "mundo subido", 60, visible=True),
           "al terminar la subida pregunta qué hacer con él")
        info = page.locator("#mu-m-info").inner_text()
        ok("26.2" in info, "y dice de qué versión de Minecraft viene (%s)" % info[:60])
        ok(page.locator("#mu-m-aviso").evaluate("e=>e.classList.contains('hidden')"),
           "sin aviso de versión: es la misma que el servidor")
        ok(page.locator("#mu-m-nombre").evaluate("e=>e.classList.contains('hidden')"),
           "por defecto propone reemplazar, así que no pide nombre")
        page.check("input[name=mu-modo][value=nuevo]")
        ok(page.locator("#mu-m-nombre").is_visible(), "al marcar «mundo distinto» pide el nombre")
        page.fill("#mu-m-nombre", "Mundo Rojo")
        page.click("#mu-m-ok")
        ok(esperar_texto(page, "#mu-lista", "mundo rojo"), "aparece en la lista de guardados")
        ok("habrá que dibujar el mapa" in page.locator("#mu-lista").inner_text(),
           "avisando de que aún no tiene mapa")
        ok(page.locator("#mu-subiendo").evaluate("e=>e.classList.contains('hidden')"),
           "la barra de progreso se esconde al terminar")

        # un archivo que no vale
        malo = base / "notas.txt"; malo.write_text("hola")
        page.set_input_files("#mu-file", str(malo))
        page.wait_for_timeout(600)
        ok("zip" in page.locator("#toast").inner_text().lower(),
           "un archivo que no es un mundo se rechaza en el navegador")

        # ── 3b. un mundo de una versión más nueva ────────────────────────────
        titulo("3b · un mundo de un Minecraft más nuevo")
        tmp = base / "crudo-FUT"; shutil.rmtree(tmp, ignore_errors=True)
        _pm.mundo_falso(tmp / "mundo", "FUTURO", ver_id=99999, ver="99.9")
        import zipfile as _z
        with _z.ZipFile(base / "futuro.zip", "w") as zz:
            for f in tmp.rglob("*"):
                if f.is_file():
                    zz.write(f, f.relative_to(tmp))
        shutil.rmtree(tmp, ignore_errors=True)
        page.set_input_files("#mu-file", str(base / "futuro.zip"))
        ok(esperar_texto(page, "#mu-m-aviso", "más nuevo", 60, visible=True),
           "avisa en rojo de que el servidor no podría abrirlo")
        ok(not page.locator("#mu-m-ok").is_enabled(), "y no deja continuar")
        page.click("#mu-modal .btn.ghost")
        page.wait_for_timeout(400)
        ok(not page.locator("#mu-modal").is_visible(), "se puede cancelar")

        # ── 4. cambiar de mundo ──────────────────────────────────────────────
        titulo("4 · cambiar de mundo")
        (base / "render.log").unlink(missing_ok=True)
        avisos.clear()
        ok(clic_en(page, "Entrar en este mundo"), "el botón de entrar se puede pulsar")
        page.wait_for_timeout(700)
        ok(len(avisos) == 2, "pide DOS confirmaciones antes de tumbar el servidor (%d)" % len(avisos))
        ok(avisos and "sale" in avisos[0], "la primera explica que echa a los que estén dentro")
        ok(avisos and "dibujarlo desde cero" in avisos[0],
           "y avisa de que el mapa hay que rehacerlo")
        ok(esperar_texto(page, "#mu-activo", "mundo rojo"), "el panel ya lo da por activo")
        ok(_pm.marca_de(mc / "world" / "marca.txt") == "ROJO",
           "y en el disco `world` es el Rojo de verdad")
        time.sleep(1)
        ok(any("--completo" in l for l in _pm.render_log(base)),
           "se lanzó el redibujado completo del mapa")

        titulo("5 · volver, y quitar")
        (base / "render.log").unlink(missing_ok=True)
        ok(clic_en(page, "Entrar en este mundo"), "se puede volver a entrar en el otro")
        ok(esperar_texto(page, "#mu-activo", "mundo original"), "se vuelve al de siempre")
        ok(_pm.marca_de(web / "maps" / "overworld" / "tiles" / "0" / "x0" / "z0.png") == "ORIGINAL",
           "con sus azulejos del mapa restaurados")
        time.sleep(1)
        ok(_pm.render_log(base) and not any("--completo" in l for l in _pm.render_log(base)),
           "y esta vez sin redibujar de cero")
        clic_en(page, "Quitar")
        ok(esperar_texto(page, "#mu-lista", "todavía no hay otros"), "quitar lo saca de la lista")
        ok(esperar_texto(page, "#mu-disco", "papelera"), "y aparece la papelera con su peso")

        # ── 5b. reemplazar el mundo activo ───────────────────────────────────
        titulo("5b · subir una versión nueva del mundo de ahora")
        (base / "render.log").unlink(missing_ok=True)
        z2 = _pm.zip_de_mundo(base / "orig-v2.zip", "ORIGINAL-V2")
        avisos.clear()
        page.set_input_files("#mu-file", str(z2))
        ok(esperar_texto(page, "#mu-modal", "mundo subido", 60, visible=True),
           "vuelve a preguntar")
        page.check("input[name=mu-modo][value=reemplazar]")
        page.click("#mu-m-ok")
        page.wait_for_timeout(600)
        ok(len(avisos) == 1 and "no se pierde" in avisos[0],
           "pide confirmación y explica que la versión de ahora se guarda")
        ok(esperar_texto(page, "#mu-lista", "antes del", 90),
           "la versión anterior queda en la lista")
        ok(_pm.marca_de(mc / "world" / "marca.txt") == "ORIGINAL-V2",
           "y en el disco `world` es la versión nueva")
        ok((web / "maps" / "overworld" / "tiles" / "0" / "x0" / "z0.png").exists(),
           "el mapa NO se tiró: sigue puesto")
        time.sleep(1)
        ok(_pm.render_log(base) and not any("--completo" in l for l in _pm.render_log(base)),
           "así que el redibujado es incremental")

        # ── 5c. traer los jugadores ──────────────────────────────────────────
        titulo("5c · traer los jugadores de otro mundo")
        ok(page.locator("#mu-traer").is_visible(), "sale la tarjeta de traer jugadores")
        u = _pm.JUGADORES[0]
        import json as _j
        ok(_j.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "ORIGINAL-V2",
           "de partida, el activo tiene sus estadísticas")
        origenes = page.locator("#mu-tr-origen option").all_inner_texts()
        ok(any("antes del" in o for o in origenes),
           "y el desplegable ofrece la versión anterior (%s)" % origenes)
        page.select_option("#mu-tr-origen",
                           index=[i for i, o in enumerate(origenes) if "antes del" in o][0])
        avisos.clear()
        page.click("#mu-traer button")
        page.wait_for_timeout(600)
        ok(len(avisos) == 1 and "se guarda antes de pisarlo" in avisos[0],
           "avisa de lo que va a pasar antes de tocar nada")
        t0 = time.time()
        while time.time() - t0 < 90:
            if _j.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "ORIGINAL":
                break
            page.wait_for_timeout(500)
        ok(_j.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "ORIGINAL",
           "las estadísticas del otro mundo llegan al activo")
        ok(_j.loads((mc / "world" / "advancements" / (u + ".json")).read_text())["marca"] == "ORIGINAL",
           "y los logros también")

        # ── 5d. la versión de Minecraft y el mapa en pausa ───────────────────
        titulo("5d · versión de Minecraft")
        page.click("#tabbtn-sistema")
        ok(esperar_texto(page, "#ver-kv", "26.2", 60), "Sistema dice qué versión hay puesta")
        ok("26.3" in page.locator("#ver-kv").inner_text(), "y que hay una 26.3 esperando")
        ok(page.locator("#ver-ahora").is_visible(), "sale el botón de actualizar")
        ok(page.is_checked("#ver-auto"), "y la casilla de actualizarse solo viene marcada")

        avisos.clear()
        page.click("#ver-ahora")
        page.wait_for_timeout(600)
        ok(len(avisos) == 1 and "NO tiene vuelta atrás" in avisos[0],
           "avisa de que convertir el mundo no se deshace")
        ok(esperar_texto(page, "#ver-kv", "26.3", 120), "actualiza a la 26.3")

        titulo("5e · el mapa se queda en pausa, no se pierde")
        ok(esperar_texto(page, "#ver-nota", "en pausa", 120),
           "Sistema dice que el mapa está en pausa")
        ok(esperar_texto(page, "#sys-auto", "en pausa", 30),
           "y el semáforo lo pinta como pausa, no como avería")
        page.click("#tabbtn-map")
        ok(esperar_texto(page, "#mapa-congelado", "todavía no la soporta", 60, visible=True),
           "en la pestaña Mapa sale el aviso explicando por qué")
        ok("mapa de antes, completo" in page.locator("#mapa-congelado-n").inner_text(),
           "y deja claro que el mapa que se ve sigue entero")
        ok(page.locator("#mapa-congelado-b").is_visible(), "con su botón para intentarlo")

        # ── 6. nada roto por el camino ───────────────────────────────────────
        titulo("6 · la consola del navegador")
        # el iframe del mapa apunta a un BlueMap que aquí no existe, así que
        # carga el propio panel y su CSP lo rechaza: ruido del escenario, no del panel
        graves = [e for e in errores if "404" not in e and "ERR_FAILED" not in e
                  and "frame-ancestors" not in e]
        ok(not graves, "ni un error de JavaScript en toda la sesión: %s" % (graves[:2] or ""))

        # La captura va por CDP y no por page.screenshot(): las fuentes de la
        # página no cargan aquí (viven en el servidor de verdad) y
        # page.screenshot se queda esperando a document.fonts para siempre.
        # CDP dispara la foto sin esperar a nadie.
        try:
            foto = ctx.new_cdp_session(page).send(
                "Page.captureScreenshot", {"format": "png", "captureBeyondViewport": True})
            import base64
            (base / "pestana-mundo.png").write_bytes(base64.b64decode(foto["data"]))
            print("   captura: %s" % (base / "pestana-mundo.png"))
        except Exception as e:
            print("   (sin captura: %s)" % e)
        ctx.close(); nav.close()

    print("\n" + "─" * 60)
    if fallos:
        print("\033[31m%d fallos\033[0m de %d comprobaciones:" % (len(fallos), pasadas + len(fallos)))
        for f in fallos:
            print("   · %s" % f)
    else:
        print("\033[32m%d comprobaciones, todas bien\033[0m" % pasadas)
    print("escenario: %s" % base)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
