#!/usr/bin/env python3
"""
Los seis interruptores de pestaña, y el traspaso de admin — POR DENTRO DEL
PANEL DE VERDAD.

Importa el `server.py` real, le apunta a un escenario de mentira y le habla por
HTTP con el cliente de pruebas de Flask. Lo que se comprueba es el panel que se
sube al servidor, no una maqueta parecida.

POR QUÉ ESTA PRUEBA
-------------------
Hasta ahora, seis pestañas eran «solo del admin» y eso estaba escrito a mano en
47 rutas distintas. Al convertirlas en permisos por persona, el riesgo no es que
algo deje de funcionar —eso se ve enseguida— sino que **una ruta se quede sin
comprobar nada** y no lo note nadie. Esconder una pestaña en el navegador no es
un permiso: quien sepa la dirección entra igual con `curl`. Ya pasó una vez, con
`must_change`, que solo lo forzaba un modal (claude/cuentas-y-seguridad.md).

Por eso, además de probar cada permiso, la §6 recorre TODAS las rutas del panel
y se planta si alguna no comprueba la sesión.

Correr:  python3 scripts/probar-permisos-pestanas.py
"""
import importlib.util, json, os, re, shutil, sys, tempfile, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent
sys.path.insert(0, str(AQUI))

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


# Una ruta de cada pestaña, la más barata de llamar. GET donde se pueda: lo que
# se prueba es el portero, no lo que hay detrás.
PUERTAS = {
    "backups":      ("get",  "/api/backups", None),
    "plugins":      ("get",  "/api/plugins", None),
    "moderadores":  ("get",  "/api/users", None),
    "mundo":        ("get",  "/api/mundos", None),
    "sistema":      ("get",  "/api/system/status", None),
}
# Lo que NO se puede encender por persona: sigue siendo solo del admin porque no
# está en la lista de seis. Si algún día alguien lo mete en un interruptor sin
# querer, esta prueba lo dice.
SOLO_ADMIN = [
    ("post", "/api/users/create", {"name": "x", "password": "12345678"}),
    ("post", "/api/users/update", {"name": "bicho", "perms": {}}),
    ("post", "/api/users/delete", {"name": "bicho"}),
    ("post", "/api/users/transfer_admin", {"name": "bicho"}),
    ("post", "/api/player/effects", {}),
    ("post", "/api/player/teleport", {}),
    # 🔴 la consola NO es un interruptor: es del admin y punto
    ("get",  "/api/console/tail", None),
    ("post", "/api/console/send", {"command": "list"}),
]


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-permisos-"))
    print("escenario en %s" % base)
    _pm.montar(base)
    mc, panel, web = base / "minecraft", base / "panel", base / "web"
    shutil.copy(REPO / "scripts" / "mundos.py", panel / "scripts" / "mundos.py")
    shutil.copy(REPO / "nbt.py", panel / "nbt.py")

    os.environ.update(
        MC_DIR=str(mc), PANEL_DIR=str(panel), BLUEMAP_WEB=str(web),
        ESTADO=str(base / "estado"), RENDER_LOG=str(base / "render.log"),
        PATH="%s:%s" % (base / "bin", os.environ["PATH"]),
        PANEL_SECRET="secreto-de-prueba-0123456789")

    s = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(s)
    s.loader.exec_module(srv)                       # ← el server.py DE VERDAD

    def cuentas(**extra):
        base_u = {"jey": {"hash": srv.hash_pw("contraseñalarga"), "role": "admin",
                          "perms": {}, "must_change": False},
                  "bicho": {"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                            "perms": {}, "must_change": False},
                  "mirona": {"hash": srv.hash_pw("contraseñalarga"), "role": "viewer",
                             "perms": {}, "must_change": False}}
        for n, v in extra.items():
            base_u[n] = v
        srv.save_users(base_u)

    cuentas()
    srv.app.config["TESTING"] = True

    def cliente(quien, csrf=True):
        c = srv.app.test_client()
        if quien:
            c.set_cookie("panel_session",
                         srv.sign("%s|%d" % (quien, time.time() + 3600)),
                         domain="localhost")
        if csrf:
            c.environ_base["HTTP_X_PANEL"] = "1"
        return c

    def llama(c, puerta):
        metodo, ruta, cuerpo = puerta
        return (c.get(ruta) if metodo == "get"
                else c.post(ruta, json=cuerpo or {})).status_code

    admin, mod, fuera = cliente("jey"), cliente("bicho"), cliente(None)

    # ── 1 · de partida, un moderador no tiene ninguna ────────────────────
    titulo("1 · las pestañas nacen apagadas")
    for perm, puerta in PUERTAS.items():
        ok(llama(mod, puerta) == 403,
           "un moderador recién hecho NO entra en «%s»" % perm)
    ok(llama(fuera, PUERTAS["mundo"]) == 401, "y sin sesión es 401, no 403")
    for perm, puerta in PUERTAS.items():
        ok(llama(admin, puerta) in (200, 400, 500),
           "el admin sigue entrando en «%s» (%d)" % (perm, llama(admin, puerta)))

    # ── 2 · encender una NO enciende las demás ───────────────────────────
    # Es lo que separa «varios interruptores» de «un interruptor de admin».
    titulo("2 · cada interruptor abre SOLO su pestaña")
    for perm in PUERTAS:
        cuentas(pepe={"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                      "perms": {perm: True}, "must_change": False})
        pepe = cliente("pepe")
        ok(llama(pepe, PUERTAS[perm]) in (200, 400, 500),
           "con «%s» entra en su pestaña" % perm)
        otras = [p for p in PUERTAS if p != perm]
        cerradas = [p for p in otras if llama(pepe, PUERTAS[p]) == 403]
        ok(len(cerradas) == len(otras),
           "y sigue fuera de las demás (%d/%d)" % (len(cerradas), len(otras)))

    # ── 3 · «moderadores» es de MIRAR ────────────────────────────────────
    # Si no, el interruptor no sería un interruptor: quien lo tuviera podría
    # encenderse los demás y quedarse el panel.
    titulo("3 · «moderadores» deja mirar, no tocar")
    cuentas(pepe={"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                  "perms": {"moderadores": True}, "must_change": False})
    pepe = cliente("pepe")
    ok(pepe.get("/api/users").status_code == 200, "ve la lista de cuentas")
    ok(pepe.get("/api/audit").status_code == 200, "y el registro de actividad")
    r = pepe.get("/api/users").get_json()
    ok(r.get("puede_gestionar") is False,
       "y el panel le dice que no manda, para no enseñarle botones que dan 403")
    ok(srv.jsonify and r.get("perms_pestana") == srv.PERMS_PESTANA,
       "la lista de interruptores la manda el backend, no la inventa la web")
    negadas = [ruta for metodo, ruta, cuerpo in SOLO_ADMIN
               if llama(pepe, (metodo, ruta, cuerpo)) == 403]
    ok(len(negadas) == len(SOLO_ADMIN),
       "no puede crear, borrar, cambiar permisos ni traspasar (%d/%d)"
       % (len(negadas), len(SOLO_ADMIN)))
    # y en concreto: no puede encenderse a sí mismo el resto
    pepe.post("/api/users/update", json={"name": "pepe", "perms": {"sistema": True}})
    ok(srv.load_users()["pepe"]["perms"].get("sistema") is not True,
       "y sobre todo NO puede encenderse a sí mismo «sistema»")

    # ── 4 · el traspaso de admin ─────────────────────────────────────────
    titulo("4 · traspasar el admin")
    cuentas()
    admin, mod = cliente("jey"), cliente("bicho")
    ok(admin.post("/api/users/transfer_admin",
                  json={"name": "mirona"}).status_code == 400,
       "no se le puede dar a un observador, solo a un moderador")
    ok(admin.post("/api/users/transfer_admin",
                  json={"name": "nadie"}).status_code == 404,
       "ni a una cuenta que no existe")
    ok(admin.post("/api/users/transfer_admin",
                  json={"name": "jey"}).status_code == 400,
       "ni a uno mismo")
    ok(mod.post("/api/users/transfer_admin",
                json={"name": "bicho"}).status_code == 403,
       "y un moderador no puede quedarse el panel llamándola él")
    ok(cliente("jey", csrf=False).post(
        "/api/users/transfer_admin", json={"name": "bicho"}).status_code == 403,
       "sin la cabecera X-Panel tampoco (escudo anti-CSRF)")
    ok(srv.load_users()["jey"]["role"] == "admin",
       "después de los cinco intentos fallidos, todo sigue igual")

    r = admin.post("/api/users/transfer_admin", json={"name": "bicho"})
    ok(r.status_code == 200, "el admin sí puede (%d)" % r.status_code)
    users = srv.load_users()
    ok(users["bicho"]["role"] == "admin", "bicho es el admin")
    ok(users["jey"]["role"] == "mod", "y jey ha bajado a moderador")
    ok([n for n, v in users.items() if v["role"] == "admin"] == ["bicho"],
       "queda EXACTAMENTE un admin, ni cero ni dos")
    # sin elegir nada, no se conserva nada: las claves quedan puestas pero en
    # False, que es lo que hace que NO hereden los valores por defecto del rol
    ok(not any(users["jey"]["perms"].values()),
       "sin marcar nada, el que baja no conserva ni un permiso")
    ok(users["jey"]["perms"].get("ban") is False,
       "y queda escrito que NO, no simplemente ausente — si no, heredaría el "
       "valor por defecto de moderador y el aviso habría mentido")

    titulo("5 · y el cambio es de verdad, no solo en la ficha")
    jey_ahora = cliente("jey")
    cerradas = [p for p in PUERTAS if llama(jey_ahora, PUERTAS[p]) == 403]
    ok(len(cerradas) == len(PUERTAS),
       "el ex-admin ya no entra en ninguna pestaña (%d/%d)"
       % (len(cerradas), len(PUERTAS)))
    ok(jey_ahora.post("/api/users/transfer_admin",
                      json={"name": "bicho"}).status_code == 403,
       "y no puede recuperarlo él solo — que es justo lo que avisa el modal")
    ok(llama(cliente("bicho"), PUERTAS["sistema"]) in (200, 400, 500),
       "el nuevo admin entra en todo")


    # ── 5b · la consola es del admin, y funciona ─────────────────────────
    # 🔴 Dos cosas distintas y las dos importan: que NADIE que no sea admin
    # entre —ni con `view_console` escrito a mano en users.json, que es
    # exactamente lo que quedaría de una instalación anterior— y que al admin
    # le siga funcionando de verdad, no solo que le devuelva 200.
    titulo("5b · la consola")
    cuentas(colado={"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                    "perms": {"view_console": True, "moderadores": True},
                    "must_change": False})
    colado = cliente("colado")
    ok(colado.get("/api/console/tail").status_code == 403,
       "un moderador con view_console puesto A MANO tampoco ve la consola")
    ok(colado.post("/api/console/send", json={"command": "list"}).status_code == 403,
       "ni puede escribir en ella — que es poder `op`-earse")
    ok("view_console" not in srv.ALL_PERMS and "view_console" not in srv.PERMS_PESTANA,
       "y no sale como interruptor en ningún sitio")
    r = colado.get("/api/users").get_json()
    ok("view_console" not in (r.get("conservables") or []),
       "tampoco entre lo que se puede conservar al traspasar el admin")

    jefe = cliente("jey")
    (mc / "logs").mkdir(parents=True, exist_ok=True)
    (mc / "logs" / "latest.log").write_text(
        "[10:00:00] [Server thread/INFO]: Done (9.9s)!\n"
        "[10:00:01] [Server thread/INFO]: JEYtheFlash joined the game\n")
    rt = jefe.get("/api/console/tail")
    lineas = rt.get_json().get("lines") or []
    ok(rt.status_code == 200 and any("joined the game" in l for l in lineas),
       "el admin ve la consola en vivo, con las líneas del log de verdad (%d)"
       % len(lineas))
    rs = jefe.post("/api/console/send", json={"command": "list"})
    ok(rs.status_code == 200 and "output" in (rs.get_json() or {}),
       "y puede mandar una orden (sin RCON de verdad, contesta que no alcanza)")
    ok(any("console: list" in l for l in (srv.AUDIT_F.read_text().splitlines()
                                          if srv.AUDIT_F.exists() else [])),
       "y la orden queda apuntada en el registro, con quién la mandó")
    ok(jefe.post("/api/console/send", json={"command": "  "}).status_code == 400,
       "una orden vacía se rechaza, no se manda")

    # ── 5c · al traspasar, se elige con qué te quedas ────────────────────
    titulo("5c · qué conserva quien traspasa")
    cuentas()
    jefe = cliente("jey")
    r = jefe.post("/api/users/transfer_admin",
                  json={"name": "bicho", "rol": "mod",
                        "perms": {"ban": True, "kick": False, "mundo": True,
                                  "view_console": True}})
    ok(r.status_code == 200, "traspasa conservando lo que marcó (%d)" % r.status_code)
    yo = srv.load_users()["jey"]
    ok(yo["role"] == "mod", "sigue de moderador")
    ok(yo["perms"].get("ban") is True and yo["perms"].get("kick") is False,
       "conserva lo marcado y pierde lo desmarcado")
    ok(yo["perms"].get("mundo") is True,
       "y puede conservar una pestaña si la marca (aquí, Mundo)")
    ok(yo["perms"].get("view_console") is not True,
       "🔴 pero la consola NO, aunque la pida expresamente en la petición")
    ok(cliente("jey").get("/api/console/tail").status_code == 403,
       "y se comprueba de verdad: la consola le da 403")
    ok(cliente("jey").get("/api/mundos").status_code == 200,
       "mientras que Mundo, que sí marcó, le sigue abriendo")

    titulo("5d · o dejarlo del todo y ser un jugador normal")
    cuentas()
    r = cliente("jey").post("/api/users/transfer_admin",
                            json={"name": "bicho", "rol": "viewer",
                                  "perms": {"ban": True, "mundo": True}})
    ok(r.status_code == 200 and r.get_json().get("mi_rol") == "viewer",
       "se puede bajar a observador de una vez")
    yo = srv.load_users()["jey"]
    ok(yo["role"] == "viewer", "queda como observador")
    ok(not any(yo["perms"].values()),
       "y sin permisos de moderador, aunque los hubiera marcado antes de cambiar de idea")
    ex = cliente("jey")
    ok(ex.get("/api/mundos").status_code == 403 and
       ex.get("/api/console/tail").status_code == 403,
       "ni mundos ni consola")
    ok(ex.get("/api/status").status_code in (200, 500),
       "pero entra al panel a mirar, que es lo que es un observador")

    titulo("5e · un rol inventado no cuela")
    cuentas()
    ok(cliente("jey").post("/api/users/transfer_admin",
                           json={"name": "bicho", "rol": "admin"}).status_code == 400,
       "no puedes quedarte de admin «también» (ni inventarte un rol)")
    ok(srv.load_users()["jey"]["role"] == "admin", "y no ha pasado nada")

    # ── 6 · que no se haya quedado ninguna puerta abierta ────────────────
    titulo("6 · ninguna ruta del panel sin portero")
    # Públicas a propósito: entrar, salir, saber quién soy y el nombre del
    # servidor (que se enseña en la pantalla de login).
    PUBLICAS = {"/api/login", "/api/login2", "/api/pin", "/api/logout",
                "/api/me", "/api/branding"}
    texto = (REPO / "server.py").read_text()
    trozos = re.split(r'(?m)^(?=@app\.(?:post|get|route)\()', texto)
    sin_portero = []
    for tr in trozos:
        m = re.match(r'@app\.(?:post|get|route)\("(/api/[^"]+)"', tr)
        if not m or m.group(1) in PUBLICAS:
            continue
        cuerpo = tr.split("\n", 1)[1] if "\n" in tr else ""
        cuerpo = re.split(r'(?m)^@app\.', cuerpo)[0]
        if not re.search(r"\b(require|_require_tab|_require_admin"
                         r"|_require_admin_ro|do_cmd|current_user)\b", cuerpo):
            sin_portero.append(m.group(1))
    ok(not sin_portero,
       "las %d rutas comprueban la sesión%s"
       % (len([t for t in trozos if t.startswith("@app.")]),
          "" if not sin_portero else " — SE HAN QUEDADO SUELTAS: %s" % sin_portero))

    # y que el refactor no haya dejado ningún «solo admin» a medias en las
    # rutas que ahora son de pestaña
    for perm, (_m, ruta, _c) in PUERTAS.items():
        tr = next((t for t in trozos
                   if re.match(r'@app\.(?:post|get|route)\("%s"' % re.escape(ruta), t)), "")
        # `_require_tab("x")` es el portero nuevo; `require("x")` es el de
        # siempre y vale igual — `backups` y la consola ya eran permisos antes
        # de todo esto y no hacía falta tocarlos. Lo que NO puede quedar es un
        # `u["role"] != "admin"` suelto, que es lo que se está buscando.
        ok('_require_tab("%s")' % perm in tr or 'require("%s")' % perm in tr,
           "%s pasa por el portero de «%s», no por un chequeo de rol suelto"
           % (ruta, perm))
        ok('!= "admin"' not in tr,
           "y ya no le queda ningún chequeo de rol a mano (%s)" % ruta)

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
