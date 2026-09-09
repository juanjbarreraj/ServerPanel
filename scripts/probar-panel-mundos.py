#!/usr/bin/env python3
"""
Prueba la pestaña Mundo POR DENTRO DEL PANEL DE VERDAD.

No reconstruye nada: importa el server.py real, le apunta el MC_DIR y el
PANEL_DIR a un escenario de mentira (el mismo que monta probar-mundos.py) y le
habla por HTTP con el cliente de pruebas de Flask. Así lo que se comprueba es
el panel que se sube al servidor, no una maqueta parecida.

Cubre el ciclo entero: listar → preparar y descargar → subir a trozos →
importar → cambiar → volver → borrar → vaciar; más los permisos.

Correr:  python3 scripts/probar-panel-mundos.py
"""
import importlib.util, io, json, os, shutil, subprocess, sys, tempfile, time, zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent

sys.path.insert(0, str(AQUI))
import importlib
_pm = importlib.import_module("probar-mundos".replace("-", "_")) \
    if (AQUI / "probar_mundos.py").exists() else None
if _pm is None:                       # el fichero se llama con guiones
    spec = importlib.util.spec_from_file_location("pm", AQUI / "probar-mundos.py")
    _pm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(_pm)

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


def esperar(cli, segundos=180):
    """Espera a que el trabajo en marcha termine, y devuelve cómo acabó."""
    t0 = time.time()
    while time.time() - t0 < segundos:
        d = cli.get("/api/mundos").get_json()
        tr = d.get("trabajo") or {}
        if tr.get("estado") != "trabajando":
            return tr
        time.sleep(0.5)
    return {"estado": "colgado", "mensaje": "no terminó en %ds" % segundos}


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-panel-"))
    print("escenario en %s" % base)
    _pm.montar(base)
    mc, panel, web = base / "minecraft", base / "panel", base / "web"

    # el panel busca mundos.py dentro de su propia carpeta de scripts
    shutil.copy(REPO / "scripts" / "mundos.py", panel / "scripts" / "mundos.py")
    # mundos.py lee el nbt.py del panel para saber la versión de un mundo; sin
    # él la comprobación se queda muda y la prueba no probaría nada
    shutil.copy(REPO / "nbt.py", panel / "nbt.py")

    os.environ.update(
        MC_DIR=str(mc), PANEL_DIR=str(panel), BLUEMAP_WEB=str(web),
        ESTADO=str(base / "estado"), RENDER_LOG=str(base / "render.log"),
        PATH="%s:%s" % (base / "bin", os.environ["PATH"]),
        PANEL_SECRET="secreto-de-prueba-0123456789")

    spec = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)          # ← el server.py DE VERDAD

    # una cuenta de admin y otra de mod, firmadas como las firma él mismo
    srv.save_users({
        "jey":  {"hash": srv.hash_pw("contraseñalarga"), "role": "admin",
                 "perms": {}, "must_change": False},
        "bicho": {"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                  "perms": {}, "must_change": False}})

    srv.app.config["TESTING"] = True

    def cliente(quien):
        c = srv.app.test_client()
        if quien:
            c.set_cookie("panel_session",
                         srv.sign("%s|%d" % (quien, time.time() + 3600)),
                         domain="localhost")
        c.environ_base["HTTP_X_PANEL"] = "1"
        return c

    admin, mod, fuera = cliente("jey"), cliente("bicho"), cliente(None)

    # ── 1. permisos ──────────────────────────────────────────────────────────
    titulo("1 · quién puede entrar")
    ok(fuera.get("/api/mundos").status_code == 401, "sin sesión: 401")
    ok(mod.get("/api/mundos").status_code == 403, "un mod no ve los mundos: 403")
    ok(admin.get("/api/mundos").status_code == 200, "el admin sí: 200")
    ok(mod.post("/api/mundos/cambiar", json={"slug": "x"}).status_code == 403,
       "un mod no puede cambiar de mundo")
    sin_csrf = srv.app.test_client()
    sin_csrf.set_cookie("panel_session", srv.sign("jey|%d" % (time.time() + 3600)),
                        domain="localhost")
    ok(sin_csrf.post("/api/mundos/cambiar", json={"slug": "x"}).status_code == 403,
       "sin la cabecera X-Panel tampoco (escudo anti-CSRF)")

    # ── 2. listar ────────────────────────────────────────────────────────────
    titulo("2 · la lista")
    d = admin.get("/api/mundos").get_json()
    ok(d["ok"] and d["disponible"], "el panel encuentra mundos.py")
    ok(len(d["mundos"]) == 1 and d["mundos"][0]["activo"], "un mundo, y es el activo")
    ok(d["libre"] > 0, "dice cuánto disco queda")
    ok(d["descarga"] is None, "todavía no hay ninguna descarga preparada")

    # ── 3. preparar y descargar ──────────────────────────────────────────────
    titulo("3 · descargar el mundo de ahora")
    r = admin.post("/api/mundos/preparar").get_json()
    ok(r["ok"], "acepta prepararla")
    ok(admin.get("/api/mundos").get_json()["trabajo"]["estado"] in ("trabajando", "listo"),
       "y lo cuenta mientras trabaja")
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "la copia termina bien (%s)" % tr.get("mensaje", "")[:60])
    d = admin.get("/api/mundos").get_json()
    ok(d["descarga"] and d["descarga"]["bytes"] > 0, "aparece la copia lista, con su peso")
    resp = admin.get("/api/mundos/descargar")
    ok(resp.status_code == 200, "se puede descargar")
    ok("attachment" in resp.headers.get("Content-Disposition", ""),
       "va como adjunto, no se abre en el navegador")
    tgz = base / "bajado.tar.gz"
    tgz.write_bytes(resp.data)
    listado = subprocess.run(["tar", "tzf", str(tgz)], capture_output=True, text=True).stdout
    ok("world/level.dat" in listado, "y lo descargado es el mundo de verdad")
    ok("save-off" in _pm.consola(base), "se hizo con el guardado pausado")

    # ── 4. subir a trozos ────────────────────────────────────────────────────
    titulo("4 · subir un mundo a trozos")
    z = _pm.zip_de_mundo(base / "verde.zip", "VERDE")
    crudo, sid = z.read_bytes(), "abc123def456"
    trozo = max(1, len(crudo) // 3)
    enviados = 0
    while enviados < len(crudo):
        cacho = crudo[enviados:enviados + trozo]
        r = admin.post("/api/mundos/trozo", data={
            "id": sid, "offset": str(enviados),
            "trozo": (io.BytesIO(cacho), "world.zip")},
            content_type="multipart/form-data")
        if r.status_code != 200:
            ok(False, "trozo en %d: %s" % (enviados, r.get_json()))
            break
        enviados += len(cacho)
    ok(enviados == len(crudo), "los %d bytes llegan en %d trozos"
       % (len(crudo), -(-len(crudo) // trozo)))
    r = admin.post("/api/mundos/trozo", data={
        "id": sid, "offset": "0", "trozo": (io.BytesIO(b"x"), "world.zip")},
        content_type="multipart/form-data")
    ok(r.status_code == 409 and r.get_json().get("esperado") == len(crudo),
       "un trozo repetido se rechaza y dice por dónde iba (no se corrompe)")
    r = admin.post("/api/mundos/trozo", data={
        "id": "../../malo", "offset": "0", "trozo": (io.BytesIO(b"x"), "w.zip")},
        content_type="multipart/form-data")
    ok(r.status_code == 400, "un id con rutas se rechaza")

    r = admin.post("/api/mundos/importar", json={"id": sid, "nombre": "Mundo Verde"}).get_json()
    ok(r["ok"], "acepta importarlo")
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "lo importa bien (%s)" % tr.get("mensaje", "")[:70])
    d = admin.get("/api/mundos").get_json()
    ok(len(d["mundos"]) == 2, "ya son 2 mundos")
    verde = [m for m in d["mundos"] if m["slug"] == "mundo-verde"]
    ok(len(verde) == 1 and not verde[0]["activo"], "el Verde está, y no es el activo")
    ok(not list((panel / "data" / "subidas").glob("*.part")),
       "el archivo subido se borra al terminar (son los mismos gigas otra vez)")

    r = admin.post("/api/mundos/importar", json={"id": "999999999", "nombre": "Fantasma"})
    ok(r.status_code == 400, "no se puede importar una subida que no existe")
    r = admin.post("/api/mundos/importar", json={"id": sid, "nombre": "x"})
    ok(r.status_code == 400, "ni sin ponerle nombre")

    # ── 5. cambiar de mundo ──────────────────────────────────────────────────
    titulo("5 · cambiar de mundo desde el panel")
    (base / "render.log").unlink(missing_ok=True)
    r = admin.post("/api/mundos/cambiar", json={"slug": "mundo-verde"}).get_json()
    ok(r["ok"], "acepta el cambio")
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "el cambio acaba bien (%s)" % tr.get("mensaje", "")[:70])
    ok(_pm.marca_de(mc / "world" / "marca.txt") == "VERDE", "`world` es ahora el Verde")
    ok((mc / "mundos" / "mundo-original" / "mapa").is_dir(),
       "el mapa del anterior se archivó con él")
    d = admin.get("/api/mundos").get_json()
    act = [m for m in d["mundos"] if m["activo"]][0]
    ok(act["slug"] == "mundo-verde", "la lista ya lo marca como activo")
    ok(not act["tiene_mapa"], "y avisa de que aún no tiene mapa")
    time.sleep(1)
    ok(any("--completo" in l for l in _pm.render_log(base)),
       "lanzó el redibujado completo del mapa")

    titulo("6 · volver atrás")
    (base / "render.log").unlink(missing_ok=True)
    admin.post("/api/mundos/cambiar", json={"slug": "mundo-original"})
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "vuelve bien")
    ok(_pm.marca_de(mc / "world" / "marca.txt") == "ORIGINAL", "`world` es el original")
    ok(_pm.marca_de(web / "maps" / "overworld" / "tiles" / "0" / "x0" / "z0.png") == "ORIGINAL",
       "sus azulejos vuelven al mapa web")
    time.sleep(1)
    ok(_pm.render_log(base) and not any("--completo" in l for l in _pm.render_log(base)),
       "esta vez el render es incremental, no de horas")

    # ── 7. borrar y papelera ─────────────────────────────────────────────────
    titulo("7 · quitar un mundo")
    r = admin.post("/api/mundos/borrar", json={"slug": "mundo-original"}).get_json()
    esperar(admin)
    d = admin.get("/api/mundos").get_json()
    ok(len(d["mundos"]) == 2, "no deja quitar el mundo activo")
    admin.post("/api/mundos/borrar", json={"slug": "mundo-verde"})
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "quita el otro")
    d = admin.get("/api/mundos").get_json()
    ok(len(d["mundos"]) == 1, "queda uno en la lista")
    ok(d["papelera"] > 0, "y lo quitado se ve en la papelera")
    admin.post("/api/mundos/vaciar_papelera")
    esperar(admin)
    ok(admin.get("/api/mundos").get_json()["papelera"] == 0, "la papelera se vacía")

    # ── 8. mundo nuevo ───────────────────────────────────────────────────────
    titulo("8 · estrenar mundo desde el panel")
    r = admin.post("/api/mundos/nuevo", json={"nombre": "Temporada 2",
                                              "semilla": "12345"}).get_json()
    ok(r["ok"], "acepta")
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "lo estrena (%s)" % tr.get("mensaje", "")[:70])
    ok(_pm.marca_de(mc / "world" / "marca.txt") == "GENERADO", "el servidor generó el mundo")
    ok("level-seed=12345" in (mc / "server.properties").read_text(), "con la semilla pedida")
    d = admin.get("/api/mundos").get_json()
    ok([m for m in d["mundos"] if m["activo"]][0]["nombre"] == "Temporada 2",
       "y la lista lo enseña con su nombre")
    r = admin.post("/api/mundos/nuevo", json={"nombre": " "})
    ok(r.status_code == 400, "no deja crear uno sin nombre")

    # ── 9. inspeccionar y el aviso de versión ────────────────────────────────
    titulo("9 · mirar el archivo antes de meterlo")

    def sube(cli, ruta, sid):
        crudo = Path(ruta).read_bytes()
        off = 0
        while off < len(crudo):
            cacho = crudo[off:off + 4096]
            r = cli.post("/api/mundos/trozo", data={
                "id": sid, "offset": str(off), "trozo": (io.BytesIO(cacho), "w.zip")},
                content_type="multipart/form-data")
            assert r.status_code == 200, r.get_json()
            off += len(cacho)
        return sid

    z = _pm.zip_de_mundo(base / "gris.zip", "GRIS")
    sube(admin, z, "aaaa1111bbbb")
    r = admin.post("/api/mundos/inspeccionar", json={"id": "aaaa1111bbbb"}).get_json()
    ok(r["ok"] and r["legible"], "inspecciona el archivo subido")
    ok(r["version"] == "26.2", "lee su versión (%s)" % r.get("version"))
    ok(r["mas_nueva"] is False, "y ve que no es más nueva que la del servidor")
    ok(r["activo_nombre"], "dice cómo se llama el mundo activo, para preguntarlo bien")
    ok(admin.post("/api/mundos/inspeccionar", json={"id": "zz"}).status_code == 400,
       "un id inventado se rechaza")

    # el mundo Gris entra de verdad: hace falta un mundo CON jugadores del que
    # traerlos en la prueba 11
    r = admin.post("/api/mundos/importar",
                   json={"id": "aaaa1111bbbb", "nombre": "Mundo Gris"}).get_json()
    ok(r["ok"] and esperar(admin)["estado"] == "listo", "y se puede importar de verdad")

    tmp = base / "crudo-FUT"; shutil.rmtree(tmp, ignore_errors=True)
    _pm.mundo_falso(tmp / "mundo", "FUTURO", ver_id=99999, ver="99.9")
    with zipfile.ZipFile(base / "futuro.zip", "w") as zz:
        for f in tmp.rglob("*"):
            if f.is_file():
                zz.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp, ignore_errors=True)
    sube(admin, base / "futuro.zip", "cccc2222dddd")
    r = admin.post("/api/mundos/inspeccionar", json={"id": "cccc2222dddd"}).get_json()
    ok(r["mas_nueva"] is True, "avisa de que un mundo de 99.9 es más nuevo")
    admin.post("/api/mundos/importar", json={"id": "cccc2222dddd", "nombre": "Futuro"})
    tr = esperar(admin)
    ok(tr["estado"] == "error" and "sin arrancar" in tr.get("mensaje", ""),
       "y el panel NO lo importa: explica por qué")

    z3 = _pm.zip_de_mundo(base / "descartame.zip", "DESCARTE")
    sube(admin, z3, "9999aaaa9999")
    ok((panel / "data" / "subidas" / "9999aaaa9999.part").exists(), "una subida deja su archivo")
    ok(admin.post("/api/mundos/descartar", json={"id": "9999aaaa9999"}).get_json()["ok"],
       "y cancelarla lo suelta")
    ok(not (panel / "data" / "subidas" / "9999aaaa9999.part").exists(),
       "el archivo ya no ocupa disco")

    # ── 10. reemplazar el mundo activo ───────────────────────────────────────
    titulo("10 · subir una versión nueva del mundo activo")
    (base / "render.log").unlink(missing_ok=True)
    _pm.mapa_falso(web, "ACTIVO")      # el mundo activo vuelve a tener mapa
    z2 = _pm.zip_de_mundo(base / "t2-v2.zip", "ACTIVO-V2")
    sube(admin, z2, "eeee3333ffff")
    antes = [m for m in admin.get("/api/mundos").get_json()["mundos"] if m["activo"]][0]
    r = admin.post("/api/mundos/importar",
                   json={"id": "eeee3333ffff", "modo": "reemplazar"}).get_json()
    ok(r["ok"], "acepta reemplazar")
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "lo hace bien (%s)" % tr.get("mensaje", "")[:60])
    ok(_pm.marca_de(mc / "world" / "marca.txt") == "ACTIVO-V2", "`world` es la versión nueva")
    ahora = [m for m in admin.get("/api/mundos").get_json()["mundos"] if m["activo"]][0]
    ok(ahora["slug"] == antes["slug"], "sigue siendo el mismo mundo")
    ok((web / "maps").is_dir(), "el mapa no se archivó")
    time.sleep(1)
    ok(_pm.render_log(base) and not any("--completo" in l for l in _pm.render_log(base)),
       "y el redibujado es incremental")
    ok(not list((panel / "data" / "subidas").glob("*.part")),
       "el archivo subido se borra al terminar")

    # ── 11. traer los jugadores ──────────────────────────────────────────────
    titulo("11 · traer los jugadores de otro mundo")
    u = _pm.JUGADORES[0]
    origen = "mundo-gris"
    ok(json.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "ACTIVO-V2",
       "de partida el activo tiene sus estadísticas")
    r = admin.post("/api/mundos/jugadores",
                   json={"origen": origen, "que": ["logros", "stats"]}).get_json()
    ok(r["ok"], "acepta")
    tr = esperar(admin)
    ok(tr["estado"] == "listo", "los trae (%s)" % tr.get("mensaje", "")[:70])
    ok(json.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "GRIS",
       "y las estadísticas del Gris pisan a las de aquí")
    ok(admin.post("/api/mundos/jugadores",
                  json={"origen": origen, "que": []}).status_code == 400,
       "no deja traer nada sin marcar")
    ok(admin.post("/api/mundos/jugadores",
                  json={"origen": "../x", "que": ["stats"]}).status_code == 400,
       "ni desde un mundo con ruta rara")
    ok(mod.post("/api/mundos/jugadores",
                json={"origen": origen, "que": ["stats"]}).status_code == 403,
       "y un mod no puede")

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
