#!/usr/bin/env python3
"""
Que la Historia siga leyendo el log DESPUÉS de reiniciar el servidor.

POR QUÉ ASÍ
-----------
Cambiar de mundo para el servidor es pararlo y volver a arrancarlo, y eso hace
que log4j comprima `latest.log` en un `.log.gz`, borre el original y cree uno
nuevo. Si el panel no se entera de que es OTRO fichero, se queda sentado en el
byte donde se quedó y la Historia se congela — sin un solo error en ninguna
parte, que es lo peor que le puede pasar a un automatismo.

Hay tres formas de que el fichero sea otro y las tres tienen que valer:

  1. fichero nuevo, inode nuevo               ← lo normal
  2. el mismo fichero truncado a cero         ← se detecta porque encoge
  3. fichero nuevo que REUSA el inode que acaba de quedar libre
       ← ni el inode ni el tamaño lo delatan. Solo lo delata que el principio
         del fichero es distinto, que es lo que se comprueba aquí.

El 3 no es rebuscado: el inode del fichero que log4j acaba de borrar es
exactamente el que el sistema de ficheros tiene más a mano para el siguiente.

Se importa el `server.py` REAL y se le da un log escrito a mano con frases de
verdad.

Correr:  python3 scripts/probar-feed-reinicio.py
"""
import gzip, importlib.util, json, os, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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


ANA = "11111111-1111-1111-1111-111111111111"
BETO = "22222222-2222-2222-2222-222222222222"

MENSAJES = {
    "version": "26.3",
    "muertes": [{"clave": "death.attack.mob", "n": 2,
                 "rx": r"^([A-Za-z0-9_]{1,16}) was slain by (.+)$",
                 "en": "%1$s was slain by %2$s", "es": "%1$s fue asesinado por %2$s"}],
    "sesion": {"entro": {"rx": r"^([A-Za-z0-9_]{1,16}) joined the game$",
                         "en": "%s joined the game", "es": "%s se unió a la partida"},
               "salio": {"rx": r"^([A-Za-z0-9_]{1,16}) left the game$",
                         "en": "%s left the game", "es": "%s salió de la partida"}},
    "logros": {"tarea": {"rx": r"^([A-Za-z0-9_]{1,16}) has made the advancement (.+)$",
                         "en": "%s has made the advancement %s",
                         "es": "%s ha conseguido el logro %s"}},
    "efectos": [], "bichos": {},
}


def linea(hora, msg):
    return "[%s] [Server thread/INFO]: %s\n" % (hora, msg)


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-feed-rein-"))
    mc, panel = base / "minecraft", base / "panel"
    (panel / "data").mkdir(parents=True, exist_ok=True)
    (mc / "logs").mkdir(parents=True, exist_ok=True)
    (mc / "world/players/stats").mkdir(parents=True, exist_ok=True)
    (mc / "world/players/data").mkdir(parents=True, exist_ok=True)
    print("escenario en %s" % base)

    (mc / "usercache.json").write_text(json.dumps(
        [{"uuid": ANA, "name": "Ana"}, {"uuid": BETO, "name": "Beto"}]))
    (mc / "whitelist.json").write_text((mc / "usercache.json").read_text())
    (panel / "data/mensajes.json").write_text(json.dumps(MENSAJES, ensure_ascii=False))

    LOG = mc / "logs/latest.log"
    LOG.write_text(
        linea("10:00:01", "Starting minecraft server version 26.3") +
        linea("10:00:09", "Done (8.1s)! For help, type \"help\"") +
        linea("10:02:00", "Ana joined the game") +
        linea("10:05:30", "Ana was slain by Zombie") +
        linea("10:06:00", "Beto joined the game"))

    os.environ.update(MC_DIR=str(mc), PANEL_DIR=str(panel),
                      PANEL_SECRET="secreto-de-prueba-0123456789")
    spec = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)                  # ← el server.py DE VERDAD

    def eventos():
        return srv.feed_eventos(200)

    titulo("1 · lectura normal")
    ok(srv.feed_scan() is True, "feed_scan dice que ha leído")
    ev = eventos()
    ok(any(e["k"] == "muerte" and e["p"] == "Ana" for e in ev),
       "la muerte de Ana está en la Historia")
    est = srv._feed_state()
    ok(est["latest"]["offset"] == LOG.stat().st_size,
       "el cursor se queda al final del fichero")
    ok(est["latest"].get("cabeza"), "y se apunta cómo empieza el fichero")
    ok(set(est["abiertas"]) == {"Ana", "Beto"},
       "las dos sesiones siguen abiertas: %s" % list(est["abiertas"]))
    cuantos = len(ev)

    titulo("2 · no se relee lo mismo dos veces")
    srv.feed_scan()
    ok(len(eventos()) == cuantos, "la Historia no se duplica")

    titulo("3 · cambio de mundo: el servidor se para y arranca")
    with open(LOG, "a") as f:
        f.write(linea("10:30:00", "Stopping the server"))
    srv.feed_scan()
    # log4j: comprime el viejo, lo borra, crea uno nuevo
    gz = mc / "logs/2026-09-17-1.log.gz"
    with gzip.open(gz, "wt") as g:
        g.write(LOG.read_text())
    LOG.unlink()
    nuevo = (linea("10:34:02", "Starting minecraft server version 26.3") +
             linea("10:34:11", "Done (9.0s)! For help, type \"help\"") +
             linea("10:36:00", "Ana joined the game") +
             linea("10:41:00", "Ana has made the advancement [Zoología aplicada]"))
    LOG.write_text(nuevo)
    os.utime(LOG, (time.time(), time.time()))

    ok(srv.feed_scan() is True, "vuelve a leer")
    ev = eventos()
    logro = next((e for e in ev if e["k"] == "logro"), None)
    ok(logro is not None, "el logro del arranque nuevo SÍ llega a la Historia")
    # La hora exacta es la que diga el log (10:41); lo que no puede pasar es que
    # la fecha se vaya días atrás, porque entonces el evento se hunde en la
    # lista y parece que la Historia sigue parada aunque esté leyendo.
    ok(logro and -60 < (time.time() - logro["t"]) < 26 * 3600,
       "con una fecha creíble, ni del futuro ni de hace días: %s"
       % (logro and time.strftime("%F %T", time.localtime(logro["t"]))))
    cortadas = [e for e in ev if e["k"] == "sesion" and e.get("cortada")]
    ok(len(cortadas) == 2,
       "las sesiones que pilló el reinicio se cierran, no desaparecen (%d)"
       % len(cortadas))
    est = srv._feed_state()
    ok(est["latest"]["offset"] == LOG.stat().st_size, "y el cursor va al día")

    titulo("4 · el caso feo: el fichero nuevo REUSA el inode del viejo")
    # Se simula a mano lo que hace el sistema de ficheros: mismo inode, tamaño
    # MAYOR que el cursor de antes (si fuera menor lo cazaría el tamaño) y
    # contenido completamente distinto.
    inode_antes = LOG.stat().st_ino
    relleno = "".join(linea("10:4%d:00" % (i % 10), "Server thread tick %d" % i)
                      for i in range(40))
    with open(LOG, "r+") as f:                    # r+ = mismo inode, se sobreescribe
        f.seek(0)
        f.write(linea("11:00:03", "Starting minecraft server version 26.3") +
                linea("11:00:12", "Done (9.4s)! For help, type \"help\"") +
                relleno +
                linea("11:20:00", "Beto joined the game") +
                linea("11:25:00", "Beto was slain by Zombie"))
        f.truncate()
    ok(LOG.stat().st_ino == inode_antes, "el inode es el mismo que antes")
    ok(LOG.stat().st_size > est["latest"]["offset"],
       "y el fichero es MÁS GRANDE que el cursor: ni el inode ni el tamaño avisan")
    srv.feed_scan()
    ev = eventos()
    ok(any(e["k"] == "muerte" and e["p"] == "Beto" for e in ev),
       "aun así la muerte de Beto llega: se detecta por cómo empieza el fichero")
    ok(srv._feed_state()["latest"]["offset"] == LOG.stat().st_size,
       "y el cursor se recoloca")

    titulo("5 · truncado en el sitio (mismo inode, más pequeño)")
    LOG.write_text(linea("12:00:01", "Starting minecraft server version 26.3") +
                   linea("12:03:00", "Ana left the game"))
    srv.feed_scan()
    ok(srv._feed_state()["latest"]["offset"] == LOG.stat().st_size,
       "el cursor vuelve al tamaño del fichero pequeño")

    titulo("6 · y el estado que se guarda es el que se relee")
    ok(json.loads((panel / "data/feed_state.json").read_text())["latest"]["cabeza"]
       == srv._feed_state()["latest"]["cabeza"], "la cabeza queda guardada en disco")

    titulo("7 · un panel que viene de la versión anterior no se atraganta")
    # feed_state.json de antes de este arreglo: sin `cabeza`. No debe provocar
    # una relectura entera ni perder el sitio.
    viejo = srv._feed_state()
    viejo["latest"].pop("cabeza", None)
    srv._feed_state_save(viejo)
    antes = len(eventos())
    srv.feed_scan()
    ok(len(eventos()) == antes, "no se relee el log ni se duplica nada")
    ok(srv._feed_state()["latest"].get("cabeza"), "y a partir de ahora ya la lleva")

    titulo("8 · si las frases son de otra versión, se dice")
    # El fallo silencioso de la misma familia: el log se lee, las líneas se
    # entienden, y aun así no sale ni un evento porque las frases del juego
    # cambiaron y `mensajes.json` sigue siendo el de hace dos versiones.
    (mc / "versions/26.3").mkdir(parents=True, exist_ok=True)
    (mc / "versions/26.3/server-26.3.jar").write_text("x")
    srv._ver_cache.update(t=0, val=None)
    d = srv._frases_desfase()
    ok(d["desfase"] is False, "con el jar y las frases en la misma versión, callado")
    srv._salud("feed", ok=True)
    ok(next(x for x in srv._automatismos() if x["id"] == "feed")["ok"],
       "y la fila de Sistema en verde")

    (mc / "versions/26.9").mkdir(parents=True, exist_ok=True)
    (mc / "versions/26.9/server-26.9.jar").write_text("x")
    srv._ver_cache.update(t=0, val=None)
    d = srv._frases_desfase()
    ok(d["desfase"] is True, "si el servidor pasa a la 26.9, se detecta")
    ok(d["frases"] == "26.3" and d["jar"] == "26.9",
       "y se nombran las dos versiones: %s vs %s" % (d["frases"], d["jar"]))
    fila = next(x for x in srv._automatismos() if x["id"] == "feed")
    ok(not fila["ok"], "la Historia pasa a ROJO aunque esté leyendo el log")
    ok("build-mensajes" in (fila["nota"] or ""),
       "con la orden que lo arregla: %r" % (fila["nota"] or "")[-40:])

    titulo("9 · si el contador de días se desmadró, se vuelve a anclar solo")
    # La fecha de cada línea se cuenta mirando si la hora retrocede. Una línea
    # rara puede sumar un día de más, ese día se guarda en `ultimo_ts` y se
    # arrastra a la siguiente lectura. Los eventos acaban en el FUTURO y, como
    # la Historia se ordena por fecha, se quedan clavados arriba y todo lo nuevo
    # queda enterrado: desde fuera, indistinguible de «no se actualiza».
    malo = srv._feed_state()
    malo["latest"]["ultimo_ts"] = time.time() + 10 * 86400
    srv._feed_state_save(malo)
    with open(LOG, "a") as f:
        f.write(linea("12:30:00", "Beto joined the game") +
                linea("12:31:00", "Beto was slain by Zombie"))
    srv.feed_scan()
    nuevos = [e for e in eventos() if e["k"] == "muerte" and e["p"] == "Beto"]
    ok(nuevos, "la muerte llega igual")
    ok(nuevos and nuevos[0]["t"] < time.time() + 3600,
       "y NO queda fechada en el futuro: %s"
       % (nuevos and time.strftime("%F %T", time.localtime(nuevos[0]["t"]))))
    ok(srv._feed_state()["latest"]["ultimo_ts"] < time.time() + 3600,
       "el cursor de fecha se deja arreglado, no solo este evento")
    top = eventos()[0]
    ok(top["t"] < time.time() + 3600,
       "y lo primero que se ve en la Historia ya no es algo de dentro de 10 días")


    titulo("10 · la 26.3 antepone «System chat: » a todo lo que difunde")
    # Líneas COPIADAS del log del servidor de Juan el 18/09/2026. Esto es lo que
    # dejó la Historia muda seis días: las plantillas salen del jar y ahí ese
    # prefijo no aparece, porque lo pone el código al escribir en consola.
    M = srv.mensajes()
    def leer(msg):
        r = srv._interpretar(msg, M)
        return None if r is None else (r[0], r[1])

    ok(leer("System chat: JEYtheFlash joined the game") == ("entro", "JEYtheFlash"),
       "una entrada con el prefijo de la 26.3 se entiende")
    ok(leer("System chat: JEYtheFlash left the game") == ("salio", "JEYtheFlash"),
       "y una salida")
    ok(leer("System chat: Ana was slain by Zombie") == ("muerte", "Ana"),
       "y una muerte")
    ok((leer("System chat: Ana has made the advancement [Zoología aplicada]") or (None,))[0]
       == "logro", "y un logro")
    ok(leer("Ana joined the game") == ("entro", "Ana"),
       "y las líneas SIN prefijo, de los logs de antes de la 26.3, siguen valiendo")

    # y de punta a punta, leyendo el log como lo lee el panel
    with open(LOG, "a") as f:
        f.write(linea("13:00:00", "System chat: Ana joined the game") +
                linea("13:30:00", "System chat: Ana has made the advancement [Zoología]") +
                linea("13:45:00", "System chat: Ana left the game"))
    srv.feed_scan()
    ev = eventos()
    ok(any(e["k"] == "logro" and e["p"] == "Ana" for e in ev),
       "el logro llega a la Historia leyendo el log de verdad")
    ses = [e for e in ev if e["k"] == "sesion" and e["p"] == "Ana" and e.get("seg") == 2700]
    ok(ses, "y la sesión sale entera, con sus 45 minutos")

    titulo("11 · pero el prefijo no se puede falsificar")
    # Si algún día el chat se escribiera «Ana: hola», sin cuidado cualquiera
    # podría escribir «Ana: Beto left the game». Por eso el prefijo tiene que
    # llevar un espacio: un nombre de Minecraft no puede tenerlo.
    ok(leer("Ana: Beto left the game") is None,
       "un nombre de jugador NO sirve de prefijo")
    ok(leer("JEYtheFlash: Ana was slain by Zombie") is None, "ni aunque sea una muerte")
    ok(leer("System chat: <Ana> Beto left the game") is None,
       "ni el chat de alguien dentro de un mensaje de difusión")
    ok(leer("System chat: [Ana] Beto left the game") is None,
       "ni un /say")
    ok(leer("System chat: [JEYtheFlash: Set own game mode to Survival Mode]") is None,
       "ni la respuesta a una orden")
    ok(leer("Player JEYtheFlash standing on air - force-sending blocks below") is None,
       "y una línea corriente del servidor sigue sin ser nada")

    titulo("12 · al cambiar el lector se vuelven a leer los logs guardados")
    # Sin esto, los seis días que no se entendían seguirían faltando para
    # siempre: los .log.gz están marcados como ya procesados.
    est = srv._feed_state()
    est["archivos"] = ["2026-09-17-1.log.gz"]
    est["lector"] = 1
    srv._feed_state_save(est)
    srv.FEEDHIST_F.write_text(json.dumps({"t": 1, "k": "sesion", "p": "viejo"}) + "\n")
    srv.feed_relleno()
    est = srv._feed_state()
    ok(est.get("lector") == srv.FEED_LECTOR,
       "el estado se queda con la versión nueva del lector (%s)" % est.get("lector"))
    ok("2026-09-17-1.log.gz" not in (est.get("archivos") or [])
       or gz.name in (est.get("archivos") or []),
       "la lista de «ya leídos» se vació para volver a leerlos")
    ok(gz.name in (est.get("archivos") or []),
       "y el log guardado de verdad se ha procesado")
    ok(srv.FEED_F.exists(), "el fichero de eventos EN VIVO no se toca")

    titulo("13 · y el mismo suceso no sale dos veces")
    # Un log que se archiva mientras el panel lo está leyendo acaba en los dos
    # ficheros. Antes no se notaba porque el relleno solo corría una vez.
    ev = eventos()
    claves = [(e.get("t"), e.get("k"), e.get("p"), e.get("fin"), e.get("titulo"),
               e.get("clave"), e.get("victima")) for e in ev]
    ok(len(claves) == len(set(claves)),
       "no hay ni un evento repetido en la Historia (%d de %d)"
       % (len(set(claves)), len(claves)))
    # dos logros del mismo jugador en el mismo segundo SÍ son dos
    dos = srv._sin_repetidos([
        {"t": 100, "k": "logro", "p": "Ana", "titulo": "Uno"},
        {"t": 100, "k": "logro", "p": "Ana", "titulo": "Dos"},
        {"t": 100, "k": "logro", "p": "Ana", "titulo": "Uno"}])
    ok(len(dos) == 2, "pero dos logros distintos en el mismo segundo se quedan (%d)"
       % len(dos))


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
