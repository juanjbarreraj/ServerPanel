#!/usr/bin/env python3
"""
La muerte de una mascota, ya dentro del panel: cómo llega a la Historia y quién
puede ver dónde pasó.

POR QUÉ ASÍ
-----------
`probar-mascotas.py` comprueba el censo —quién hay y quién ha dejado de estar—.
Esto comprueba lo de después, que es donde se puede colar un fallo de los que no
se ven: que las coordenadas de la muerte se le enseñen a quien no debe.

Dónde se muere una mascota es dónde quedaron sus cosas y, muchas veces, las de
su dueño. La regla es la misma que con las muertes de personas: la ven el dueño
y los moderadores, y nadie más. Aquí se comprueba con tres sesiones distintas.

Se importa el `server.py` REAL y se le habla por HTTP con el cliente de pruebas
de Flask.

Correr:  python3 scripts/probar-mascotas-feed.py
"""
import importlib.util, json, os, shutil, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

fallos, pasadas = [], 0
DUEÑO_UUID = "aaaaaaaa-1111-2222-3333-444444444444"
OTRO_UUID = "bbbbbbbb-1111-2222-3333-444444444444"


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
    base = Path(tempfile.mkdtemp(prefix="probar-masc-feed-"))
    mc, panel = base / "minecraft", base / "panel"
    (panel / "data").mkdir(parents=True, exist_ok=True)
    (mc / "logs").mkdir(parents=True, exist_ok=True)
    (mc / "world/players/stats").mkdir(parents=True, exist_ok=True)
    (mc / "world/players/data").mkdir(parents=True, exist_ok=True)
    (mc / "world/dimensions/minecraft/overworld/entities").mkdir(parents=True, exist_ok=True)
    # El panel busca el censo dentro de su propia carpeta de scripts, igual que
    # hace con mundos.py: si no está, la Historia se queda sin muertes de mascota
    # y hay que verlo en rojo, no adivinarlo.
    (panel / "scripts").mkdir(parents=True, exist_ok=True)
    for n in ("mascotas.py", "buscar-entidad.py"):
        shutil.copy(REPO / "scripts" / n, panel / "scripts" / n)
    shutil.copy(REPO / "nbt.py", panel / "nbt.py")
    print("escenario en %s" % base)

    (mc / "usercache.json").write_text(json.dumps([
        {"uuid": DUEÑO_UUID, "name": "Tazzk93"},
        {"uuid": OTRO_UUID, "name": "sofidiaz"}]))
    (mc / "whitelist.json").write_text((mc / "usercache.json").read_text())
    (mc / "logs/latest.log").write_text("[10:00:00] [Server thread/INFO]: Done (1s)!\n")

    # mensajes.json mínimo pero con la forma real, incluidos los nombres de
    # bichos que ahora saca build-mensajes.py del jar.
    (panel / "data/mensajes.json").write_text(json.dumps({
        "version": "26.3",
        "muertes": [{"clave": "death.attack.mob", "n": 2,
                     "rx": r"^([A-Za-z0-9_]{1,16}) was slain by (.+)$",
                     "en": "%1$s was slain by %2$s", "es": "%1$s fue asesinado por %2$s"}],
        "sesion": {"entro": {"rx": r"^([A-Za-z0-9_]{1,16}) joined the game$",
                             "en": "%s joined the game", "es": "%s se unió"},
                   "salio": {"rx": r"^([A-Za-z0-9_]{1,16}) left the game$",
                             "en": "%s left the game", "es": "%s se fue"}},
        "logros": {"tarea": {"rx": r"^([A-Za-z0-9_]{1,16}) has made the advancement (.+)$",
                             "en": "%s has made the advancement %s", "es": "%s logró %s"}},
        "efectos": [],
        "bichos": {"wolf": {"en": "Wolf", "es": "Lobo"},
                   "parrot": {"en": "Parrot", "es": "Loro"}},
    }, ensure_ascii=False))

    os.environ.update(MC_DIR=str(mc), PANEL_DIR=str(panel),
                      PANEL_SECRET="secreto-de-prueba-0123456789")
    spec = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)               # ← el server.py DE VERDAD

    srv.save_users({
        "jey": {"hash": srv.hash_pw("contraseñalarga"), "role": "admin",
                "perms": {}, "must_change": False},
        # la cuenta del dueño se llama igual que su nombre de Minecraft: así es
        # como el panel sabe que la muerte es «suya»
        "Tazzk93": {"hash": srv.hash_pw("contraseñalarga"), "role": "viewer",
                    "perms": {}, "must_change": False},
        "sofidiaz": {"hash": srv.hash_pw("contraseñalarga"), "role": "viewer",
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

    admin, dueño, otro = cliente("jey"), cliente("Tazzk93"), cliente("sofidiaz")

    titulo("1 · el censo mete la muerte en la Historia")
    srv._feed_mascotas([
        {"n": "Fido", "tipo": "wolf", "dueño": DUEÑO_UUID, "dim": "overworld",
         "pos": [120, 70, -340], "t": time.time() - 60},
        {"n": None, "tipo": "parrot", "dueño": DUEÑO_UUID, "dim": "nether",
         "pos": [5, 40, 9], "t": time.time() - 30},
    ])
    ev = admin.get("/api/feed?limite=20").get_json()["eventos"]
    muertes = [e for e in ev if e["k"] == "mobmuerto"]
    ok(len(muertes) == 2, "salen las dos muertes (%d)" % len(muertes))
    fido = next((e for e in muertes if e.get("victima") == "Fido"), None)
    ok(fido is not None, "con el nombre del nametag")
    ok(fido and fido.get("censo") is True,
       "marcadas como del censo, para no decir que alguien las mató")
    ok(fido and fido.get("p") == "Tazzk93", "y se sabe de quién era el lobo")
    ok(fido and fido.get("u") == DUEÑO_UUID, "con su uuid, para la carita")

    titulo("2 · el nombre de la especie sale del jar, no del navegador")
    ok(fido and fido.get("bicho_es") == "Lobo", "«wolf» se muestra como «Lobo»")
    ok(fido and fido.get("bicho_en") == "Wolf", "y como «Wolf» en inglés")
    loro = next((e for e in muertes if e.get("bicho") == "parrot"), None)
    ok(loro is not None and loro.get("victima") is None,
       "una mascota SIN nombre también sale, con su especie")
    ok(loro and loro.get("bicho_es") == "Loro", "traducida igual")
    ok(loro and loro.get("dim") == "minecraft:the_nether",
       "y la dimensión en el formato que ya usa el resto del feed: %r"
       % (loro or {}).get("dim"))

    titulo("3 · dónde murió lo ve quien debe")
    def coords(c):
        e = next(x for x in c.get("/api/feed?limite=20").get_json()["eventos"]
                 if x["k"] == "mobmuerto" and x.get("victima") == "Fido")
        return e
    a, d, o = coords(admin), coords(dueño), coords(otro)
    ok(a.get("x") == 120, "el admin ve las coordenadas")
    ok(d.get("x") == 120, "el dueño también: era su lobo")
    ok(o.get("x") is None, "quien no tiene nada que ver, NO")
    ok(o.get("lugar_oculto") is True,
       "y se le dice que hay un sitio, no que no lo hubiera")
    ok("y" not in o and "z" not in o, "no se cuela ni la Y ni la Z")

    titulo("4 · lo que ya dijo el datapack no se repite")
    # Los dos caminos ven la misma muerte: si a un bicho con nombre lo mata un
    # jugador, el datapack de vigilancia lo anuncia al instante y el censo lo
    # echa de menos unos minutos después. Saldría dos veces, y la segunda sin
    # decir quién fue.
    ahora = time.time()
    srv._añadir(srv.FEED_F, [{"t": ahora - 300, "k": "mobmuerto", "p": "sofidiaz",
                              "u": OTRO_UUID, "victima": "Rex"}])
    srv._feed_mascotas([{"n": "Rex", "tipo": "wolf", "dueño": DUEÑO_UUID,
                         "dim": "overworld", "pos": [1, 2, 3], "t": ahora}])
    ev = admin.get("/api/feed?limite=50").get_json()["eventos"]
    rex = [e for e in ev if e.get("victima") == "Rex"]
    ok(len(rex) == 1, "Rex sale una sola vez (%d)" % len(rex))
    ok(rex and rex[0].get("p") == "sofidiaz",
       "y se queda la del datapack, que SÍ sabe quién lo mató")

    # pero una muerte vieja del mismo nombre no tapa una nueva
    srv._feed_mascotas([{"n": "Rex", "tipo": "wolf", "dueño": DUEÑO_UUID,
                         "dim": "overworld", "pos": [1, 2, 3],
                         "t": ahora + srv.MASCOTAS_VENTANA + 600}])
    ev = admin.get("/api/feed?limite=50").get_json()["eventos"]
    ok(len([e for e in ev if e.get("victima") == "Rex"]) == 2,
       "otro Rex meses después sí se cuenta: no es la misma muerte")

    srv._feed_mascotas([{"n": None, "tipo": "wolf", "dueño": DUEÑO_UUID,
                         "dim": "overworld", "pos": [1, 2, 3], "t": ahora}])
    ev = admin.get("/api/feed?limite=50").get_json()["eventos"]
    ok(len([e for e in ev if e["k"] == "mobmuerto" and not e.get("victima")]) == 2,
       "y una mascota sin nombre nunca se descarta: el datapack no puede verla")

    titulo("5 · el panel sabe encontrar y vigilar el censo")
    m = srv.mascotas_mod()
    ok(m is not None, "carga scripts/mascotas.py")
    ok(m is None or str(m.MC) == str(mc),
       "y lo apunta al MC_DIR del panel, no al de por defecto: %s"
       % (m and m.MC))
    ok(m is None or str(m.ESTADO_F).startswith(str(panel)),
       "el censo se guarda en data/ del panel")
    srv._salud("mascotas", ok=True)
    fila = next((x for x in srv._automatismos() if x["id"] == "mascotas"), None)
    ok(fila is not None, "y sale en «lo que se actualiza solo» de Sistema")
    ok(fila and fila["ok"], "en verde cuando acaba de correr")
    srv._salud("mascotas", ok=False, nota="algo se rompió")
    fila = next(x for x in srv._automatismos() if x["id"] == "mascotas")
    ok(not fila["ok"] and "rompió" in fila["nota"], "y en rojo, con el motivo, si falla")

    titulo("6 · una pasada de verdad contra un mundo vacío no inventa muertes")
    # Sin ficheros de entidades no hay censo posible: lo que no puede pasar es
    # que eso se cuente como que se murieron todas.
    antes = len(admin.get("/api/feed?limite=50").get_json()["eventos"])
    r = m.pasada()
    ok(not r["muertas"], "nada dado por muerto (%d)" % len(r["muertas"]))
    ok(len(admin.get("/api/feed?limite=50").get_json()["eventos"]) == antes,
       "y la Historia se queda igual")


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
