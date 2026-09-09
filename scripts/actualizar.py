#!/usr/bin/env python3
"""
Mantiene Minecraft al día solo, y el mapa a salvo mientras tanto.

POR QUÉ ES SEGURO CAMBIAR EL JAR
--------------------------------
El servicio arranca con `screen -DmS mc java ... -jar server.jar nogui`, o sea
un nombre FIJO. Actualizar es cambiar ese fichero y reiniciar: no hay que tocar
systemd ni pedir más permisos que el `systemctl restart` que el panel ya tiene.
Lo que Mojang publica es un jar «bundler» que al arrancar se descomprime él solo
en `versions/<ver>/`, así que basta con ponerlo en su sitio.

Y como el jar viejo se guarda, si el server no levanta se devuelve solo.

EL MAPA SE CONGELA A PROPÓSITO
------------------------------
BlueMap va por detrás de Minecraft: cuando sale una versión, tarda días o
semanas en soportarla. Si se le dejara correr contra un mundo que no entiende,
podría escribir azulejos malos ENCIMA de los buenos y perderías el mapa que ya
tenías.

Así que al actualizar se deja una nota en `data/mapa-congelado.json` y
`render-mapa.sh` se para en seco al verla. El mapa se queda tal cual estaba —
completo, con el terreno de la versión anterior— y se sigue viendo.

Para descongelarlo está `mapa-al-dia`: mira si hay un BlueMap nuevo, lo pone, y
prueba a renderizar. Si sale bien, quita la nota y todo vuelve a la normalidad.
Si BlueMap todavía no puede, deja la nota puesta, devuelve el BlueMap de antes y
no toca ni un azulejo. Se puede intentar tantas veces como haga falta.

OJO: el render que lanza es el NORMAL, no `--completo`. Los azulejos viejos
siguen siendo válidos —el terreno de antes no ha cambiado—, así que solo hay que
dibujar lo nuevo. Además de tardar minutos en vez de horas, esto hace que el
mapa nunca se quede en blanco: los azulejos se van reemplazando en su sitio.

Uso (en el servidor):
    python3 ~/panel/scripts/actualizar.py comprobar
    python3 ~/panel/scripts/actualizar.py actualizar [--a 26.3] [--forzar]
    python3 ~/panel/scripts/actualizar.py mapa-estado
    python3 ~/panel/scripts/actualizar.py mapa-al-dia
    python3 ~/panel/scripts/actualizar.py deshacer

Con `--json` escupe JSON (es lo que usa el panel).
"""
import hashlib, json, os, re, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

HOME  = Path.home()
MC    = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
PANEL = Path(os.environ.get("PANEL_DIR", HOME / "panel"))
BM    = Path(os.environ.get("BLUEMAP_DIR", HOME / "bluemap"))
JAR       = MC / "server.jar"
GUARDADOS = MC / "actualizaciones"
LOG       = MC / "actualizar.log"
LOCK      = MC / ".actualizar.lock"
CONGELADO = PANEL / "data" / "mapa-congelado.json"
SERVICIO  = os.environ.get("MC_SERVICE", "minecraft")
SCREEN    = os.environ.get("MC_SCREEN", "mc")

MANIFIESTO = os.environ.get(
    "MC_MANIFIESTO",
    "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
BM_RELEASES = os.environ.get(
    "BLUEMAP_RELEASES",
    "https://api.github.com/repos/BlueMap-Minecraft/BlueMap/releases/latest")

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


def bajar(url, timeout=60):
    pet = urllib.request.Request(url, headers={"User-Agent": "panel-califree"})
    with urllib.request.urlopen(pet, timeout=timeout) as r:
        return r.read()


class Candado:
    def __enter__(self):
        if LOCK.exists() and time.time() - LOCK.stat().st_mtime < 3600:
            salir(False, "Ya hay una actualización en marcha")
        LOCK.parent.mkdir(parents=True, exist_ok=True)
        LOCK.write_text(str(os.getpid()))
        return self

    def __exit__(self, *a):
        LOCK.unlink(missing_ok=True)


# ------------------------------------------------------------ qué versión hay
def version_instalada():
    """La versión que el servidor está corriendo de verdad.

    Se mira por FECHA y no por nombre: ordenar «26.10» y «26.2» como texto pone
    la 26.10 primero, y a partir de la décima versión de un año eso elegiría mal.
    La carpeta que el bundler acaba de escribir es siempre la más reciente.
    """
    try:
        jars = list((MC / "versions").glob("*/server-*.jar"))
        if jars:
            j = max(jars, key=lambda p: p.stat().st_mtime)
            m = re.search(r"server-(.+)\.jar$", j.name)
            if m:
                return m.group(1)
        d = sorted((MC / "versions").glob("*"), key=lambda p: p.stat().st_mtime)
        return d[-1].name if d else None
    except Exception:
        return None


def ultima_de_mojang():
    """La última versión ESTABLE. Las snapshots se ignoran a propósito: un
    servidor con amigos no quiere ser el conejillo de indias de Mojang."""
    m = json.loads(bajar(MANIFIESTO))
    id_ = m["latest"]["release"]
    for v in m["versions"]:
        if v["id"] == id_:
            return id_, v["url"], v.get("releaseTime")
    return id_, None, None


def datos_del_jar(url_version):
    v = json.loads(bajar(url_version))
    s = v.get("downloads", {}).get("server") or {}
    return s.get("url"), s.get("sha1"), s.get("size")


def comprobar():
    inst = version_instalada()
    try:
        ult, url, cuando = ultima_de_mojang()
    except Exception as e:
        salir(False, "No pude preguntarle a Mojang: %s" % e, instalada=inst)
    salir(True, ("Hay una versión nueva: %s" % ult) if ult != inst else "Estás al día",
          instalada=inst, ultima=ult, hay_nueva=(ult != inst and bool(inst)),
          publicada=cuando, congelado=leer_congelado())


# --------------------------------------------------------- hablar con el juego
def mc_cmd(cmd):
    try:
        r = subprocess.run(["screen", "-p", "0", "-S", SCREEN, "-X", "eval",
                            'stuff "%s\\015"' % cmd],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False


def servidor_vivo():
    try:
        r = subprocess.run(["pgrep", "-f", "server.jar"], capture_output=True,
                           text=True, timeout=10)
        return bool(r.stdout.strip())
    except Exception:
        return False


def parar_servidor(espera=180):
    if not servidor_vivo():
        return True
    decir("guardando y parando el servidor…")
    mc_cmd("save-all flush")
    time.sleep(6)
    if not mc_cmd("stop"):
        decir("⚠ la consola no responde")
        return False
    t0 = time.time()
    while time.time() - t0 < espera:
        if not servidor_vivo():
            time.sleep(3)
            return True
        time.sleep(2)
    return False


def arrancar_y_esperar(espera=420):
    """Arranca y espera al «Done» del log, no solo a que haya un proceso.

    Un servidor que no puede con el mundo ARRANCA: el proceso existe unos
    segundos y luego se cae. Mirar solo `pgrep` daría por bueno un arranque
    fallido y no se desharía el cambio.
    """
    latest = MC / "logs" / "latest.log"
    antes = latest.stat().st_mtime if latest.exists() else 0
    subprocess.run(["sudo", "-n", "systemctl", "restart", SERVICIO],
                   capture_output=True, text=True, timeout=90)
    t0 = time.time()
    while time.time() - t0 < espera:
        time.sleep(3)
        try:
            if latest.exists() and latest.stat().st_mtime >= antes:
                texto = latest.read_text(errors="replace")[-20000:]
                if "]: Done (" in texto:
                    decir("servidor arrancado en %d s" % (time.time() - t0))
                    return True, ""
                for malo in ("which is not compatible", "Failed to start the minecraft server",
                             "world is from a newer version", "UnsupportedClassVersionError"):
                    if malo.lower() in texto.lower():
                        return False, malo
        except Exception:
            pass
        if not servidor_vivo() and time.time() - t0 > 30:
            return False, "el proceso se cayó al arrancar"
    return False, "no terminó de arrancar en %d s" % espera


# ------------------------------------------------------------- congelar mapa
def congelar(version, motivo):
    CONGELADO.parent.mkdir(parents=True, exist_ok=True)
    CONGELADO.write_text(json.dumps(
        {"desde": time.time(), "version": version, "motivo": motivo},
        ensure_ascii=False, indent=2))
    decir("mapa congelado: %s" % motivo)


def leer_congelado():
    try:
        return json.loads(CONGELADO.read_text())
    except Exception:
        return None


# ------------------------------------------------------------------ actualizar
def actualizar(destino=None, forzar=False):
    inst = version_instalada()
    ult, url_ver, _ = ultima_de_mojang() if not destino else (destino, None, None)
    if not url_ver:
        m = json.loads(bajar(MANIFIESTO))
        url_ver = next((v["url"] for v in m["versions"] if v["id"] == ult), None)
    if not url_ver:
        salir(False, "No encuentro la versión %s en el manifiesto de Mojang" % ult)
    if inst == ult and not forzar:
        salir(True, "Ya estás en la %s" % ult, instalada=inst, cambiado=False)

    url_jar, sha1, peso = datos_del_jar(url_ver)
    if not url_jar:
        salir(False, "Esa versión no publica jar de servidor")

    decir("═" * 58)
    decir("ACTUALIZAR MINECRAFT: %s → %s" % (inst, ult))
    decir("═" * 58)

    # 1) copia de seguridad ANTES de nada. Actualizar convierte el mundo y eso
    #    no tiene vuelta atrás: esta copia es la única marcha atrás real.
    guion = MC / "backup.sh"
    if guion.exists():
        decir("copia de seguridad antes de tocar nada…")
        r = subprocess.run(["bash", str(guion)], capture_output=True, text=True,
                           timeout=3600)
        decir("copia %s" % ("hecha" if r.returncode == 0 else "FALLÓ — sigo igualmente"))
    else:
        decir("⚠ no encuentro backup.sh: sigo sin copia")

    # 2) descargar y comprobar la firma
    GUARDADOS.mkdir(parents=True, exist_ok=True)
    nuevo = GUARDADOS / ("server-%s.jar" % ult)
    decir("descargando %s (%.0f MB)…" % (ult, (peso or 0) / 2**20))
    try:
        crudo = bajar(url_jar, timeout=600)
    except Exception as e:
        salir(False, "No pude descargar el jar: %s" % e)
    if sha1 and hashlib.sha1(crudo).hexdigest() != sha1:
        salir(False, "El jar descargado no cuadra con su firma — no lo instalo")
    nuevo.write_bytes(crudo)
    decir("descargado y verificado")

    # 3) cambiar el jar, guardando el de antes
    if not parar_servidor():
        salir(False, "No pude parar el servidor; no he tocado nada")
    anterior = GUARDADOS / ("anterior-%s.jar" % (inst or "desconocida"))
    if JAR.exists():
        shutil.copy2(JAR, anterior)
    shutil.copy2(nuevo, JAR)
    decir("jar cambiado (el de antes, en %s)" % anterior.name)

    # 4) el mapa se congela ANTES de arrancar: en cuanto el mundo se convierta,
    #    BlueMap podría no entenderlo, y el cron nocturno no debe pillarlo
    congelar(ult, "Minecraft pasó a la %s y BlueMap puede tardar en soportarla" % ult)

    bien, porque = arrancar_y_esperar()
    if not bien:
        decir("⚠ no arrancó (%s): devuelvo la versión anterior" % porque)
        parar_servidor()
        if anterior.exists():
            shutil.copy2(anterior, JAR)
            # Y se borra: `deshacer` se queda con el jar guardado más reciente, y
            # si un intento fallido dejara el suyo, «deshacer» devolvería la
            # versión en la que ya estás en vez de la de antes de actualizar.
            anterior.unlink(missing_ok=True)
        CONGELADO.unlink(missing_ok=True)
        ok2, _ = arrancar_y_esperar()
        salir(False, "La %s no arrancó (%s). He devuelto la %s y %s."
                     % (ult, porque, inst, "ya está en marcha" if ok2 else
                        "NO levanta: míralo con systemctl status " + SERVICIO),
              instalada=version_instalada(), cambiado=False)

    decir("Minecraft %s en marcha" % ult)
    tareas_de_despues()
    salir(True, "Actualizado a la %s. El mapa se queda como estaba hasta que "
                "BlueMap la soporte." % ult,
          instalada=version_instalada(), anterior=inst, cambiado=True,
          congelado=leer_congelado())


def tareas_de_despues():
    """Lo que queda desincronizado al cambiar de versión.

    Sin esto, una actualización «automática» te deja iconos rotos en el panel y
    el datapack de vigilancia mudo, y no te enteras hasta semanas después.
    """
    iconos = PANEL / "get-icons.py"
    if iconos.exists():
        decir("regenerando los iconos de ítems (bloques nuevos)…")
        r = subprocess.run(["python3", str(iconos)], capture_output=True,
                           text=True, timeout=1800)
        decir("iconos %s" % ("al día" if r.returncode == 0 else "FALLARON"))
    vig = PANEL / "scripts" / "vigilancia.py"
    if vig.exists():
        decir("regenerando el datapack de vigilancia (lee el formato del jar nuevo)…")
        r = subprocess.run(["python3", str(vig)], capture_output=True,
                           text=True, timeout=900)
        decir("vigilancia %s" % ("al día" if r.returncode == 0 else "FALLÓ"))

    # El lector de biomas está COMPILADO CONTRA EL JAR de antes. Con el jar
    # nuevo hay que recompilarlo, y eso lo hace él solo al arrancar: aquí basta
    # con reiniciarlo. Si no se hiciera, el mapa del mundo entero seguiría
    # dibujando los biomas de la versión vieja sin decir nada — que es
    # exactamente la clase de error que no se ve.
    if (PANEL / "scripts" / "biomas-servicio.sh").exists():
        decir("reiniciando el lector de biomas (se recompila con el jar nuevo)…")
        r = subprocess.run(["sudo", "-n", "systemctl", "restart", "biomas"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode == 0:
            decir("lector de biomas reiniciado")
        else:
            decir("el lector de biomas no se pudo reiniciar (%s). "
                  "Si nunca lo instalaste, es normal: "
                  "sudo bash ~/panel/scripts/biomas-instalar.sh"
                  % (r.stderr or "").strip()[:120])
    # Los azulejos ya dibujados del mapa de biomas no hay que tocarlos a mano:
    # se guardan bajo la huella del generador, y si la versión nueva cambiara
    # cómo se reparten los biomas, la huella cambia y se dibujan de nuevo solos.


def deshacer():
    """Devuelve el jar anterior. El MUNDO no se deshace: para eso está la copia
    de seguridad que se hizo justo antes."""
    guardados = sorted(GUARDADOS.glob("anterior-*.jar"),
                       key=lambda p: p.stat().st_mtime) if GUARDADOS.is_dir() else []
    if not guardados:
        salir(False, "No tengo ningún jar anterior guardado")
    ant = guardados[-1]
    ver = re.search(r"anterior-(.+)\.jar$", ant.name).group(1)
    if not parar_servidor():
        salir(False, "No pude parar el servidor")
    shutil.copy2(ant, JAR)
    bien, porque = arrancar_y_esperar()
    salir(bien, ("Vuelto a la %s. OJO: el MUNDO sigue convertido — si no arranca, "
                 "restaura una copia de seguridad de antes de actualizar." % ver)
          if bien else "Devolví la %s pero no arranca (%s)" % (ver, porque),
          instalada=version_instalada())


# --------------------------------------------------------------- el mapa
def bluemap_instalada():
    try:
        return (BM / ".version").read_text().strip()
    except Exception:
        return None


def bluemap_ultima():
    d = json.loads(bajar(BM_RELEASES))
    tag = (d.get("tag_name") or "").lstrip("v")
    for a in d.get("assets", []):
        if a["name"].endswith("-cli.jar"):
            return tag, a["browser_download_url"]
    return tag, None


def mapa_estado():
    c = leer_congelado()
    inst = bluemap_instalada()
    ult = None
    try:
        ult, _ = bluemap_ultima()
    except Exception:
        pass
    salir(True, "El mapa está congelado" if c else "El mapa va al día",
          congelado=c, bluemap=inst, bluemap_ultima=ult,
          hay_bluemap_nuevo=bool(ult and inst and ult != inst),
          version=version_instalada())


def mapa_al_dia():
    """Intenta poner el mapa al día con la versión de Minecraft de ahora.

    Es idempotente y no destruye nada: si BlueMap todavía no puede, se queda
    todo exactamente como estaba y se puede reintentar mañana.
    """
    c = leer_congelado()
    if not c:
        salir(True, "El mapa no está congelado: no hay nada que hacer", congelado=None)

    jar = BM / "bluemap-cli.jar"
    guardado = None
    # 1) ¿hay un BlueMap más nuevo? Si lo hay, se prueba con él.
    try:
        ult, url = bluemap_ultima()
        if url and ult != bluemap_instalada():
            decir("bajando BlueMap %s…" % ult)
            crudo = bajar(url, timeout=600)
            if jar.exists():
                guardado = BM / "bluemap-cli.jar.anterior"
                shutil.copy2(jar, guardado)
            jar.write_bytes(crudo)
            (BM / ".version").write_text(ult)
            decir("BlueMap %s instalado" % ult)
        else:
            decir("no hay BlueMap más nuevo (%s): pruebo igualmente con el que hay" % ult)
    except Exception as e:
        decir("⚠ no pude mirar si hay BlueMap nuevo: %s" % e)

    # 2) probar el render de verdad. Se quita la nota primero porque
    #    render-mapa.sh se para en seco si la ve.
    CONGELADO.unlink(missing_ok=True)
    guion = PANEL / "scripts" / "render-mapa.sh"
    if not guion.exists():
        congelar(c.get("version"), c.get("motivo", ""))
        salir(False, "No encuentro render-mapa.sh")
    decir("probando a dibujar el mapa…")
    r = subprocess.run(["bash", str(guion)], capture_output=True, text=True,
                       timeout=6 * 3600)
    if r.returncode == 0:
        if guardado:
            guardado.unlink(missing_ok=True)
        decir("el mapa está al día con la %s" % version_instalada())
        salir(True, "Mapa actualizado a la %s" % version_instalada(),
              congelado=None, bluemap=bluemap_instalada())

    # 3) no pudo: se deja TODO como estaba. Ni un azulejo tocado.
    decir("⚠ BlueMap todavía no puede con la %s (código %d)"
          % (version_instalada(), r.returncode))
    if guardado and guardado.exists():
        shutil.copy2(guardado, jar)
        guardado.unlink(missing_ok=True)
        decir("BlueMap devuelto a la versión de antes")
    congelar(c.get("version"), c.get("motivo", ""))
    salir(False, "BlueMap todavía no soporta la %s. El mapa se queda como estaba; "
                 "vuelve a intentarlo en unos días." % version_instalada(),
          congelado=leer_congelado(), bluemap=bluemap_instalada())


# ---------------------------------------------------------------------- main
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    if cmd == "comprobar":
        comprobar()
    if cmd == "mapa-estado":
        mapa_estado()
    if cmd == "actualizar":
        destino = None
        for i, a in enumerate(sys.argv):
            if a == "--a" and i + 1 < len(sys.argv):
                destino = sys.argv[i + 1]
        with Candado():
            actualizar(destino, "--forzar" in sys.argv)
    if cmd == "mapa-al-dia":
        with Candado():
            mapa_al_dia()
    if cmd == "deshacer":
        with Candado():
            deshacer()
    salir(False, "No entiendo «%s»" % cmd)


if __name__ == "__main__":
    sys.exit(main())
