#!/usr/bin/env python3
"""
La pestaña Moderadores EN UN NAVEGADOR DE VERDAD: los seis interruptores, el
modo de solo lectura y el aviso del traspaso.

Las pruebas de `probar-permisos-pestanas.py` comprueban el portero — que el
backend diga que no. Esta comprueba lo otro, que es distinto y también falla
solo: que la pantalla no enseñe botones que luego dan 403, que el aviso diga de
verdad lo que se pierde, y que al traspasar el panel se reconstruya en vez de
quedarse enseñando pestañas que ya no son tuyas.

Correr:  python3 scripts/probar-ui-permisos.py
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


def main():
    from playwright.sync_api import sync_playwright

    base = Path(tempfile.mkdtemp(prefix="probar-ui-perm-"))
    print("escenario en %s" % base)
    _pm.montar(base)
    mc, panel, web = base / "minecraft", base / "panel", base / "web"
    shutil.copy(REPO / "scripts" / "mundos.py", panel / "scripts" / "mundos.py")
    shutil.copy(REPO / "nbt.py", panel / "nbt.py")
    (panel / "static").mkdir(exist_ok=True)
    for f in (REPO / "static").iterdir():
        if f.is_file():
            shutil.copy(f, panel / "static" / f.name)

    os.environ.update(
        MC_DIR=str(mc), PANEL_DIR=str(panel), BLUEMAP_WEB=str(web),
        ESTADO=str(base / "estado"), RENDER_LOG=str(base / "render.log"),
        PATH="%s:%s" % (base / "bin", os.environ["PATH"]),
        PANEL_SECRET="secreto-de-prueba-0123456789")

    s2 = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(s2); s2.loader.exec_module(srv)

    def cuentas():
        srv.save_users({
            "jey":   {"hash": srv.hash_pw("contraseñalarga"), "role": "admin",
                      "perms": {}, "must_change": False},
            "bicho": {"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                      "perms": {}, "must_change": False},
            "curioso": {"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                        "perms": {"moderadores": True}, "must_change": False}})
    cuentas()

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

        def abrir(quien):
            ctx = nav.new_context(viewport={"width": 1280, "height": 1000})
            ctx.add_cookies([{"name": "panel_session",
                              "value": srv.sign("%s|%d" % (quien, time.time() + 3600)),
                              "domain": "127.0.0.1", "path": "/"}])
            # Google Fonts no responde desde aquí; se contesta vacío en vez de
            # abortar, que dejaría document.fonts esperando para siempre.
            ctx.route("**://fonts.googleapis.com/**",
                      lambda r: r.fulfill(status=200, content_type="text/css", body=""))
            ctx.route("**://fonts.gstatic.com/**",
                      lambda r: r.fulfill(status=200, content_type="font/woff2", body=""))
            p = ctx.new_page()
            errs = []
            p.on("pageerror", lambda e: errs.append(str(e)))
            p.on("console", lambda m: errs.append("console:" + m.text)
                 if m.type == "error" else None)
            p.goto(raiz, wait_until="domcontentloaded")
            p.wait_for_selector("#tabs button", timeout=20000)
            return p, errs

        def pestanas(p):
            return [b.inner_text().strip() for b in p.locator("#tabs button").all()]

        # ── 1 · qué pestañas ve cada uno ─────────────────────────────────
        titulo("1 · las pestañas salen por permiso, no por rol")
        pa, ea = abrir("jey")
        tabs_admin = pestanas(pa)
        for nombre in ("Consola", "Copias", "Plugins", "Moderadores", "Mundo", "Sistema"):
            ok(any(nombre in t for t in tabs_admin), "el admin ve «%s»" % nombre)
        # la Consola sale por ROL, no por interruptor: es del admin y punto

        pb, eb = abrir("bicho")
        tabs_mod = pestanas(pb)
        ocultas = [n for n in ("Consola", "Copias", "Plugins", "Moderadores",
                               "Mundo", "Sistema")
                   if not any(n in t for t in tabs_mod)]
        ok(len(ocultas) == 6,
           "un moderador sin permisos no ve ninguna de las seis (%d/6)" % len(ocultas))
        ok(len(tabs_mod) > 0, "pero sí ve las suyas de siempre (%d)" % len(tabs_mod))

        pc, ec = abrir("curioso")
        tabs_cur = pestanas(pc)
        ok(any("Moderadores" in t for t in tabs_cur),
           "y con el interruptor encendido, «Moderadores» le aparece")
        ok(not any("Sistema" in t for t in tabs_cur),
           "y solo esa: «Sistema» sigue sin salirle")

        # ── 2 · solo lectura de verdad ───────────────────────────────────
        titulo("2 · con «moderadores» se mira, no se toca")
        pc.locator("#tabs button", has_text="Moderadores").first.click()
        pc.wait_for_selector("#users-list .usercard", timeout=10000)
        ok(pc.locator("#users-solo-ver").is_visible(),
           "le sale el aviso de que es solo lectura")
        ok(not pc.locator("#users-crear").is_visible(),
           "y el formulario de crear cuentas no está")
        ok(pc.locator("#users-list button", has_text="Eliminar").count() == 0,
           "ni botones de eliminar")
        ok(pc.locator("#users-list button", has_text="Hacerle admin").count() == 0,
           "ni de traspasar el admin")
        sw = pc.locator("#users-list .permrow input[type=checkbox]")
        n_sw = sw.count()
        apagados = sum(1 for i in range(n_sw) if sw.nth(i).is_disabled())
        ok(n_sw > 0 and apagados == n_sw,
           "y los %d interruptores están bloqueados, no solo escondidos" % n_sw)

        # ── 3 · el admin sí manda, y ve los seis nuevos ──────────────────
        titulo("3 · lo que ve el admin en la pestaña")
        pa.locator("#tabs button", has_text="Moderadores").first.click()
        pa.wait_for_selector("#users-list .usercard", timeout=10000)
        # OJO: los <h3> del panel llevan text-transform:uppercase, así que
        # innerText los devuelve en mayúsculas. Se compara sin distinguir.
        texto = pa.locator("#users-list").inner_text()
        bajo = texto.lower()
        ok("pestañas del panel" in bajo,
           "los interruptores de pestaña salen en su propio bloque")
        for et in ("Plugins", "Moderadores", "Mundo", "Sistema"):
            ok(et.lower() in bajo, "está el interruptor de «%s»" % et)
        ok("consola" not in bajo,
           "🔴 y NO hay interruptor de consola: no se puede dar a nadie")
        ok("sin vuelta atrás" in bajo,
           "y avisa de cuáles no tienen vuelta atrás")
        ok(pa.locator("#users-list button", has_text="Hacerle admin").count() == 2,
           "hay un «Hacerle admin» por cada moderador (y ninguno para sí mismo)")

        # ── 4 · el aviso del traspaso ────────────────────────────────────
        titulo("4 · el aviso antes del clic sin vuelta atrás")
        pa.locator("#users-list .usercard", has_text="bicho").locator(
            "button", has_text="Hacerle admin").first.click()
        pa.wait_for_selector("#tr-modal.open", timeout=5000)
        aviso = pa.locator("#tr-paso1").inner_text()
        ok("bicho" in aviso, "dice a quién se lo va a dar")
        ok("dejar de ser el admin" in aviso.lower() or "moderador" in aviso,
           "y que quien lo hace baja a moderador")
        puntos = pa.locator("#tr-lista li").count()
        ok(puntos >= 4, "con la lista de lo que se pierde igualmente (%d puntos)" % puntos)
        ok("consola" in aviso.lower(),
           "y la consola está entre lo que se pierde sí o sí")
        ok(aviso.count("bicho") >= 2,
           "y le recuerda a quién tendría que pedírselo para recuperarlo")

        # ── 4b · el paso 2: con qué me quedo ─────────────────────────────
        titulo("4b · elegir con qué se queda")
        pa.locator("#tr-paso1 button", has_text="Entendido").click()
        pa.wait_for_selector("#tr-paso2:not(.hidden)", timeout=5000)
        cajas = pa.locator("#tr-perms input[type=checkbox]")
        ok(cajas.count() > 0, "sale la lista de permisos a conservar (%d)" % cajas.count())
        marcadas = sum(1 for i in range(cajas.count()) if cajas.nth(i).is_checked())
        ok(0 < marcadas < cajas.count(),
           "vienen marcados los de un moderador normal, no todos (%d de %d)"
           % (marcadas, cajas.count()))
        etiquetas = pa.locator("#tr-perms").inner_text().lower()
        ok("consola" not in etiquetas,
           "🔴 y la consola no se puede marcar: no está en la lista")
        ok("consola" in pa.locator("#tr-paso2").inner_text().lower(),
           "pero se explica por qué no está")

        # el botón de «solo jugador normal» esconde la lista
        pa.locator("#tr-rol-viewer").click()
        pa.wait_for_timeout(200)
        ok(not pa.locator("#tr-perms-caja").is_visible(),
           "al elegir «solo jugador normal» desaparece la lista de permisos")
        ok(pa.locator("#tr-viewer-nota").is_visible(),
           "y se explica qué es un observador")
        pa.locator("#tr-rol-mod").click()
        pa.wait_for_timeout(200)
        ok(pa.locator("#tr-perms-caja").is_visible(), "y se puede volver atrás")

        # marcar / desmarcar todo
        pa.locator("#tr-paso2 button[onclick=\"traspasoMarcar(false)\"]").click()
        pa.wait_for_timeout(150)
        ok(sum(1 for i in range(cajas.count()) if cajas.nth(i).is_checked()) == 0,
           "«Desmarcar todo» desmarca todo")
        pa.locator("#tr-paso2 button[onclick=\"traspasoMarcar(true)\"]").click()
        pa.wait_for_timeout(150)
        ok(sum(1 for i in range(cajas.count()) if cajas.nth(i).is_checked()) == cajas.count(),
           "y «Marcar todo» marca todo")

        # volver al paso 1 y cancelar: no pasa nada
        pa.locator("#tr-paso2 button", has_text="Atrás").click()
        pa.wait_for_selector("#tr-paso1:not(.hidden)", timeout=5000)
        ok(True, "se puede volver al aviso")
        pa.locator("#tr-paso1 button", has_text="Cancelar").click()
        pa.wait_for_timeout(300)
        ok(srv.load_users()["jey"]["role"] == "admin",
           "si se cancela, no pasa absolutamente nada")

        # ── 5 · y al aceptar, el panel se reconstruye ────────────────────
        titulo("5 · al traspasarlo, la pantalla se pone al día sola")
        pa.locator("#users-list .usercard", has_text="bicho").locator(
            "button", has_text="Hacerle admin").first.click()
        pa.wait_for_selector("#tr-modal.open", timeout=5000)
        pa.locator("#tr-paso1 button", has_text="Entendido").click()
        pa.wait_for_selector("#tr-paso2:not(.hidden)", timeout=5000)
        # se conserva «ban» y nada más, para comprobar que la elección viaja
        pa.locator("#tr-paso2 button[onclick=\"traspasoMarcar(false)\"]").click()
        # El <input> real está escondido por el CSS del interruptor (se ve el
        # .slider), así que .check() se queda esperando a algo que nunca es
        # visible. Se pulsa lo que pulsa una persona: la etiqueta.
        pa.locator("#tr-perms label:has(input[data-perm=ban])").click()
        ok(pa.locator("#tr-perms input[data-perm=ban]").is_checked(),
           "el interruptor se marca pulsándolo como lo pulsaría una persona")
        pa.locator("#tr-ok").click()
        # el modal cerrado queda OCULTO, no ausente: hay que esperar a eso
        pa.wait_for_selector("#tr-modal.open", state="hidden", timeout=10000)
        pa.wait_for_timeout(600)
        users = srv.load_users()
        ok(users["bicho"]["role"] == "admin" and users["jey"]["role"] == "mod",
           "el traspaso ha ocurrido")
        ok(users["jey"]["perms"].get("ban") is True,
           "y lo que se marcó en el paso 2 llega de verdad (conserva «ban»)")
        ok(users["jey"]["perms"].get("mundo") is not True,
           "y lo que no se marcó, no (no conserva «mundo»)")
        quedan = pestanas(pa)
        fuera = [n for n in ("Consola", "Copias", "Plugins", "Moderadores",
                             "Mundo", "Sistema")
                 if not any(n in t for t in quedan)]
        ok(len(fuera) == 6,
           "y las seis pestañas desaparecen sin recargar la página (%d/6)" % len(fuera))

        # ── 6 · sin errores de JavaScript por el camino ──────────────────
        titulo("6 · la consola del navegador, limpia")
        # Las peticiones que el backend niega con 403 ensucian la consola del
        # navegador con un "Failed to load resource" que no es un fallo nuestro.
        ruido = ("Failed to load resource", "403", "401", "favicon",
                 "mc-heads", "fonts")
        def limpios(errs):
            return [e for e in errs if not any(r in e for r in ruido)]
        for quien, errs in (("admin", ea), ("moderador", eb), ("solo lectura", ec)):
            ok(not limpios(errs), "%s: sin errores de JavaScript%s"
               % (quien, "" if not limpios(errs) else " — %s" % limpios(errs)[:2]))

        nav.close()
    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
