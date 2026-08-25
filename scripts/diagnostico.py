#!/usr/bin/env python3
"""
Responde con MEDICIONES, no con suposiciones:

  1. ¿El mapa se actualiza solo a medida que la gente explora?
  2. ¿Las estadísticas se actualizan solas, incluso mientras juegan?
  3. ¿Y los logros?
  4. ¿Cuánto le cuesta el panel al servidor?

Es de solo lectura salvo por un `save-all flush`, que es exactamente lo que el
propio Minecraft hace cada pocos minutos: no cambia nada del mundo, solo obliga
a volcar a disco lo que hay en memoria. Se usa para MEDIR cuánto se retrasan los
ficheros, que es justo la pregunta.

Uso (EN EL SERVIDOR):  python3 ~/panel/scripts/diagnostico.py
                       python3 ~/panel/scripts/diagnostico.py --sin-flush
"""
import glob, json, os, re, socket, struct, subprocess, sys, time
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent
MC    = Path.home() / "minecraft"
WEB   = Path("/var/www/bluemap-web")
FLUSH = "--sin-flush" not in sys.argv
AHORA = time.time()


def titulo(t):
    print("\n" + "=" * 66)
    print("  " + t)
    print("=" * 66)


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
    return "hace %.1f días" % (s / 86400)


def mtime(p):
    try:
        return Path(p).stat().st_mtime
    except OSError:
        return 0


def mas_nuevo(patron, tope=None):
    """(mtime, ruta) del fichero más reciente que case. tope limita el barrido."""
    mejor, cual, n = 0, None, 0
    for f in glob.iglob(patron, recursive=True):
        try:
            m = os.stat(f).st_mtime
        except OSError:
            continue
        n += 1
        if m > mejor:
            mejor, cual = m, f
        if tope and n >= tope:
            break
    return mejor, cual, n


# ------------------------------------------------------------------ RCON
def rcon(cmd, timeout=6.0):
    """RCON mínimo, sin importar server.py (que levantaría sus hilos)."""
    props = {}
    try:
        for linea in (MC / "server.properties").read_text().splitlines():
            if "=" in linea and not linea.startswith("#"):
                k, v = linea.split("=", 1)
                props[k.strip()] = v.strip()
    except OSError:
        return None
    def pkt(i, t, cuerpo):
        d = struct.pack("<ii", i, t) + cuerpo.encode() + b"\x00\x00"
        return struct.pack("<i", len(d)) + d
    def leer(s):
        crudo = b""
        while len(crudo) < 4:
            c = s.recv(4 - len(crudo))
            if not c:
                raise ConnectionError("cerrado")
            crudo += c
        (n,) = struct.unpack("<i", crudo)
        cuerpo = b""
        while len(cuerpo) < n:
            c = s.recv(n - len(cuerpo))
            if not c:
                break
            cuerpo += c
        return cuerpo[8:-2].decode("utf-8", "replace")
    try:
        with socket.create_connection(("127.0.0.1", int(props.get("rcon.port", 25575))),
                                      timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(pkt(1, 3, props.get("rcon.password", "")))
            leer(s)
            s.sendall(pkt(2, 2, cmd))
            return leer(s)
    except Exception as e:
        return "(sin rcon: %s)" % e


def region_dirs():
    """26.2 movió el overworld; se prueban los dos sitios."""
    out = []
    for d in (MC / "world/dimensions/minecraft/overworld/region",
              MC / "world/dimensions/minecraft/the_nether/region",
              MC / "world/dimensions/minecraft/the_end/region",
              MC / "world/region", MC / "world/DIM-1/region", MC / "world/DIM1/region"):
        if d.is_dir():
            out.append(d)
    return out


# ============================================================== 1) el mapa
def mapa():
    titulo("1 · ¿EL MAPA SE ACTUALIZA SOLO?")

    cron = Path("/etc/cron.d/bluemap-render")
    if not cron.exists():
        print("  ✗ NO hay tarea programada (/etc/cron.d/bluemap-render no existe).")
        print("    → el mapa NO se actualiza solo. Solo cambia cuando lo lanzas a mano")
        print("      o desde Sistema → 'Escanear estructuras y actualizar el mapa'.")
        activo = False
    else:
        # 🔴 Esta comprobación estuvo MAL y dio un falso negativo en producción:
        # buscaba las palabras "bluemap" o "java" en las líneas, y cuando el
        # comando pasó a ser `render-mapa.sh` ninguna las contenía. El
        # diagnóstico decía "el mapa NO se actualiza solo" mientras el propio
        # render.log de arriba demostraba que el cron había disparado a las
        # 09:00. Ahora no se adivina por el nombre del comando: una entrada de
        # cron es cualquier línea que no sea comentario ni asignación de
        # variable (SHELL=…, PATH=…, MAILTO=…).
        lineas = [l.strip() for l in cron.read_text().splitlines()
                  if l.strip() and not l.strip().startswith("#")]
        entradas = [l for l in lineas
                    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", l)]
        activo = bool(entradas)
        if activo:
            print("  ✔ tarea programada ACTIVA:")
            for l in entradas:
                print("      " + l)
        else:
            print("  ✗ el fichero existe pero no tiene ninguna línea de horario")
            print("    (solo comentarios o variables) → el mapa no se actualiza solo.")

    # ¿cuánto se ha movido el mundo desde el último render?
    mundo, cual_m, _ = 0, None, 0
    for d in region_dirs():
        m, c, n = mas_nuevo(str(d / "*.mca"))
        if m > mundo:
            mundo, cual_m = m, c
    TOPE = 40000
    tiles, cual_t, n_t = mas_nuevo(str(WEB / "maps/**/*.prbm*"), tope=TOPE)

    print()
    print("  mundo   — región tocada más recientemente : %s" % hace(mundo))
    print("  mapa    — tile renderizado más reciente   : %s   (%d tiles mirados%s)"
          % (hace(tiles), n_t, ", TOPE — es una muestra" if n_t >= TOPE else ""))
    if mundo and tiles:
        atraso = (mundo - tiles) / 3600
        if atraso <= 0:
            print("  → el mapa está al día.")
        else:
            print("  → el mapa va %.1f h POR DETRÁS de lo que la gente ya exploró." % atraso)
    log = Path.home() / "bluemap/render.log"
    if log.exists():
        print("  último render (render.log) : %s" % hace(mtime(log)))


# ================================================ 2 y 3) stats y logros
def _dir_jugadores():
    for c in (MC / "world/players/stats", MC / "world/stats"):
        if c.is_dir():
            return c, c.parent / "advancements" if (c.parent / "advancements").is_dir() \
                   else MC / "world/advancements"
    return MC / "world/players/stats", MC / "world/advancements"


def datos_de_jugador():
    sdir, adir = _dir_jugadores()
    titulo("2 y 3 · ¿ESTADÍSTICAS Y LOGROS SE ACTUALIZAN SOLOS?")

    nombres = {}
    for f in (MC / "usercache.json", MC / "whitelist.json"):
        try:
            for e in json.loads(f.read_text()):
                if e.get("uuid") and e.get("name"):
                    nombres[e["uuid"]] = e["name"]
        except Exception:
            pass

    lista = rcon("list") or ""
    conectados = set()
    m = re.search(r"players online:?\s*(.*)$", lista, re.I | re.S)
    if m:
        conectados = {n.strip() for n in m.group(1).split(",")
                      if re.fullmatch(r"[A-Za-z0-9_]{1,16}", n.strip())}
    print("  conectados ahora: %s" % (", ".join(sorted(conectados)) or "nadie"))
    print("  (respuesta cruda de /list: %r)" % lista[:90])

    ficheros = sorted(sdir.glob("*.json"), key=lambda f: -mtime(f))[:8]
    if not ficheros:
        print("  ✗ no encuentro ficheros de estadísticas en %s" % sdir)
        return

    def tabla(cabecera):
        print("\n  %-17s %-9s %-22s %s" % ("jugador", "estado", "estadísticas", "logros"))
        print("  " + "-" * 62)
        filas = []
        for f in ficheros:
            uuid = f.stem
            nom = nombres.get(uuid, uuid[:8])
            est = "EN LÍNEA" if nom in conectados else "fuera"
            a = adir / (uuid + ".json")
            filas.append((nom, est, mtime(f), mtime(a)))
            print("  %-17s %-9s %-22s %s" % (nom, est, hace(mtime(f)), hace(mtime(a))))
        return filas

    print("\n  ANTES de forzar el guardado:")
    antes = tabla("antes")

    if not FLUSH:
        return
    print("\n  … mandando 'save-all flush' (lo mismo que hace el autoguardado) …")
    r = rcon("save-all flush")
    print("  respuesta: %r" % (r or "")[:80])
    time.sleep(4)

    print("\n  DESPUÉS del guardado:")
    despues = tabla("después")

    cambiaron = sum(1 for (n1, e1, s1, a1), (n2, e2, s2, a2) in zip(antes, despues)
                    if s2 > s1 or a2 > a1)
    print()
    if cambiaron:
        print("  → %d fichero(s) se acaban de reescribir." % cambiaron)
        print("    Es decir: los datos NO están al segundo; se escriben cuando el")
        print("    servidor guarda (autoguardado, o al desconectarse el jugador).")
        print("    El panel lee esos ficheros, así que muestra la última foto guardada.")
    else:
        print("  → nada cambió con el flush: o no hay nadie jugando, o ya estaban")
        print("    al día. Repite esto con alguien conectado y jugando para verlo.")


# ================================================== 4) coste del panel
def rendimiento():
    titulo("4 · ¿CUÁNTO LE CUESTA EL PANEL AL SERVIDOR?")

    def unidad(nombre):
        try:
            out = subprocess.run(
                ["systemctl", "show", nombre, "-p", "CPUUsageNSec",
                 "-p", "MemoryCurrent", "-p", "ActiveEnterTimestampMonotonic"],
                capture_output=True, text=True, timeout=10).stdout
            d = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
            cpu = int(d.get("CPUUsageNSec", 0) or 0) / 1e9
            mem = int(d.get("MemoryCurrent", 0) or 0) / 2**20
            arr = int(d.get("ActiveEnterTimestampMonotonic", 0) or 0) / 1e6
            with open("/proc/uptime") as f:
                up = float(f.read().split()[0])
            vivo = max(1.0, up - arr)
            return cpu, mem, vivo
        except Exception:
            return None

    for nombre in ("panel", "minecraft"):
        d = unidad(nombre)
        if not d:
            print("  %-10s (no pude leerlo)" % nombre)
            continue
        cpu, mem, vivo = d
        print("  %-10s CPU acumulada %8.1f s en %5.1f h vivo  =  %5.2f%% de un núcleo"
              % (nombre, cpu, vivo / 3600, cpu * 100 / vivo))
        print("  %-10s memoria       %8.0f MB" % ("", mem))

    print("\n  Tiempo que tarda cada respuesta del panel (medido aquí mismo):")
    puerto = 8444
    for ruta, que in (("/api/branding", "pulso"),):
        t0 = time.time()
        subprocess.run(["curl", "-s", "-o", "/dev/null",
                        "http://127.0.0.1:%d%s" % (puerto, ruta)],
                       capture_output=True, timeout=15)
        print("    %-22s %6.0f ms   (%s)" % (ruta, (time.time() - t0) * 1000, que))

    print("\n  Coste del RCON, que es lo único que toca el hilo del juego:")
    for cmd in ("list", "tick query"):
        t0 = time.time()
        rcon(cmd)
        print("    %-12s %6.0f ms" % (cmd, (time.time() - t0) * 1000))

    print("\n  MSPT ahora mismo: %s" % (rcon("tick query") or "").replace("\n", " ")[:110])


if __name__ == "__main__":
    print("Diagnóstico del panel — %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    try:
        mapa()
    except Exception as e:
        print("  (falló: %s)" % e)
    try:
        datos_de_jugador()
    except Exception as e:
        print("  (falló: %s)" % e)
    try:
        rendimiento()
    except Exception as e:
        print("  (falló: %s)" % e)
    print("\nListo. Pásame esta salida entera.")
