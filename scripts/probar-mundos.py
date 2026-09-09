#!/usr/bin/env python3
"""
Prueba de mundos.py con mundos de mentira.

Monta en una carpeta temporal un ~/minecraft, un ~/panel y un /var/www/bluemap-web
falsos, y unos `screen`, `pgrep` y `sudo` de pega que fingen un servidor que se
para y arranca. Después corre el mundos.py DE VERDAD, por línea de órdenes, y
comprueba que:

  · la copia se hace con el guardado pausado (save-off … save-on)
  · importar un zip crea un mundo nuevo y rechaza rutas con ..
  · cambiar mueve el mundo Y se lleva el mapa y los marcadores con él
  · volver atrás devuelve los azulejos originales, sin redibujar entero
  · borrar no borra: mueve a la papelera
  · un mundo nuevo deja que el servidor lo genere

Correr:  python3 scripts/probar-mundos.py
"""
import json, os, shutil, subprocess, sys, tempfile, time, zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
MUNDOS_PY = AQUI / "mundos.py"

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


# ─────────────────────────────────────────────────────── el escenario de mentira
def escribir(p: Path, texto):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto)


# Los level.dat y los playerdata son NBT DE VERDAD, no texto: mundos.py los lee
# para saber la versión del mundo y para reubicar a los jugadores en el spawn,
# y con ficheros de mentira esas dos cosas no se probarían.
import importlib.util as _iu
_spec = _iu.spec_from_file_location("nbt", AQUI.parent / "nbt.py")
nbt = _iu.module_from_spec(_spec); _spec.loader.exec_module(nbt)

JUGADORES = ["11111111-1111-1111-1111-111111111111",
             "22222222-2222-2222-2222-222222222222"]


def comp(pares):
    return nbt.Tag(nbt.TAG_COMPOUND, [[k.encode(), v] for k, v in pares])


def level_dat(destino: Path, ver_id=4189, ver="26.2", spawn=(100, 70, -200)):
    raiz = comp([("Data", comp([
        ("Version", comp([("Id", nbt.Tag(nbt.TAG_INT, ver_id)),
                          ("Name", nbt.Tag(nbt.TAG_STRING, ver.encode()))])),
        ("DataVersion", nbt.Tag(nbt.TAG_INT, ver_id)),
        ("SpawnX", nbt.Tag(nbt.TAG_INT, spawn[0])),
        ("SpawnY", nbt.Tag(nbt.TAG_INT, spawn[1])),
        ("SpawnZ", nbt.Tag(nbt.TAG_INT, spawn[2])),
        ("LevelName", nbt.Tag(nbt.TAG_STRING, b"world")),
    ]))])
    destino.parent.mkdir(parents=True, exist_ok=True)
    nbt.save(str(destino), b"", raiz, gz=True)


def player_dat(destino: Path, pos, xp=17):
    raiz = comp([
        ("Pos", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_DOUBLE, [float(p) for p in pos]))),
        ("Motion", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_DOUBLE, [1.0, 2.0, 3.0]))),
        ("Rotation", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_FLOAT, [9.0, 9.0]))),
        ("Dimension", nbt.Tag(nbt.TAG_STRING, b"minecraft:the_nether")),
        ("XpLevel", nbt.Tag(nbt.TAG_INT, xp)),
        ("SpawnX", nbt.Tag(nbt.TAG_INT, 5555)),
        ("LastDeathLocation", comp([("y", nbt.Tag(nbt.TAG_INT, 12))])),
        ("Inventory", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_COMPOUND, []))),
    ])
    destino.parent.mkdir(parents=True, exist_ok=True)
    nbt.save(str(destino), b"", raiz, gz=True)


def leer_player(p: Path):
    _, raiz, _ = nbt.load(str(p))
    d = {}
    for clave in ("Pos", "Dimension", "XpLevel", "SpawnX", "LastDeathLocation", "Motion"):
        t = nbt.cget(raiz.v, clave)
        d[clave] = None if t is None else (t.v.items if hasattr(t.v, "items") else t.v)
    return d


def mundo_falso(raiz: Path, marca, ver_id=4189, ver="26.2", pos=(1.5, 2.5, 3.5)):
    """Un mundo con la pinta justa: level.dat, regiones y datos de jugador."""
    escribir(raiz / "marca.txt", "mundo de %s" % marca)
    level_dat(raiz / "level.dat", ver_id=ver_id, ver=ver)
    escribir(raiz / "region" / "r.0.0.mca", "region 0,0 de %s" % marca)
    escribir(raiz / "region" / "r.1.0.mca", "region 1,0 de %s" % marca)
    escribir(raiz / "entities" / "r.0.0.mca", "entidades de %s" % marca)
    for u in JUGADORES:
        player_dat(raiz / "playerdata" / (u + ".dat"), pos)
        escribir(raiz / "stats" / (u + ".json"), '{"marca":"%s"}' % marca)
        escribir(raiz / "advancements" / (u + ".json"), '{"marca":"%s"}' % marca)
    escribir(raiz / "session.lock", "bloqueo")
    return raiz


def mapa_falso(web: Path, marca):
    escribir(web / "maps" / "overworld" / "tiles" / "0" / "x0" / "z0.png", "tile %s" % marca)
    escribir(web / "maps" / "overworld" / "settings.json", '{"marca":"%s"}' % marca)
    escribir(web / "settings.json", '{"maps":["overworld"]}')
    escribir(web / "index.html", "<html><head></head></html>")


def montar(base: Path):
    mc, panel, web, bin_ = base / "minecraft", base / "panel", base / "web", base / "bin"
    estado = base / "estado"
    mundo_falso(mc / "world", "ORIGINAL")
    escribir(mc / "server.properties",
             "#Minecraft server properties\nlevel-name=world\nlevel-seed=\nmotd=hola\n")
    mapa_falso(web, "ORIGINAL")
    escribir(panel / "data" / "markers.json", '{"marca":"ORIGINAL"}')
    escribir(panel / "data" / "structures.json", '{"marca":"ORIGINAL"}')
    escribir(panel / "data" / "structures-cache.json", '{"v":2,"marca":"ORIGINAL"}')
    escribir(panel / "data" / "users.json", '{"comun":"no es de ningun mundo"}')

    # ── render-mapa.sh de pega: solo apunta con qué lo llamaron
    escribir(panel / "scripts" / "render-mapa.sh",
             '#!/bin/bash\necho "$*" >> "$RENDER_LOG"\n')
    os.chmod(panel / "scripts" / "render-mapa.sh", 0o755)

    # ── el servidor de pega: un fichero dice si está vivo
    escribir(estado / "vivo", "1")
    level_dat(estado / "plantilla-level.dat")
    bin_.mkdir(parents=True, exist_ok=True)

    # pgrep: "vivo" si el fichero está
    escribir(bin_ / "pgrep", """#!/bin/bash
[ -f "$ESTADO/vivo" ] && { echo 4242; exit 0; }
exit 1
""")
    # screen: apunta la orden; `stop` mata al servidor
    escribir(bin_ / "screen", """#!/bin/bash
orden="$*"
echo "$orden" >> "$ESTADO/consola.log"
case "$orden" in *stop*) rm -f "$ESTADO/vivo" ;; esac
exit 0
""")
    # sudo systemctl restart: revive, y genera el mundo si no hay ninguno
    escribir(bin_ / "sudo", """#!/bin/bash
args="$*"
echo "$args" >> "$ESTADO/sudo.log"
case "$args" in
  *"systemctl restart"*)
    if [ ! -d "$MC_DIR/world" ]; then
      mkdir -p "$MC_DIR/world/region"
      echo "mundo de GENERADO" > "$MC_DIR/world/marca.txt"
      cp "$ESTADO/plantilla-level.dat" "$MC_DIR/world/level.dat"
      echo "region generada" > "$MC_DIR/world/region/r.0.0.mca"
    fi
    touch "$ESTADO/vivo"
    ;;
esac
exit 0
""")
    for n in ("pgrep", "screen", "sudo"):
        os.chmod(bin_ / n, 0o755)
    return mc, panel, web, bin_, estado


def correr(base, *args, espera_ok=True):
    mc, panel, web, bin_, estado = (base / "minecraft", base / "panel", base / "web",
                                    base / "bin", base / "estado")
    env = dict(os.environ)
    env.update(MC_DIR=str(mc), PANEL_DIR=str(panel), BLUEMAP_WEB=str(web),
               ESTADO=str(estado), RENDER_LOG=str(base / "render.log"),
               PATH="%s:%s" % (bin_, os.environ["PATH"]))
    r = subprocess.run([sys.executable, str(MUNDOS_PY)] + list(args) + ["--json"],
                       capture_output=True, text=True, env=env, timeout=300)
    try:
        datos = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        datos = {"ok": False, "mensaje": "(sin JSON) " + r.stdout[-400:] + r.stderr[-400:]}
    if espera_ok and not datos.get("ok"):
        print("     ↳ salida: %s" % datos.get("mensaje"))
    return datos


def zip_de_mundo(destino: Path, marca, dentro_de_carpeta=True):
    tmp = destino.parent / ("crudo-" + marca)
    shutil.rmtree(tmp, ignore_errors=True)
    raiz = tmp / ("Mi mundo %s" % marca) if dentro_de_carpeta else tmp
    mundo_falso(raiz, marca)
    with zipfile.ZipFile(destino, "w") as z:
        for f in tmp.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp, ignore_errors=True)
    return destino


def marca_de(p: Path):
    """La marca que lleva dentro un fichero de mentira. Se limpia el salto de
    línea: los ficheros que escribe el `sudo` de pega salen de un `echo`."""
    try:
        return p.read_text().strip().rsplit(" ", 1)[-1]
    except Exception:
        return None


def render_log(base):
    f = base / "render.log"
    return f.read_text().split("\n") if f.exists() else []


def consola(base):
    f = base / "estado" / "consola.log"
    return f.read_text() if f.exists() else ""


# ────────────────────────────────────────────────────────────────── las pruebas
def main():
    base = Path(tempfile.mkdtemp(prefix="probar-mundos-"))
    print("escenario en %s" % base)
    montar(base)
    mc, panel, web = base / "minecraft", base / "panel", base / "web"

    # ── 1. listar de arranque ────────────────────────────────────────────────
    titulo("1 · listar, recién montado")
    d = correr(base, "listar")
    ok(d.get("ok"), "listar responde")
    ms = d.get("mundos", [])
    ok(len(ms) == 1, "hay 1 mundo (había %d)" % len(ms))
    ok(ms and ms[0]["activo"], "y está marcado como activo")
    ok(ms and ms[0]["tiene_mapa"], "dice que tiene mapa")

    # ── 2. copia consistente ─────────────────────────────────────────────────
    titulo("2 · copia del mundo activo")
    tgz = base / "descarga.tar.gz"
    d = correr(base, "copia", str(tgz))
    ok(d.get("ok"), "la copia sale bien")
    ok(tgz.exists() and tgz.stat().st_size > 0, "el .tar.gz existe y pesa algo")
    r = subprocess.run(["tar", "tzf", str(tgz)], capture_output=True, text=True)
    ok("world/level.dat" in r.stdout, "dentro está world/level.dat")
    ok("world/region/r.0.0.mca" in r.stdout, "y las regiones")
    log = consola(base)
    ok("save-off" in log and "save-on" in log, "pausó y reanudó el guardado")
    ok(log.index("save-off") < log.index("save-on"), "en ese orden")
    ok((base / "estado" / "vivo").exists(), "el servidor siguió vivo (no se para para copiar)")

    # ── 3. importar ──────────────────────────────────────────────────────────
    titulo("3 · importar un mundo subido")
    z = zip_de_mundo(base / "subido.zip", "AZUL")
    d = correr(base, "importar", str(z), "--nombre", "Mundo Azul")
    ok(d.get("ok"), "importa sin quejarse")
    slug_azul = d.get("slug")
    ok(slug_azul == "mundo-azul", "el slug sale del nombre (%s)" % slug_azul)
    ok((mc / "mundos" / slug_azul / "mundo" / "level.dat").exists(),
       "encontró el level.dat aunque venía dentro de otra carpeta")
    ok(not (mc / "mundos" / slug_azul / "mundo" / "session.lock").exists(),
       "quitó el session.lock del otro servidor")
    d = correr(base, "listar")
    ok(len(d.get("mundos", [])) == 2, "ahora se listan 2 mundos")
    azul = [m for m in d["mundos"] if m["slug"] == slug_azul][0]
    ok(not azul["activo"] and not azul["tiene_mapa"], "el nuevo ni es activo ni tiene mapa")

    # ── 4. importar cosas que no valen ───────────────────────────────────────
    titulo("4 · lo que hay que rechazar")
    malo = base / "malo.zip"
    with zipfile.ZipFile(malo, "w") as zz:
        zz.writestr("../../fuera.txt", "esto no debería salir de la carpeta")
        zz.writestr("level.dat", "x")
    d = correr(base, "importar", str(malo), "--nombre", "Malo", espera_ok=False)
    ok(not d.get("ok"), "rechaza un zip con rutas ..")
    ok(not (base / "fuera.txt").exists() and not (mc / "fuera.txt").exists(),
       "y no escribió nada fuera")
    nada = base / "nada.zip"
    with zipfile.ZipFile(nada, "w") as zz:
        zz.writestr("leeme.txt", "esto no es un mundo")
    d = correr(base, "importar", str(nada), "--nombre", "Nada", espera_ok=False)
    ok(not d.get("ok") and "level.dat" in d.get("mensaje", ""),
       "rechaza un zip sin level.dat, y lo dice")
    ok(len(correr(base, "listar").get("mundos", [])) == 2, "los rechazos no dejaron restos")

    # ── 5. cambiar ───────────────────────────────────────────────────────────
    titulo("5 · cambiar al mundo importado")
    (base / "render.log").unlink(missing_ok=True)
    d = correr(base, "cambiar", slug_azul)
    ok(d.get("ok"), "el cambio sale bien")
    ok(marca_de(mc / "world" / "marca.txt") == "AZUL",
       "en `world` está ahora el mundo AZUL")
    orig = mc / "mundos" / "mundo-original" / "mundo"
    ok(marca_de(orig / "marca.txt") == "ORIGINAL", "el original quedó guardado")
    ok(all((orig / "playerdata" / (u + ".dat")).exists() and
           (orig / "stats" / (u + ".json")).exists() for u in JUGADORES),
       "con sus jugadores y sus estadísticas")
    ok((mc / "mundos" / "mundo-original" / "mapa" / "overworld" / "tiles" / "0" / "x0" / "z0.png").exists(),
       "sus azulejos del mapa se archivaron con él")
    ok(not (web / "maps").exists(), "y el mapa web quedó vacío para el mundo nuevo")
    ok(json.loads((mc / "mundos" / "mundo-original" / "panel" / "markers.json").read_text())["marca"] == "ORIGINAL",
       "sus marcadores también")
    ok(not (panel / "data" / "markers.json").exists(),
       "el panel ya no sirve los marcadores del mundo viejo")
    ok((panel / "data" / "users.json").exists(),
       "pero las cuentas, que no son de ningún mundo, siguen ahí")
    ok(json.loads((mc / "mundos" / "activo.json").read_text())["slug"] == slug_azul,
       "activo.json apunta al nuevo")
    ok(d.get("mapa_completo") is True, "avisa de que toca redibujar el mapa entero")
    time.sleep(1)
    ok(any("--completo" in l for l in render_log(base)),
       "lanzó el render con --completo")
    ok((base / "estado" / "vivo").exists(), "y dejó el servidor arrancado")
    ok("stop" in consola(base), "lo paró antes de mover carpetas")

    # ── 6. volver ────────────────────────────────────────────────────────────
    titulo("6 · volver al mundo original")
    (base / "render.log").unlink(missing_ok=True)
    d = correr(base, "cambiar", "mundo-original")
    ok(d.get("ok"), "vuelve bien")
    ok(marca_de(mc / "world" / "marca.txt") == "ORIGINAL", "`world` es el original otra vez")
    ok(all((mc / "world" / "playerdata" / (u + ".dat")).exists() for u in JUGADORES),
       "con sus jugadores intactos")
    ok(marca_de(web / "maps" / "overworld" / "tiles" / "0" / "x0" / "z0.png") == "ORIGINAL",
       "sus azulejos volvieron al mapa web")
    ok(json.loads((panel / "data" / "markers.json").read_text())["marca"] == "ORIGINAL",
       "y sus marcadores al panel")
    ok(marca_de(mc / "mundos" / slug_azul / "mundo" / "marca.txt") == "AZUL",
       "el AZUL quedó guardado, no se perdió")
    ok(d.get("mapa_completo") is False, "no hace falta redibujar: tenía mapa")
    time.sleep(1)
    ok(render_log(base) and not any("--completo" in l for l in render_log(base)),
       "lanzó el render incremental, sin --completo")

    # ── 7. lo que no se puede hacer ──────────────────────────────────────────
    titulo("7 · las negativas")
    d = correr(base, "cambiar", "mundo-original", espera_ok=False)
    ok(not d.get("ok"), "no cambia al mundo que ya está activo")
    d = correr(base, "borrar", "mundo-original", espera_ok=False)
    ok(not d.get("ok"), "no borra el mundo activo")
    ok((mc / "world" / "level.dat").exists(), "y el mundo sigue ahí")
    d = correr(base, "cambiar", "no-existe", espera_ok=False)
    ok(not d.get("ok"), "no cambia a un mundo inventado")

    # ── 8. borrar de verdad ──────────────────────────────────────────────────
    titulo("8 · borrar el importado")
    d = correr(base, "borrar", slug_azul)
    ok(d.get("ok"), "borra")
    ok(not (mc / "mundos" / slug_azul).exists(), "desaparece de la lista")
    quedan = list((mc / "mundos" / "_borrados").glob("*%s*" % slug_azul))
    ok(len(quedan) == 1, "pero sigue en la papelera")
    ok(marca_de(quedan[0] / "mundo" / "marca.txt") == "AZUL" if quedan else False,
       "entero, con su level.dat")
    ok(len(correr(base, "listar").get("mundos", [])) == 1, "listar vuelve a mostrar 1")
    d = correr(base, "vaciar-papelera")
    ok(d.get("ok") and not (mc / "mundos" / "_borrados").exists(), "la papelera se puede vaciar")

    # ── 9. mundo nuevo desde cero ────────────────────────────────────────────
    titulo("9 · estrenar un mundo desde cero")
    (base / "render.log").unlink(missing_ok=True)
    d = correr(base, "nuevo", "--nombre", "Temporada 2", "--semilla", "12345")
    ok(d.get("ok"), "lo crea")
    ok(marca_de(mc / "world" / "marca.txt") == "GENERADO",
       "el servidor generó un `world` nuevo")
    ok((mc / "mundos" / "mundo-original" / "mundo" / "level.dat").exists(),
       "el mundo de antes quedó guardado")
    props = (mc / "server.properties").read_text()
    ok("level-seed=12345" in props, "escribió la semilla en server.properties")
    ok("motd=hola" in props and "level-name=world" in props,
       "sin cargarse el resto de server.properties")
    time.sleep(1)
    ok(any("--completo" in l for l in render_log(base)), "lanzó el render completo")

    # ── 10. server.properties raro ───────────────────────────────────────────
    titulo("10 · si alguien tocó level-name")
    (mc / "server.properties").write_text("level-name=otracosa\n")
    d = correr(base, "cambiar", "mundo-original", espera_ok=False)
    ok(not d.get("ok") and "level-name" in d.get("mensaje", ""),
       "se planta y avisa, en vez de dejar al server sin mundo")

    # ── de aquí en adelante, escenario limpio ────────────────────────────────
    # Las pruebas 11-14 miran el mapa, los marcadores y los ficheros de jugador,
    # y a estas alturas el escenario de arriba ya ha cambiado de mundo tres
    # veces. Heredar ese estado hacía que fallaran cosas que están bien.
    base = Path(tempfile.mkdtemp(prefix="probar-mundos2-"))
    montar(base)
    mc, panel, web = base / "minecraft", base / "panel", base / "web"
    print("   (escenario limpio en %s)" % base)

    # ── 11. inspeccionar y el aviso de versión ───────────────────────────────
    titulo("11 · mirar el archivo antes de meterlo")
    z = zip_de_mundo(base / "gris.zip", "GRIS")
    d = correr(base, "inspeccionar", str(z))
    ok(d.get("ok"), "inspecciona sin descomprimir el mundo entero")
    ok(d.get("version") == "26.2", "lee la versión del level.dat (%s)" % d.get("version"))
    ok(d.get("mas_nueva") is False, "y ve que no es más nueva que la del servidor")

    futuro = base / "futuro.zip"
    tmp = base / "crudo-FUTURO"; shutil.rmtree(tmp, ignore_errors=True)
    mundo_falso(tmp / "mundo", "FUTURO", ver_id=99999, ver="99.9")
    with zipfile.ZipFile(futuro, "w") as zz:
        for f in tmp.rglob("*"):
            if f.is_file():
                zz.write(f, f.relative_to(tmp))
    shutil.rmtree(tmp, ignore_errors=True)
    d = correr(base, "inspeccionar", str(futuro))
    ok(d.get("mas_nueva") is True, "avisa de que un mundo de 99.9 es más nuevo")
    d = correr(base, "importar", str(futuro), "--nombre", "Futuro", espera_ok=False)
    ok(not d.get("ok") and "sin arrancar" in d.get("mensaje", ""),
       "y NO deja importarlo: explica que el servidor no podría abrirlo")
    d = correr(base, "importar", str(futuro), "--nombre", "Futuro", "--forzar")
    ok(d.get("ok"), "salvo que se fuerce a propósito")
    correr(base, "borrar", d.get("slug", "futuro"))
    correr(base, "vaciar-papelera")

    # ── 12. reemplazar el mundo activo ───────────────────────────────────────
    titulo("12 · subir una versión nueva del MISMO mundo")
    (base / "render.log").unlink(missing_ok=True)
    z2 = zip_de_mundo(base / "original-v2.zip", "ORIGINAL-V2")
    antes_slug = [m for m in correr(base, "listar")["mundos"] if m["activo"]][0]["slug"]
    d = correr(base, "reemplazar", str(z2))
    ok(d.get("ok"), "reemplaza bien (%s)" % d.get("mensaje", "")[:60])
    ok(marca_de(mc / "world" / "marca.txt") == "ORIGINAL-V2", "`world` es la versión nueva")
    ok(d.get("slug") == antes_slug, "sigue siendo el MISMO mundo, no uno nuevo")
    ok((web / "maps" / "overworld" / "tiles" / "0" / "x0" / "z0.png").exists(),
       "el mapa NO se archivó: los azulejos siguen puestos")
    ok((panel / "data" / "markers.json").exists(), "y los marcadores tampoco")
    ok(d.get("mapa_completo") is False, "así que el redibujado es incremental")
    time.sleep(1)
    ok(render_log(base) and not any("--completo" in l for l in render_log(base)),
       "y se lanzó sin --completo")
    resp = d.get("respaldo")
    ok(resp and marca_de(mc / "mundos" / resp / "mundo" / "marca.txt") == "ORIGINAL",
       "la versión de antes quedó guardada por si acaso")
    ok(len(correr(base, "listar").get("mundos", [])) == 2, "y sale en la lista")

    # ── 13. traer los jugadores de otro mundo ────────────────────────────────
    titulo("13 · traer logros y estadísticas de otro mundo")
    u = JUGADORES[0]
    ok(json.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "ORIGINAL-V2",
       "de partida, el mundo activo tiene SUS estadísticas")
    d = correr(base, "jugadores", resp, "--que", "logros,stats")
    ok(d.get("ok"), "los trae (%s)" % d.get("mensaje", "")[:70])
    ok(d.get("cuenta", {}).get("stats") == 2, "cuenta los 2 jugadores")
    ok(json.loads((mc / "world" / "stats" / (u + ".json")).read_text())["marca"] == "ORIGINAL",
       "las estadísticas del mundo viejo pisaron a las del activo")
    ok(json.loads((mc / "world" / "advancements" / (u + ".json")).read_text())["marca"] == "ORIGINAL",
       "y los logros igual")
    guardado = d.get("resguardo")
    ok(guardado and json.loads(
        (Path(guardado) / "stats" / (u + ".json")).read_text())["marca"] == "ORIGINAL-V2",
       "lo que había antes quedó resguardado")
    ok("stop" in consola(base), "lo hizo con el servidor parado")
    ok((base / "estado" / "vivo").exists(), "y lo volvió a arrancar")

    titulo("14 · traer también el inventario")
    antes = leer_player(mc / "world" / "playerdata" / (u + ".dat"))
    ok(antes["XpLevel"] == 17, "el jugador del activo tiene su XP")
    d = correr(base, "jugadores", resp, "--que", "inventario")
    ok(d.get("ok"), "trae el inventario")
    ahora = leer_player(mc / "world" / "playerdata" / (u + ".dat"))
    ok(ahora["XpLevel"] == 17, "conserva la experiencia")
    ok(ahora["Pos"] == [100.5, 70.0, -199.5],
       "PERO lo reubica en el spawn del mundo activo (%s)" % (ahora["Pos"],))
    ok(ahora["Motion"] == [0.0, 0.0, 0.0], "y le para el movimiento")
    ok(ahora["Dimension"] == b"minecraft:overworld", "lo saca del Nether del mundo viejo")
    ok(ahora["SpawnX"] is None and ahora["LastDeathLocation"] is None,
       "borra la cama y el sitio donde murió, que son coordenadas de allí")

    d = correr(base, "jugadores", "no-existe", "--que", "stats", espera_ok=False)
    ok(not d.get("ok"), "no trae de un mundo inventado")
    act_slug = [m for m in correr(base, "listar")["mundos"] if m["activo"]][0]["slug"]
    d = correr(base, "jugadores", act_slug, "--que", "stats", espera_ok=False)
    ok(not d.get("ok"), "ni del mundo que ya está activo")

    # ── resumen ──────────────────────────────────────────────────────────────
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
