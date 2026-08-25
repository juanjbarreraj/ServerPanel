#!/usr/bin/env python3
"""
¿Aguanta esto solo?

Una sola pasada que contesta, con hechos del propio servidor, a la única
pregunta que importa cuando nadie va a estar mirando durante semanas:

    ¿QUÉ se actualiza solo, y hay pruebas de que lo está haciendo?

No cambia nada. Solo mira. Se puede correr cuantas veces se quiera.

Uso (EN EL SERVIDOR):  python3 ~/panel/scripts/revision.py
"""
import glob, json, os, re, subprocess, sys, time
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent
HOME  = Path.home()
MC    = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
BM    = HOME / "bluemap"
DATA  = PANEL / "data"
AHORA = time.time()

BIEN, MAL, OJO = [], [], []


# ---------------------------------------------------------------- pintar
def titulo(t):
    print("\n" + "═" * 68)
    print("  " + t)
    print("═" * 68)


def hace(ts):
    if not ts:
        return "nunca"
    s = AHORA - ts
    if s < 90:
        return "hace %d s" % s
    if s < 5400:
        return "hace %d min" % round(s / 60)
    if s < 172800:
        return "hace %.1f h" % (s / 3600)
    return "hace %d días" % round(s / 86400)


def linea(estado, texto, detalle=""):
    """estado: 'ok' | 'mal' | 'ojo'"""
    marca = {"ok": "  ✔ ", "mal": "  ✘ ", "ojo": "  ! "}[estado]
    print(marca + texto + (("   " + detalle) if detalle else ""))
    {"ok": BIEN, "mal": MAL, "ojo": OJO}[estado].append(texto)


def mtime(p):
    try:
        return Path(p).stat().st_mtime
    except Exception:
        return 0


def tam(p):
    try:
        return Path(p).stat().st_size
    except Exception:
        return 0


def mb(n):
    return "%.1f MB" % (n / 2**20)


def correr(cmd, t=10):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=t).stdout
    except Exception:
        return ""


# ============================================ 1 · ¿se levanta solo si se cae?
def servicios():
    titulo("1 · SI ALGO SE CAE, ¿VUELVE SOLO?")
    print("  Esto es lo primero: da igual lo bien que se actualice todo si un")
    print("  servicio se muere de madrugada y se queda muerto hasta que vuelvas.\n")

    for unidad, quees in (("minecraft", "el servidor de Minecraft"),
                          ("panel", "el panel web"),
                          ("caddy", "el que sirve califree.net por HTTPS")):
        out = correr(["systemctl", "show", unidad,
                      "-p", "ActiveState", "-p", "UnitFileState",
                      "-p", "Restart", "-p", "NRestarts"])
        d = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
        if not d:
            linea("ojo", "%-10s no pude leer el servicio" % unidad)
            continue
        activo = d.get("ActiveState") == "active"
        arranca = d.get("UnitFileState") in ("enabled", "enabled-runtime", "static")
        reinicia = (d.get("Restart") or "no") not in ("no", "")
        nr = d.get("NRestarts", "0")

        est = "ok" if (activo and arranca and reinicia) else "mal"
        linea(est, "%-10s %s" % (unidad, quees),
              "· ahora: %s · al reiniciar la máquina: %s · si se cae: %s%s" % (
                  "encendido" if activo else "APAGADO",
                  "arranca" if arranca else "NO ARRANCA",
                  ("se levanta solo (Restart=%s)" % d.get("Restart")) if reinicia
                  else "SE QUEDA CAÍDO",
                  ("  · ya se ha reiniciado %s veces" % nr) if nr not in ("0", "") else ""))

        if not reinicia:
            print("      arreglo:  sudo systemctl edit %s" % unidad)
            print("                [Service]")
            print("                Restart=always")
            print("                RestartSec=15")
            print("                sudo systemctl daemon-reload")
        if not arranca:
            print("      arreglo:  sudo systemctl enable %s" % unidad)


# ============================================ 2 · las tareas programadas
def tareas():
    titulo("2 · TAREAS PROGRAMADAS (lo que corre sin que nadie lo pida)")

    entradas = []
    for f in sorted(glob.glob("/etc/cron.d/*")):
        try:
            for l in Path(f).read_text().splitlines():
                l = l.strip()
                if not l or l.startswith("#"):
                    continue
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", l):
                    continue                      # MAILTO=, PATH=, SHELL=
                entradas.append((Path(f).name, l))
        except Exception:
            pass
    propio = correr(["crontab", "-l"])
    for l in propio.splitlines():
        l = l.strip()
        if l and not l.startswith("#") and not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", l):
            entradas.append(("crontab de %s" % os.environ.get("USER", "?"), l))

    # Solo las que tienen que ver con el server: la caja trae de fábrica media
    # docena de tareas de mantenimiento de Ubuntu que no aportan nada aquí.
    NUESTRO = re.compile(r"panel|minecraft|bluemap|backup|copia|render|mc-", re.I)
    mias = [(o, l) for o, l in entradas if NUESTRO.search(l)]
    otras = len(entradas) - len(mias)

    if not mias:
        linea("mal", "no hay NINGUNA tarea programada nuestra",
              "· ni el mapa ni las copias se harán solos")
    else:
        for origen, l in mias:
            print("    [%s] %s" % (origen, l[:110]))
    if otras:
        print("    (y %d tareas del propio Ubuntu, que no nos importan)" % otras)
    print()

    texto = " ".join(l for _, l in mias)
    if "render-mapa.sh" in texto:
        linea("ok", "el mapa tiene su tarea de cada noche")
    elif "bluemap" in texto:
        linea("ojo", "hay una tarea de BlueMap pero NO llama a render-mapa.sh",
              "· probablemente le falte el paso --markers de los iconos")
    else:
        linea("mal", "el mapa NO tiene tarea programada")

    if re.search(r"backup|copia", texto, re.I):
        linea("ok", "hay una tarea de copias de seguridad")
    else:
        linea("mal", "NO hay tarea de copias de seguridad del mundo",
              "· solo se hacen si le das al botón")
        print("      arreglo:  echo 'MAILTO=\"\"' | sudo tee /etc/cron.d/mc-backup")
        print("                echo '0 8 * * * ubuntu /bin/bash %s/backup.sh' \\" % MC)
        print("                  | sudo tee -a /etc/cron.d/mc-backup")


# ============================================ 3 · pruebas de que va solo
def frescura():
    titulo("3 · ¿HAY PRUEBAS DE QUE SE ESTÁ ACTUALIZANDO?")
    print("  Cada línea mira la HUELLA que deja el automatismo al correr.\n")

    # --- el mapa
    log = BM / "render.log"
    ini = fin = 0
    try:
        txt = log.read_text(errors="replace").splitlines()[-4000:]
        for l in txt:
            m = re.match(r"\[([\d-]+ [\d:]+)\].*inicio", l)
            if m:
                ini = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            m = re.match(r"\[([\d-]+ [\d:]+)\].*fin, (\d+) min", l)
            if m:
                fin = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                ultimo_min = m.group(2)
    except Exception:
        pass
    if fin and AHORA - fin < 36 * 3600:
        linea("ok", "el mapa se rehízo entero", "· %s, tardó %s min" % (hace(fin), ultimo_min))
    elif ini and AHORA - ini < 8 * 3600 and (not fin or fin < ini):
        linea("ojo", "hay un render EN MARCHA", "· empezó %s" % hace(ini))
    else:
        linea("mal", "el mapa no termina un render desde hace mucho", "· %s" % hace(fin))

    tiles = 0
    for raiz in ("/var/www/bluemap-web/maps", str(BM / "web/maps")):
        for p in glob.glob(raiz + "/*/tiles/**/*.*", recursive=True)[:4000]:
            tiles = max(tiles, mtime(p))
    if tiles:
        est = "ok" if AHORA - tiles < 36 * 3600 else "ojo"
        linea(est, "última porción de mapa dibujada", "· %s" % hace(tiles))

    # --- los marcadores
    cache = DATA / "structures-cache.json"
    if cache.exists():
        est = "ok" if AHORA - mtime(cache) < 36 * 3600 else "mal"
        linea(est, "escaneo de estructuras (los iconos del mapa)", "· %s" % hace(mtime(cache)))
    else:
        linea("ojo", "el escaneo de estructuras aún no ha corrido nunca",
              "· correrá esta noche, o dale al botón «Actualizar los iconos»")
    txt = ""
    try:
        txt = (BM / "render.log").read_text(errors="replace")[-200000:]
    except Exception:
        pass
    if "marcadores publicados" in txt:
        linea("ok", "los iconos se están publicando en el mapa (--markers)")
    elif txt:
        linea("mal", "NO veo el paso --markers en el registro",
              "· las estructuras nuevas no se verán")

    # --- la Historia
    fs = DATA / "feed_state.json"
    if fs.exists():
        est = "ok" if AHORA - mtime(fs) < 300 else "mal"
        linea(est, "Historia leyendo el log del juego", "· %s" % hace(mtime(fs)))
    else:
        linea("mal", "la Historia no ha arrancado nunca")

    # --- estadísticas y logros: los escribe MINECRAFT, no el panel
    for carpeta, que in ((MC / "world/players/stats", "estadísticas"),
                         (MC / "world/players/advancements", "logros")):
        if not carpeta.is_dir():
            carpeta = MC / ("world/" + carpeta.name)
        ult = max([mtime(p) for p in glob.glob(str(carpeta / "*.json"))] or [0])
        if not ult:
            linea("ojo", "no encuentro los ficheros de %s" % que)
        else:
            linea("ok", "%s al día" % que,
                  "· el último cambio, %s (los escribe Minecraft solo)" % hace(ult))

    # --- copias
    cs = sorted(glob.glob(str(MC / "backups/world-*.tar.gz")), key=mtime)
    if cs:
        est = "ok" if AHORA - mtime(cs[-1]) < 8 * 86400 else "mal"
        linea(est, "copia de seguridad del mundo",
              "· la última %s · hay %d guardadas" % (hace(mtime(cs[-1])), len(cs)))
        # ¿se borran las viejas? Las copias son con diferencia lo que más ocupa,
        # y una que no pode llena el disco en unos meses sin avisar.
        peso = sum(tam(p) for p in cs)
        dias = (mtime(cs[-1]) - mtime(cs[0])) / 86400
        if len(cs) >= 3 and dias > len(cs) + 3:
            linea("ok", "las copias viejas se borran solas",
                  "· %d copias repartidas en %.0f días · %s en total" % (len(cs), dias, mb(peso)))
        elif len(cs) > 20:
            linea("ojo", "las copias NO parecen podarse",
                  "· %d guardadas, %s · la más vieja %s" % (len(cs), mb(peso), hace(mtime(cs[0]))))
            print("      mira si backup.sh borra las antiguas:")
            print("        grep -nE 'rm |ls -t|tail -n' %s/backup.sh" % MC)
        else:
            ritmo = peso / max(1.0, dias) if dias > 0.5 else peso
            linea("ok", "las copias ocupan %s" % mb(peso),
                  "· la más vieja %s · crecen ~%s al día" % (hace(mtime(cs[0])), mb(ritmo)))
    else:
        linea("mal", "NO hay ninguna copia de seguridad del mundo")

    # --- skins
    marca = DATA / "skins/last_snapshot.txt"
    if marca.exists():
        est = "ok" if AHORA - mtime(marca) < 50 * 3600 else "ojo"
        linea(est, "foto diaria de las skins", "· %s" % hace(mtime(marca)))


# ============================================ 4 · lo que NO se actualiza solo
def manuales():
    titulo("4 · LO QUE **NO** SE ACTUALIZA SOLO (y no pasa nada)")
    print("  Estas cosas se construyen una vez y solo hay que rehacerlas cuando")
    print("  cambie la VERSIÓN de Minecraft. Mientras el server siga igual, no")
    print("  hay que tocar nada.\n")

    jars = sorted(glob.glob(str(MC / "versions/*/server-*.jar")))
    ver = "?"
    if jars:
        m = re.search(r"server-([\d.\w-]+)\.jar$", jars[-1])
        ver = m.group(1) if m else "?"
    print("    versión del servidor ahora mismo: %s\n" % ver)

    for f, que, script in (
            (DATA / "advancements.json", "catálogo de logros", "build-advancements.py"),
            (DATA / "biomas.json", "nombres de biomas", "build-biomes.py"),
            (DATA / "mensajes.json", "frases de muertes/logros del feed", "build-mensajes.py")):
        if not f.exists():
            linea("mal", "falta %s" % que, "· correr: python3 ~/panel/scripts/%s" % script)
            continue
        vf = "?"
        try:
            vf = (json.loads(f.read_text()).get("version") or "?")
        except Exception:
            pass
        if vf == "?":
            # biomas.json no guarda la versión dentro; se juzga por la fecha
            linea("ok", "%s hecho" % que, "· %s (no guarda versión dentro)" % hace(mtime(f)))
        elif vf != ver and ver != "?":
            linea("ojo", "%s hecho para la %s, el server va por la %s" % (que, vf, ver),
                  "· correr: python3 ~/panel/scripts/%s" % script)
        else:
            linea("ok", "%s al día" % que, "· versión %s" % vf)

    n = len(glob.glob(str(PANEL / "icons/**/*.png"), recursive=True))
    linea("ok" if n > 1000 else "ojo", "iconos de objetos del juego",
          "· %d imágenes · se rehacen con el botón «Regenerar íconos»" % n)


# ============================================ 5 · disco y ficheros que crecen
def disco():
    titulo("5 · DISCO Y FICHEROS QUE CRECEN")

    out = correr(["df", "-B1", "--output=size,used,avail,pcent", "/"])
    try:
        size, used, avail, pcent = out.splitlines()[1].split()
        libre = int(avail)
        est = "ok" if libre > 5 * 2**30 else ("ojo" if libre > 2 * 2**30 else "mal")
        linea(est, "espacio libre en el disco",
              "· %s libres de %s (usado %s)" % (mb(libre), mb(int(size)), pcent))
    except Exception:
        linea("ojo", "no pude leer el espacio del disco")

    print("\n  Los ficheros que crecen con el uso:")
    vigilar = [
        (DATA / "audit.log", "registro de acciones", 5),
        (DATA / "system.log", "registro del sistema", 2),
        (DATA / "feed.jsonl", "eventos de la Historia", 50),
        (DATA / "feed-historico.jsonl", "Historia antigua", 50),
        (DATA / "join_requests.json", "intentos de entrar", 1),
        (BM / "render.log", "registro del mapa", 5),
    ]
    for p, que, techo_mb in vigilar:
        t = tam(p)
        if not t:
            continue
        est = "ok" if t < techo_mb * 2**20 else "ojo"
        print("    %s %-32s %10s   (tope %d MB)" %
              ("✔" if est == "ok" else "!", que, mb(t), techo_mb))

    grandes = []
    for raiz in (MC / "backups", PANEL / "memories", MC / "world", BM):
        try:
            n = sum(f.stat().st_size for f in Path(raiz).rglob("*") if f.is_file())
            grandes.append((n, str(raiz)))
        except Exception:
            pass
    if grandes:
        print("\n  Lo que más ocupa:")
        for n, r in sorted(grandes, reverse=True):
            print("    %10s  %s" % (mb(n), r))

    sobras = glob.glob("/tmp/panel-extras-*.tar.gz")
    if sobras:
        linea("ojo", "hay %d respaldos sueltos en /tmp" % len(sobras),
              "· se borran solos al pedir el siguiente")
    baks = glob.glob(str(MC / "world/players/data/*.dat.bak-*"))
    if len(baks) > 30:
        linea("ojo", "hay %d copias .dat.bak dentro del mundo" % len(baks),
              "· se podan solas al quitar el siguiente ítem")


# ============================================ 6 · el panel responde
def panel_vivo():
    titulo("6 · ¿RESPONDE EL PANEL?")
    t0 = time.time()
    out = correr(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                  "http://127.0.0.1:8444/api/branding"], t=15)
    ms = (time.time() - t0) * 1000
    if out.strip() == "200":
        linea("ok", "el panel contesta", "· %.0f ms" % ms)
    else:
        linea("mal", "el panel NO contesta bien", "· código %s" % (out.strip() or "sin respuesta"))

    t0 = time.time()
    out = correr(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                  "https://califree.net/api/branding"], t=20)
    if out.strip() in ("200", "401", "403"):
        linea("ok", "califree.net responde desde fuera", "· %.0f ms" % ((time.time() - t0) * 1000))
    else:
        linea("ojo", "califree.net no contestó como se esperaba", "· código %s" % out.strip())


def main():
    print("\nREVISIÓN DEL SERVER — %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Solo lectura: esto no cambia nada.")
    for f in (servicios, tareas, frescura, manuales, disco, panel_vivo):
        try:
            f()
        except Exception as e:
            print("\n  (fallo revisando «%s»: %s)" % (f.__name__, e))

    titulo("RESUMEN")
    print("  %d bien · %d para mirar · %d mal\n" % (len(BIEN), len(OJO), len(MAL)))
    if MAL:
        print("  HAY QUE ARREGLAR:")
        for x in MAL:
            print("    ✘ " + x)
    if OJO:
        print("\n  PARA MIRAR CON CALMA:")
        for x in OJO:
            print("    ! " + x)
    if not MAL and not OJO:
        print("  Todo en orden. El server aguanta solo.")
    print()
    return 1 if MAL else 0


if __name__ == "__main__":
    sys.exit(main())
