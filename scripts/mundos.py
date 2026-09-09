#!/usr/bin/env python3
"""
Gestor de mundos: guardar varios, cambiar entre ellos y que el mapa siga.

CÓMO ESTÁ MONTADO
-----------------
    ~/minecraft/world/              el mundo ACTIVO (aquí escribe Minecraft)
    ~/minecraft/mundos/<slug>/
        mundo/                      la carpeta del mundo guardado
        mapa/                       sus azulejos de BlueMap, archivados
        panel/                      sus marcadores y el escaneo de estructuras
        info.json                   nombre bonito, fechas
    ~/minecraft/mundos/activo.json  cuál está en `world` ahora mismo
    ~/minecraft/mundos/_borrados/   papelera: borrar solo mueve aquí

Cambiar de mundo es MOVER carpetas, no copiarlas: es instantáneo aunque el mundo
pese 2 GB, porque es el mismo disco. Y a propósito **no** se usa un enlace
simbólico en `world`: `backup.sh` hace `tar czf ... world`, y con un enlace se
llevaría el enlace en vez del mundo, dejándote copias vacías sin avisar.

LO QUE VIAJA CON CADA MUNDO
---------------------------
El mundo se lleva dentro sus estadísticas, logros y jugadores. Pero hay dos
cosas del panel que también son de ese mundo y viven fuera, así que se archivan
junto a él o al cambiar quedarían apuntando a sitios que ya no existen:

  · los azulejos del mapa de BlueMap  (son fotos del terreno viejo)
  · `data/markers.json` y el escaneo  (son coordenadas y ficheros de región)

POR QUÉ BASTA CON ARCHIVAR LOS AZULEJOS
---------------------------------------
BlueMap guarda aparte, en su carpeta `data`, en qué se quedó el último render.
Ese apunte NO se archiva, y a propósito:

  · Si vuelves a un mundo que YA tenía mapa, sus azulejos se restauran tal cual.
    Da igual lo que opine el apunte: si decide redibujar, redibuja lo mismo; y
    si decide saltárselo, los azulejos correctos ya están puestos.
  · Si el mundo NO tenía mapa (recién subido), se lanza el render con
    `--completo`, que es `-r -f`, y `-f` ignora el apunte por completo. Esto
    importa: un mundo sacado de un zip conserva fechas viejas en sus ficheros de
    región, y sin `-f` BlueMap podría darlos por dibujados y dejarte el mapa en
    blanco.

Uso (en el servidor):
    python3 ~/panel/scripts/mundos.py listar
    python3 ~/panel/scripts/mundos.py copia /tmp/mundo.tar.gz
    python3 ~/panel/scripts/mundos.py inspeccionar /tmp/subido.zip
    python3 ~/panel/scripts/mundos.py importar /tmp/subido.zip --nombre "Mundo nuevo"
    python3 ~/panel/scripts/mundos.py reemplazar /tmp/subido.zip
    python3 ~/panel/scripts/mundos.py jugadores <slug> --que logros,stats,inventario
    python3 ~/panel/scripts/mundos.py nuevo --nombre "Temporada 2" [--semilla 12345]
    python3 ~/panel/scripts/mundos.py cambiar <slug>
    python3 ~/panel/scripts/mundos.py borrar <slug>
    python3 ~/panel/scripts/mundos.py vaciar-papelera

Con `--json` escupe JSON en vez de texto (es lo que usa el panel).
"""
import gzip, json, os, re, shutil, subprocess, sys, tarfile, time, unicodedata, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import nbt                      # el lector/escritor del panel, byte a byte
except Exception:
    nbt = None

HOME   = Path.home()
MC     = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
PANEL  = Path(os.environ.get("PANEL_DIR", HOME / "panel"))
WORLD  = MC / "world"
MUNDOS = MC / "mundos"
ACTIVO = MUNDOS / "activo.json"
PAPELERA = MUNDOS / "_borrados"
WEB    = Path(os.environ.get("BLUEMAP_WEB", "/var/www/bluemap-web"))
LOG    = MC / "mundos.log"
LOCK   = MUNDOS / ".lock"
SERVICIO = os.environ.get("MC_SERVICE", "minecraft")
SCREEN   = os.environ.get("MC_SCREEN", "mc")
PROPS    = MC / "server.properties"

# Los ficheros del panel que son de ESTE mundo y no del panel en general
PANEL_POR_MUNDO = ("markers.json", "structures.json", "structures-cache.json")

JSON_MODE = "--json" in sys.argv


def decir(*a):
    linea = "[%s] %s" % (time.strftime("%F %T"), " ".join(str(x) for x in a))
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as f:
            f.write(linea + "\n")
    except Exception:
        pass
    if not JSON_MODE:
        print(linea, flush=True)


def salir(ok, mensaje, **extra):
    if JSON_MODE:
        print(json.dumps(dict(ok=ok, mensaje=mensaje, **extra), ensure_ascii=False))
    else:
        print(("✔ " if ok else "✘ ") + mensaje)
    sys.exit(0 if ok else 1)


# ------------------------------------------------------------------ utilidades
def slugify(nombre):
    s = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()[:40]
    return s or "mundo"


def slug_libre(base):
    """Nunca reutiliza un slug: ni de un mundo vivo ni de uno en la papelera."""
    slug, n = base, 2
    while (MUNDOS / slug).exists():
        slug = "%s-%d" % (base, n)
        n += 1
    return slug


def tamano(p: Path):
    try:
        if p.is_file():
            return p.stat().st_size
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    except Exception:
        return 0


def libre():
    try:
        st = os.statvfs(str(MC if MC.exists() else HOME))
        return st.f_bavail * st.f_frsize
    except Exception:
        return 0


def mb(n):
    return "%.0f MB" % (n / 2**20)


def opcion(nombre, por_defecto=None):
    """Lee `--clave valor` de la línea de órdenes."""
    for i, a in enumerate(sys.argv):
        if a == nombre and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(nombre + "="):
            return a.split("=", 1)[1]
    return por_defecto


class Candado:
    """Un cambio de mundo a la vez. Si algo se queda a medias con el servidor
    parado, el candado es lo que evita que un segundo intento lo empeore."""
    def __enter__(self):
        MUNDOS.mkdir(parents=True, exist_ok=True)
        if LOCK.exists():
            edad = time.time() - LOCK.stat().st_mtime
            if edad < 1800:
                salir(False, "Ya hay una operación de mundos en marcha (hace %d min)"
                             % (edad / 60))
            decir("candado viejo de %d min, lo piso" % (edad / 60))
        LOCK.write_text(str(os.getpid()))
        return self

    def __exit__(self, *a):
        LOCK.unlink(missing_ok=True)


# --------------------------------------------------------- hablar con el juego
def mc_cmd(cmd):
    """Manda una orden a la consola de Minecraft por la sesión de screen, igual
    que hace backup.sh. No hace falta contraseña de RCON."""
    try:
        r = subprocess.run(["screen", "-p", "0", "-S", SCREEN, "-X", "eval",
                            'stuff "%s\\015"' % cmd],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception as e:
        decir("⚠ no pude hablar con la consola: %s" % e)
        return False


def servidor_vivo():
    try:
        r = subprocess.run(["pgrep", "-f", "server.jar"], capture_output=True,
                           text=True, timeout=10)
        return bool(r.stdout.strip())
    except Exception:
        return False


def parar_servidor(espera=180):
    """Para Minecraft del todo, guardando antes.

    Se usa `stop` por la consola y NO `systemctl stop`: la regla de sudo del
    panel solo permite `restart`. Como `stop` sale con código 0, el
    `Restart=on-failure` de systemd no lo vuelve a levantar, así que se queda
    parado el tiempo que haga falta para mover las carpetas.
    """
    if not servidor_vivo():
        decir("el servidor ya estaba parado")
        return True
    decir("guardando el mundo antes de parar…")
    mc_cmd("save-all flush")
    time.sleep(6)
    decir("parando el servidor…")
    if not mc_cmd("stop"):
        decir("⚠ la consola (screen -S %s) no responde" % SCREEN)
        return False
    t0 = time.time()
    while time.time() - t0 < espera:
        if not servidor_vivo():
            time.sleep(3)                     # que suelte los ficheros del todo
            decir("servidor parado en %d s" % (time.time() - t0))
            return True
        time.sleep(2)
    decir("⚠ el servidor NO se paró en %d s" % espera)
    return False


def arrancar_servidor(espera=60):
    decir("arrancando el servidor…")
    try:
        subprocess.run(["sudo", "-n", "systemctl", "restart", SERVICIO],
                       capture_output=True, text=True, timeout=90)
    except Exception as e:
        decir("⚠ systemctl restart falló: %s" % e)
    t0 = time.time()
    while time.time() - t0 < espera:
        time.sleep(2)
        if servidor_vivo():
            decir("servidor arrancado")
            return True
    decir("⚠ el servidor no arrancó solo — míralo con: systemctl status %s" % SERVICIO)
    return False


# ------------------------------------------------------- server.properties
def prop(clave, por_defecto=""):
    try:
        for ln in PROPS.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.strip().startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k.strip() == clave:
                return v.strip()
    except Exception:
        pass
    return por_defecto


def poner_prop(clave, valor):
    """Cambia una clave de server.properties dejando el resto (y los
    comentarios) tal cual."""
    try:
        lineas = PROPS.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return False
    hecho, fuera = False, []
    for ln in lineas:
        if not ln.strip().startswith("#") and "=" in ln and ln.split("=", 1)[0].strip() == clave:
            fuera.append("%s=%s" % (clave, valor))
            hecho = True
        else:
            fuera.append(ln)
    if not hecho:
        fuera.append("%s=%s" % (clave, valor))
    PROPS.write_text("\n".join(fuera) + "\n", encoding="utf-8")
    return True


def comprobar_level_name():
    """Todo esto da por hecho que la carpeta del mundo se llama `world`. Si
    alguien tocó level-name, mover carpetas dejaría el server sin mundo."""
    ln = prop("level-name", "world")
    if ln and ln != "world":
        salir(False, "server.properties dice level-name=%s y esto espera «world». "
                     "Cámbialo o dímelo antes de tocar nada." % ln)


# ------------------------------------------------------------------ el estado
def leer_activo():
    try:
        d = json.loads(ACTIVO.read_text())
        if d.get("slug"):
            return d
    except Exception:
        pass
    return {"slug": "mundo-original", "nombre": "Mundo original"}


def guardar_activo(slug, nombre):
    MUNDOS.mkdir(parents=True, exist_ok=True)
    ACTIVO.write_text(json.dumps({"slug": slug, "nombre": nombre},
                                 ensure_ascii=False, indent=2))


def info_de(slug):
    f = MUNDOS / slug / "info.json"
    try:
        return json.loads(f.read_text())
    except Exception:
        return {"nombre": slug, "creado": None, "ultimo_uso": None}


def guardar_info(slug, datos):
    d = MUNDOS / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "info.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2))


def listar():
    act = leer_activo()
    fuera = []
    if WORLD.is_dir():
        i = info_de(act["slug"])
        fuera.append({"slug": act["slug"],
                      "nombre": act.get("nombre") or i.get("nombre") or act["slug"],
                      "activo": True, "bytes": tamano(WORLD),
                      "creado": i.get("creado"),
                      "mtime": WORLD.stat().st_mtime,
                      "tiene_mapa": (WEB / "maps").is_dir()})
    if MUNDOS.is_dir():
        for d in sorted(MUNDOS.iterdir()):
            if not d.is_dir() or d.name.startswith(".") or d.name.startswith("_"):
                continue
            if d.name == act["slug"] or not (d / "mundo" / "level.dat").exists():
                continue
            i = info_de(d.name)
            fuera.append({"slug": d.name, "nombre": i.get("nombre") or d.name,
                          "activo": False, "bytes": tamano(d / "mundo"),
                          "creado": i.get("creado"),
                          "mtime": (d / "mundo").stat().st_mtime,
                          "tiene_mapa": (d / "mapa").is_dir()})
    return fuera


# ------------------------------------------------------- copia consistente
def copia_consistente(destino: Path):
    """Comprime el mundo ACTIVO pausando el guardado, que es la única forma de
    que la copia sirva. En caliente se pillan ficheros de región a medio
    escribir: la copia parece buena y falla el día que la cargas."""
    if not WORLD.is_dir():
        salir(False, "No encuentro %s" % WORLD)
    peso = tamano(WORLD)
    if libre() < peso * 0.7 + 512 * 2**20:
        salir(False, "No hay disco de sobra para la copia: el mundo pesa %s y quedan %s"
                     % (mb(peso), mb(libre())))
    destino.parent.mkdir(parents=True, exist_ok=True)
    vivo = servidor_vivo()
    if vivo:
        decir("pausando el guardado…")
        mc_cmd("save-off")
        mc_cmd("save-all flush")
        time.sleep(10)
    try:
        decir("comprimiendo %s (%s en crudo)…" % (WORLD.name, mb(peso)))
        t0 = time.time()
        r = subprocess.run(["tar", "-czf", str(destino), "-C", str(MC), WORLD.name],
                           capture_output=True, text=True)
        if r.returncode != 0:
            destino.unlink(missing_ok=True)
            decir("⚠ tar falló: %s" % r.stderr[-300:])
            salir(False, "No pude comprimir el mundo")
        decir("listo en %d s, %s" % (time.time() - t0, mb(destino.stat().st_size)))
    finally:
        if vivo:
            mc_cmd("save-on")
            decir("guardado reactivado")
    return destino


# --------------------------------------------------------- versión del mundo
#
# La forma más fácil de dejar el servidor sin arrancar es abrir el mundo en un
# Minecraft más nuevo: el juego le sube el formato de datos y no hay vuelta
# atrás. Por eso, antes de dejar entrar un mundo, se mira su `Data.Version` y se
# compara con la del mundo que corre AHORA. Comparar contra el mundo activo (y
# no contra el nombre del jar) es exacto: es literalmente el número que este
# servidor sabe escribir.
def _version_de_bytes(crudo):
    if nbt is None:
        return {}
    try:
        if crudo[:2] == b"\x1f\x8b":
            crudo = gzip.decompress(crudo)
        _, raiz = nbt.parse(crudo)
        datos = nbt.cget(raiz.v, "Data")
        if datos is None:
            return {}
        fuera = {}
        ver = nbt.cget(datos.v, "Version")
        if ver is not None and isinstance(ver.v, list):
            vid = nbt.cget(ver.v, "Id")
            vnom = nbt.cget(ver.v, "Name")
            if vid is not None:
                fuera["id"] = vid.v
            if vnom is not None:
                fuera["nombre"] = (vnom.v.decode("utf-8", "replace")
                                   if isinstance(vnom.v, bytes) else str(vnom.v))
        if "id" not in fuera:
            dv = nbt.cget(datos.v, "DataVersion")
            if dv is not None:
                fuera["id"] = dv.v
        return fuera
    except Exception:
        return {}


def version_de(level_dat: Path):
    try:
        return _version_de_bytes(level_dat.read_bytes())
    except Exception:
        return {}


def _level_dat_del_archivo(archivo: Path):
    """Saca SOLO el level.dat de un zip/tar sin descomprimir el mundo entero.

    Así se puede avisar de la versión nada más subirlo, sin gastar minutos ni
    gigas en descomprimir algo que a lo mejor hay que rechazar.
    """
    def elegir(nombres):
        cand = [n for n in nombres if n.split("/")[-1] == "level.dat"]
        return min(cand, key=lambda n: n.count("/")) if cand else None
    try:
        if zipfile.is_zipfile(archivo):
            with zipfile.ZipFile(archivo) as z:
                n = elegir(z.namelist())
                return z.read(n) if n else None
        with tarfile.open(archivo) as t:
            nombres = [m.name for m in t.getmembers() if m.isfile()]
            n = elegir(nombres)
            if not n:
                return None
            f = t.extractfile(n)
            return f.read() if f else None
    except Exception:
        return None


def inspeccionar(archivo: Path):
    """Qué hay dentro del archivo subido, antes de decidir qué hacer con él."""
    if not archivo.exists():
        salir(False, "No encuentro el archivo %s" % archivo)
    crudo = _level_dat_del_archivo(archivo)
    if crudo is None:
        salir(False, "Ahí dentro no hay ningún level.dat — eso no es un mundo de Minecraft")
    sube = _version_de_bytes(crudo)
    aqui = version_de(WORLD / "level.dat")
    mas_nueva = bool(sube.get("id") and aqui.get("id") and sube["id"] > aqui["id"])
    act = leer_activo()
    salir(True, "Mundo válido", legible=bool(sube.get("id")),
          version=sube.get("nombre") or "?", version_id=sube.get("id"),
          version_servidor=aqui.get("nombre") or "?", version_servidor_id=aqui.get("id"),
          mas_nueva=mas_nueva, bytes=archivo.stat().st_size,
          activo_slug=act["slug"], activo_nombre=act.get("nombre") or act["slug"])


def avisar_version(crudo_level_dat, forzar):
    """Se planta si el mundo viene de un Minecraft más nuevo que el servidor."""
    if nbt is None:
        # Sin el lector de NBT esta comprobación NO se hace, y es justo la que
        # evita dejar el servidor sin arrancar. Que se vea en el diario en vez
        # de pasar de largo en silencio.
        decir("⚠ no encuentro nbt.py: NO puedo comprobar la versión del mundo")
        return
    sube = _version_de_bytes(crudo_level_dat)
    aqui = version_de(WORLD / "level.dat")
    if forzar or not (sube.get("id") and aqui.get("id")):
        if not forzar:
            decir("⚠ no pude leer la versión (subido=%s, servidor=%s): sigo sin comprobar"
                  % (sube.get("id"), aqui.get("id")))
        return
    if sube["id"] > aqui["id"]:
        salir(False, "Ese mundo viene de Minecraft %s y el servidor es %s. Si lo cargas, "
                     "el servidor no podrá abrirlo y se quedará sin arrancar. Ábrelo en la "
                     "versión del servidor, o súbelo con «forzar» si sabes lo que haces."
                     % (sube.get("nombre") or sube["id"], aqui.get("nombre") or aqui["id"]))


# ------------------------------------------------------------------ importar
def raiz_del_mundo(carpeta: Path):
    """Encuentra la carpeta que contiene level.dat. La gente sube el zip con el
    mundo dentro de otra carpeta tan a menudo como sin ella."""
    if (carpeta / "level.dat").exists():
        return carpeta
    candidatos = sorted(carpeta.rglob("level.dat"), key=lambda p: len(p.parts))
    for p in candidatos:
        return p.parent
    return None


def _ruta_peligrosa(nombre):
    p = Path(nombre)
    return nombre.startswith("/") or p.is_absolute() or ".." in p.parts


def descomprimir_mundo(archivo: Path, tmp: Path):
    """Saca el archivo a `tmp` y devuelve la carpeta que contiene el level.dat.

    Se hace SIEMPRE con el servidor en marcha: si el archivo está roto o no es
    un mundo, se descubre aquí y nadie se ha quedado sin jugar.
    """
    if not archivo.exists():
        salir(False, "No encuentro el archivo %s" % archivo)
    peso = archivo.stat().st_size
    if libre() < peso * 4 + 1 * 2**30:
        salir(False, "No hay disco de sobra: el archivo pesa %s y quedan %s"
                     % (mb(peso), mb(libre())))
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    decir("descomprimiendo %s (%s)…" % (archivo.name, mb(peso)))
    try:
        if zipfile.is_zipfile(archivo):
            with zipfile.ZipFile(archivo) as z:
                # sin esto, un zip preparado a mala fe escribe fuera de la carpeta
                for m in z.namelist():
                    if _ruta_peligrosa(m):
                        raise ValueError("ruta peligrosa dentro del zip: %s" % m)
                z.extractall(tmp)
        else:
            with tarfile.open(archivo) as t:
                for m in t.getmembers():
                    if _ruta_peligrosa(m.name):
                        raise ValueError("ruta peligrosa dentro del tar: %s" % m.name)
                # `data` es el filtro seguro de Python: ni enlaces raros ni permisos
                try:
                    t.extractall(tmp, filter="data")
                except TypeError:              # Python < 3.12
                    t.extractall(tmp)
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        salir(False, "No pude descomprimirlo: %s" % e)

    raiz = raiz_del_mundo(tmp)
    if raiz is None:
        shutil.rmtree(tmp, ignore_errors=True)
        salir(False, "Ahí dentro no hay ningún level.dat — eso no es un mundo de Minecraft")
    # una sesión abierta de otro servidor haría que Minecraft se queje al arrancar
    (raiz / "session.lock").unlink(missing_ok=True)
    return raiz


def importar(archivo: Path, nombre, forzar=False):
    avisar_version(_level_dat_del_archivo(archivo) or b"", forzar)
    slug = slug_libre(slugify(nombre))
    tmp = MUNDOS / (".importando-" + slug)
    raiz = descomprimir_mundo(archivo, tmp)

    destino = MUNDOS / slug
    destino.mkdir(parents=True, exist_ok=True)
    shutil.move(str(raiz), str(destino / "mundo"))
    shutil.rmtree(tmp, ignore_errors=True)
    guardar_info(slug, {"nombre": nombre, "creado": time.time(), "ultimo_uso": None,
                        "version": (version_de(destino / "mundo" / "level.dat")
                                    .get("nombre"))})
    decir("importado como «%s» (%s)" % (nombre, slug))
    salir(True, "«%s» está listo. Ya puedes cambiar a él." % nombre,
          slug=slug, bytes=tamano(destino / "mundo"))


# ------------------------------------------------------------------- cambiar
def _mover(origen: Path, destino: Path):
    """Mueve, y si es entre discos distintos copia y borra."""
    if not origen.exists():
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    try:
        os.rename(origen, destino)
    except OSError:
        shutil.move(str(origen), str(destino))
    return True


def guardar_lo_del_panel(slug):
    """Archiva los azulejos del mapa y los ficheros del panel que son de este
    mundo. Sin esto, al cambiar quedarían mezclados con los del mundo nuevo."""
    d = MUNDOS / slug
    mapas = WEB / "maps"
    if mapas.is_dir():
        if _mover(mapas, d / "mapa"):
            decir("mapa archivado en mundos/%s/mapa" % slug)
    (d / "panel").mkdir(parents=True, exist_ok=True)
    for n in PANEL_POR_MUNDO:
        f = PANEL / "data" / n
        if f.exists():
            shutil.move(str(f), str(d / "panel" / n))
    decir("marcadores y estructuras archivados")


def restaurar_lo_del_panel(slug):
    """Devuelve lo de ese mundo. Si nunca se renderizó, no hay mapa que devolver
    y habrá que dibujarlo entero."""
    d = MUNDOS / slug
    tenia_mapa = (d / "mapa").is_dir()
    if tenia_mapa:
        _mover(d / "mapa", WEB / "maps")
        decir("mapa de «%s» restaurado" % slug)
    else:
        decir("«%s» no tiene mapa todavía: habrá que dibujarlo entero" % slug)
    (PANEL / "data").mkdir(parents=True, exist_ok=True)
    for n in PANEL_POR_MUNDO:
        f = d / "panel" / n
        if f.exists():
            shutil.move(str(f), str(PANEL / "data" / n))
    return tenia_mapa


def lanzar_render(completo):
    render = PANEL / "scripts" / "render-mapa.sh"
    if not render.exists():
        decir("⚠ no encuentro render-mapa.sh: el mapa habrá que lanzarlo a mano")
        return False
    args = ["bash", str(render)] + (["--completo"] if completo else [])
    decir("lanzando el render del mapa (%s)…"
          % ("COMPLETO, tarda horas" if completo else "incremental"))
    # start_new_session: que sobreviva a que el panel se reinicie
    subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


def apartar_el_activo(act):
    """Deja el mundo que estaba en marcha guardado en mundos/<slug>/."""
    guardar_info(act["slug"], dict(info_de(act["slug"]),
                                   nombre=act.get("nombre") or act["slug"],
                                   ultimo_uso=time.time()))
    guardar_lo_del_panel(act["slug"])
    if not _mover(WORLD, MUNDOS / act["slug"] / "mundo"):
        raise RuntimeError("no pude apartar el mundo activo")
    decir("«%s» guardado" % (act.get("nombre") or act["slug"]))


def cambiar(slug):
    comprobar_level_name()
    act = leer_activo()
    if slug == act["slug"]:
        salir(False, "«%s» ya es el mundo activo" % (act.get("nombre") or slug))
    nuevo = MUNDOS / slug / "mundo"
    if not nuevo.is_dir():
        salir(False, "No existe el mundo «%s»" % slug)
    if not (nuevo / "level.dat").exists():
        salir(False, "«%s» no tiene level.dat: no es un mundo válido" % slug)

    info_nuevo = info_de(slug)
    decir("═" * 58)
    decir("CAMBIO DE MUNDO: «%s» → «%s»" % (act.get("nombre"), info_nuevo.get("nombre")))
    decir("═" * 58)

    if not parar_servidor():
        salir(False, "No pude parar el servidor; no he tocado nada")

    try:
        apartar_el_activo(act)
        if not _mover(nuevo, WORLD):
            # intento dejarlo como estaba
            _mover(MUNDOS / act["slug"] / "mundo", WORLD)
            restaurar_lo_del_panel(act["slug"])
            raise RuntimeError("no pude colocar el mundo nuevo; lo he dejado como estaba")
        tenia_mapa = restaurar_lo_del_panel(slug)
        guardar_activo(slug, info_nuevo.get("nombre") or slug)
        guardar_info(slug, dict(info_nuevo, ultimo_uso=time.time()))
        decir("«%s» es ahora el mundo activo" % info_nuevo.get("nombre"))
    except Exception as e:
        decir("⚠ %s" % e)
        arrancar_servidor()
        salir(False, str(e))

    arrancar_servidor()
    lanzar_render(completo=not tenia_mapa)
    salir(True, "Ahora estás en «%s»" % (info_nuevo.get("nombre") or slug),
          slug=slug, mapa_completo=not tenia_mapa)


# --------------------------------------------------------------- mundo nuevo
def nuevo(nombre, semilla=""):
    """Aparta el mundo de ahora y deja que Minecraft genere uno desde cero."""
    comprobar_level_name()
    act = leer_activo()
    slug = slug_libre(slugify(nombre))
    decir("═" * 58)
    decir("MUNDO NUEVO: «%s» (%s)%s" % (nombre, slug,
                                        ", semilla %s" % semilla if semilla else ""))
    decir("═" * 58)

    if WORLD.is_dir() and not parar_servidor():
        salir(False, "No pude parar el servidor; no he tocado nada")

    try:
        if WORLD.is_dir():
            apartar_el_activo(act)
        else:
            guardar_lo_del_panel(act["slug"])
    except Exception as e:
        decir("⚠ %s" % e)
        arrancar_servidor()
        salir(False, str(e))

    # La semilla se escribe SIEMPRE, vacía incluida: si no, la del mundo
    # anterior se quedaría puesta y el siguiente mundo nuevo saldría igual.
    poner_prop("level-seed", semilla or "")
    guardar_info(slug, {"nombre": nombre, "creado": time.time(),
                        "ultimo_uso": time.time(),
                        "semilla": semilla or None})
    guardar_activo(slug, nombre)
    decir("no hay carpeta `world`: Minecraft generará el mundo al arrancar")
    arrancar_servidor()

    # esperamos a que exista para no lanzar el render sobre la nada
    for _ in range(60):
        if (WORLD / "level.dat").exists():
            break
        time.sleep(2)
    if not (WORLD / "level.dat").exists():
        decir("⚠ el mundo aún no aparece; el mapa se dibujará esta noche")
        salir(True, "«%s» creado. El mundo se está generando." % nombre, slug=slug)
    decir("mundo generado")
    lanzar_render(completo=True)
    salir(True, "Estrenando «%s». El mapa se dibuja entero, tarda un rato." % nombre,
          slug=slug, mapa_completo=True)


# ----------------------------------------------------------------- reemplazar
def reemplazar(archivo: Path, forzar=False):
    """Mete una versión nueva del MISMO mundo, conservando su identidad.

    Es el caso de «me bajo el mundo, le construyo cosas en creativo y lo vuelvo
    a subir». La diferencia con importar+cambiar es el mapa: aquí NO se archiva
    ni se borra, se deja donde está, porque los azulejos siguen siendo de este
    mundo. BlueMap solo redibuja las regiones que cambiaron —minutos— en vez de
    dibujar el mundo entero otra vez —horas—. Los marcadores y el escaneo de
    estructuras, igual.

    La versión anterior no se tira: queda guardada en la lista como un mundo
    más, por si la construcción sale mal.
    """
    comprobar_level_name()
    avisar_version(_level_dat_del_archivo(archivo) or b"", forzar)
    act = leer_activo()
    nombre_act = act.get("nombre") or act["slug"]

    # se descomprime ANTES de tocar el servidor: si el archivo está roto, nadie
    # se queda sin jugar
    tmp = MUNDOS / ".reemplazando"
    raiz = descomprimir_mundo(archivo, tmp)

    decir("═" * 58)
    decir("NUEVA VERSIÓN DE «%s»" % nombre_act)
    decir("═" * 58)
    if WORLD.is_dir() and not parar_servidor():
        shutil.rmtree(tmp, ignore_errors=True)
        salir(False, "No pude parar el servidor; no he tocado nada")

    sello = time.strftime("%Y%m%d-%H%M")
    viejo = slug_libre("%s-antes-%s" % (act["slug"], sello))
    try:
        if WORLD.is_dir():
            # OJO: aquí NO se llama a guardar_lo_del_panel. El mapa y los
            # marcadores se quedan puestos porque siguen siendo de este mundo.
            if not _mover(WORLD, MUNDOS / viejo / "mundo"):
                raise RuntimeError("no pude apartar la versión anterior")
            guardar_info(viejo, {"nombre": "%s · antes del %s" % (nombre_act, sello),
                                 "creado": time.time(), "ultimo_uso": time.time()})
            decir("versión anterior guardada como «%s»" % viejo)
        if not _mover(raiz, WORLD):
            _mover(MUNDOS / viejo / "mundo", WORLD)
            raise RuntimeError("no pude colocar la versión nueva; la he dejado como estaba")
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        decir("⚠ %s" % e)
        arrancar_servidor()
        salir(False, str(e))
    shutil.rmtree(tmp, ignore_errors=True)

    guardar_info(act["slug"], dict(info_de(act["slug"]), nombre=nombre_act,
                                   ultimo_uso=time.time(),
                                   version=version_de(WORLD / "level.dat").get("nombre")))
    guardar_activo(act["slug"], nombre_act)      # que el estado quede siempre escrito
    arrancar_servidor()
    lanzar_render(completo=False)
    salir(True, "«%s» actualizado. El mapa solo redibuja lo que cambiaste." % nombre_act,
          slug=act["slug"], respaldo=viejo, mapa_completo=False)


# ------------------------------------------------------------ jugadores
# Qué carpeta del mundo guarda cada cosa.
# OJO con las rutas: la 26.x movió las carpetas del jugador dentro de
# `world/players/`. Antes estaban sueltas en la raíz del mundo. Se prueban las
# dos, porque un mundo importado puede venir del formato viejo — y porque con la
# ruta equivocada esto no falla, simplemente copia cero ficheros.
QUE_JUGADORES = {
    "logros":     (("players/advancements", "advancements"), "*.json", "logros"),
    "stats":      (("players/stats", "stats"),               "*.json", "estadísticas"),
    "inventario": (("players", "playerdata"),                "*.dat",  "inventario y experiencia"),
}


def carpeta_jugadores(raiz: Path, candidatas):
    """La primera que exista; si no hay ninguna, la primera (formato nuevo)."""
    for c in candidatas:
        if (raiz / c).is_dir():
            return raiz / c
    return raiz / candidatas[0]


def _cset(items, clave, tag):
    kb = clave.encode()
    for i, (n, _) in enumerate(items):
        if n == kb:
            items[i] = [kb, tag]
            return
    items.append([kb, tag])


def _spawn_del_mundo():
    try:
        _, raiz, _ = nbt.load(str(WORLD / "level.dat"))
        d = nbt.cget(raiz.v, "Data").v
        return (nbt.cget(d, "SpawnX").v, nbt.cget(d, "SpawnY").v, nbt.cget(d, "SpawnZ").v)
    except Exception:
        return None


def _reubicar_jugador(dat: Path, spawn):
    """Deja al jugador en el spawn del mundo nuevo.

    Sin esto, el .dat trae las coordenadas del mundo VIEJO y en el nuevo eso
    puede ser el interior de una montaña, el fondo del mar o el vacío. Se
    reescribe la posición en vez de borrarla: un jugador sin `Pos` es un
    jugador que el juego no sabe dónde poner.
    """
    if nbt is None or not spawn:
        return False
    try:
        nombre, raiz, gz = nbt.load(str(dat))
        it = raiz.v
        x, y, z = spawn
        _cset(it, "Pos", nbt.Tag(nbt.TAG_LIST,
                                 nbt.NList(nbt.TAG_DOUBLE, [x + 0.5, float(y), z + 0.5])))
        _cset(it, "Motion", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_DOUBLE, [0.0, 0.0, 0.0])))
        _cset(it, "Rotation", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_FLOAT, [0.0, 0.0])))
        _cset(it, "FallDistance", nbt.Tag(nbt.TAG_FLOAT, 0.0))
        # la dimensión cambió de número a texto en 1.16: se respeta lo que haya
        dim = nbt.cget(it, "Dimension")
        if dim is not None:
            _cset(it, "Dimension", nbt.Tag(dim.t, 0 if dim.t == nbt.TAG_INT
                                           else b"minecraft:overworld"))
        # cama, portal y muerte del mundo viejo: todo eso son coordenadas de allí
        for k in ("SpawnX", "SpawnY", "SpawnZ", "SpawnAngle", "SpawnDimension",
                  "SpawnForced", "enteredNetherPosition", "LastDeathLocation",
                  "RootVehicle", "Sleeping", "SleepTimer"):
            nbt.cdel(it, k)
        nbt.save(str(dat), nombre, raiz, gz)
        return True
    except Exception as e:
        decir("⚠ no pude reubicar %s: %s" % (dat.name, e))
        return False


def jugadores(origen, que):
    """Trae logros / estadísticas / inventario de un mundo guardado al activo."""
    act = leer_activo()
    if origen == act["slug"]:
        salir(False, "Ese ya es el mundo activo")
    org = MUNDOS / origen / "mundo"
    if not (org / "level.dat").exists():
        salir(False, "No existe el mundo «%s»" % origen)
    que = [q for q in que if q in QUE_JUGADORES]
    if not que:
        salir(False, "No has elegido qué traer")
    if not WORLD.is_dir():
        salir(False, "No hay mundo activo")

    nombre_org = info_de(origen).get("nombre", origen)
    decir("═" * 58)
    decir("TRAER JUGADORES: «%s» → «%s» (%s)"
          % (nombre_org, act.get("nombre") or act["slug"], ", ".join(que)))
    decir("═" * 58)

    # El servidor TIENE que estar parado: con gente dentro, Minecraft guarda su
    # estado en memoria encima de estos ficheros al salir y el traspaso se
    # borraría solo sin decir nada.
    if not parar_servidor():
        salir(False, "No pude parar el servidor; no he tocado nada")

    sello = time.strftime("%Y%m%d-%H%M")
    resguardo = MUNDOS / act["slug"] / "panel" / ("jugadores-antes-" + sello)
    cuenta, tocados = {}, set()
    try:
        for clave in que:
            subs, patron, _ = QUE_JUGADORES[clave]
            desde = carpeta_jugadores(org, subs)
            hacia = carpeta_jugadores(WORLD, subs)
            if not desde.is_dir():
                cuenta[clave] = 0
                decir("%s: el mundo de origen no tiene esa carpeta" % QUE_JUGADORES[clave][2])
                continue
            hacia.mkdir(parents=True, exist_ok=True)
            sub = hacia.name
            n = 0
            for f in sorted(desde.glob(patron)):
                previo = hacia / f.name
                if previo.exists():                      # red de seguridad
                    (resguardo / sub).mkdir(parents=True, exist_ok=True)
                    shutil.copy2(previo, resguardo / sub / f.name)
                shutil.copy2(f, previo)
                if clave == "inventario":
                    tocados.add(previo)
                n += 1
            cuenta[clave] = n
            decir("%s: %d ficheros" % (QUE_JUGADORES[clave][2], n))

        if tocados:
            spawn = _spawn_del_mundo()
            if spawn:
                decir("reubicando %d jugadores en el spawn %s…" % (len(tocados), spawn))
                for f in sorted(tocados):
                    _reubicar_jugador(f, spawn)
            else:
                decir("⚠ no pude leer el spawn: dejo las posiciones como estaban")
    except Exception as e:
        decir("⚠ %s" % e)
        arrancar_servidor()
        salir(False, "Falló a medias: %s. Lo de antes está en %s" % (e, resguardo))

    arrancar_servidor()
    resumen = ", ".join("%d %s" % (cuenta[c], QUE_JUGADORES[c][2]) for c in que)
    salir(True, "Traídos desde «%s»: %s" % (nombre_org, resumen),
          cuenta=cuenta, resguardo=str(resguardo) if resguardo.exists() else None)


# -------------------------------------------------------------------- borrar
def borrar(slug):
    act = leer_activo()
    if slug == act["slug"]:
        salir(False, "No puedo borrar el mundo activo. Cambia a otro primero.")
    d = MUNDOS / slug
    if not d.is_dir():
        salir(False, "No existe «%s»" % slug)
    nombre = info_de(slug).get("nombre", slug)
    destino = PAPELERA / ("%s-%s" % (time.strftime("%Y%m%d-%H%M"), slug))
    destino.parent.mkdir(parents=True, exist_ok=True)
    if destino.exists():
        destino = Path(str(destino) + "-%d" % int(time.time() % 1000))
    shutil.move(str(d), str(destino))
    decir("«%s» movido a %s" % (nombre, destino))
    salir(True, "«%s» quitado de la lista. Sigue en el disco, en mundos/_borrados/, "
                "por si acaso." % nombre, papelera=tamano(PAPELERA))


def vaciar_papelera():
    if not PAPELERA.is_dir():
        salir(True, "La papelera ya está vacía", liberado=0)
    antes = tamano(PAPELERA)
    shutil.rmtree(PAPELERA, ignore_errors=True)
    decir("papelera vaciada, %s libres" % mb(antes))
    salir(True, "Papelera vaciada: %s libres" % mb(antes), liberado=antes)


# ---------------------------------------------------------------------- main
def main():
    banderas = {"--json", "--forzar"}
    con_valor = {"--nombre", "--semilla", "--que"}
    args, saltar = [], False
    for a in sys.argv[1:]:
        if saltar:
            saltar = False
            continue
        if a in con_valor:
            saltar = True
            continue
        if a.startswith("--"):
            if a not in banderas and "=" not in a:
                pass
            continue
        args.append(a)
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]

    if cmd == "listar":
        datos = listar()
        if JSON_MODE:
            print(json.dumps({"ok": True, "mundos": datos, "libre": libre(),
                              "papelera": tamano(PAPELERA)}, ensure_ascii=False))
        else:
            for m in datos:
                print("  %s %-24s %-32s %10s" %
                      ("●" if m["activo"] else " ", m["slug"], m["nombre"],
                       mb(m["bytes"])))
            print("  ── libre en disco: %s · papelera: %s"
                  % (mb(libre()), mb(tamano(PAPELERA))))
        return 0

    if cmd == "copia":
        if len(args) < 2:
            salir(False, "Falta el destino: mundos.py copia /tmp/mundo.tar.gz")
        with Candado():
            copia_consistente(Path(args[1]))
        salir(True, "Copia hecha", ruta=args[1],
              bytes=Path(args[1]).stat().st_size)

    if cmd == "inspeccionar":
        if len(args) < 2:
            salir(False, "Falta el archivo")
        inspeccionar(Path(args[1]))

    if cmd == "importar":
        if len(args) < 2:
            salir(False, "Falta el archivo")
        nombre = opcion("--nombre") or Path(args[1]).stem
        with Candado():
            importar(Path(args[1]), nombre, forzar="--forzar" in sys.argv)

    if cmd == "reemplazar":
        if len(args) < 2:
            salir(False, "Falta el archivo")
        with Candado():
            reemplazar(Path(args[1]), forzar="--forzar" in sys.argv)

    if cmd == "jugadores":
        if len(args) < 2:
            salir(False, "Falta el mundo del que traerlos")
        que = [q.strip() for q in (opcion("--que") or "logros,stats").split(",") if q.strip()]
        with Candado():
            jugadores(args[1], que)

    if cmd == "nuevo":
        nombre = opcion("--nombre") or (args[1] if len(args) > 1 else None)
        if not nombre:
            salir(False, "Falta el nombre: mundos.py nuevo --nombre \"Temporada 2\"")
        semilla = re.sub(r"[^\w -]", "", opcion("--semilla") or "")[:48]
        with Candado():
            nuevo(nombre, semilla)

    if cmd == "cambiar":
        if len(args) < 2:
            salir(False, "Falta el mundo al que cambiar")
        with Candado():
            cambiar(args[1])

    if cmd == "borrar":
        if len(args) < 2:
            salir(False, "Falta el mundo a borrar")
        with Candado():
            borrar(args[1])

    if cmd == "vaciar-papelera":
        with Candado():
            vaciar_papelera()

    salir(False, "No entiendo «%s»" % cmd)


if __name__ == "__main__":
    sys.exit(main())
