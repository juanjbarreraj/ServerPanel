#!/usr/bin/env python3
"""
«La última vez que se conectó» tiene que sobrevivir a un cambio de mundo.

POR QUÉ ASÍ
-----------
El dato salía del mtime de `world/players/data/<uuid>.dat`. Ese fichero vive
DENTRO de `world/`, y la pestaña Mundo mueve esa carpeta entera cada vez que se
cambia de mundo. O sea que lo que se enseñaba no era «la última vez que entró»
sino «la última vez que jugó EN ESTE MUNDO»:

  · mundo recién estrenado  → no existe el fichero, no hay fecha
  · mundo venido de un zip  → descomprimir escribe los ficheros otra vez, así
                              que TODOS quedan con la hora de la subida; por eso
                              parecía que la fecha «se reseteaba» justo en el
                              momento del cambio

Aquí se monta ese escenario exacto —dos mundos, se cambia, y el nuevo tiene los
ficheros con fecha de hoy— y se comprueba que la fecha que enseña el panel es la
de verdad. Se importa el `server.py` REAL, no una copia de la lógica.

Correr:  python3 scripts/probar-vistos.py
"""
import importlib.util, json, os, sys, tempfile, time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

fallos, pasadas = [], 0
HORA = 3600
DIA = 86400


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


def escribir(p: Path, texto, cuando=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto)
    if cuando:
        os.utime(p, (cuando, cuando))


# Tres jugadores, con UUID de 36 caracteres como los de verdad.
ANA  = "11111111-1111-1111-1111-111111111111"
BETO = "22222222-2222-2222-2222-222222222222"
CARO = "33333333-3333-3333-3333-333333333333"
NOMBRES = {ANA: "Ana", BETO: "Beto", CARO: "Caro"}


def montar(base: Path, ahora):
    """Un mundo NUEVO puesto hoy, y el de antes guardado con las fechas buenas."""
    mc, panel = base / "minecraft", base / "panel"
    (panel / "scripts").mkdir(parents=True, exist_ok=True)

    escribir(mc / "usercache.json",
             json.dumps([{"uuid": u, "name": n} for u, n in NOMBRES.items()]))
    escribir(mc / "whitelist.json",
             json.dumps([{"uuid": u, "name": n} for u, n in NOMBRES.items()]))
    escribir(mc / "server.properties", "level-name=world\n")
    escribir(mc / "logs" / "latest.log", "[10:00:00] [Server thread/INFO]: Done (9.9s)!\n")

    # ── el mundo ACTIVO: estrenado hoy. Todos sus ficheros son de hace 5 min,
    #    que es justo lo que hacía parecer que la fecha se reseteaba.
    hoy = ahora - 300
    for u in (ANA, BETO, CARO):
        escribir(mc / "world/players/stats" / (u + ".json"), '{"stats":{}}', hoy)
        escribir(mc / "world/players/data" / (u + ".dat"), "x", hoy)
    escribir(mc / "world/level.dat", "x", hoy)

    # ── el mundo de ANTES, guardado, con las fechas de verdad
    viejo = mc / "mundos/temporada-1/mundo"
    escribir(viejo / "players/data" / (ANA + ".dat"),  "x", ahora - 30 * DIA)
    escribir(viejo / "players/data" / (BETO + ".dat"), "x", ahora - 12 * DIA)
    escribir(viejo / "players/data" / (CARO + ".dat"), "x", ahora - 40 * DIA)
    escribir(mc / "mundos/activo.json", json.dumps({"slug": "temporada-2"}))

    return mc, panel


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-vistos-"))
    ahora = time.time()
    print("escenario en %s" % base)
    mc, panel = montar(base, ahora)

    os.environ.update(MC_DIR=str(mc), PANEL_DIR=str(panel),
                      PANEL_SECRET="secreto-de-prueba-0123456789")
    spec = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)               # ← el server.py DE VERDAD

    titulo("1 · sin nada guardado, se sigue mirando el fichero")
    # Es la compatibilidad hacia atrás: en un panel que nunca ha guardado nada,
    # el comportamiento tiene que ser exactamente el de siempre.
    p = {x["uuid"]: x for x in srv.player_stats()}
    ok(len(p) == 3, "salen los tres jugadores del mundo activo (%d)" % len(p))
    ok(abs(p[ANA]["last_seen"] - (ahora - 300)) < 5,
       "la fecha es la del .dat del mundo puesto")
    ok(p[ANA]["last_seen_src"] == "activo",
       "y se dice de dónde salió: del fichero del mundo puesto")

    titulo("2 · el rescate: de los mundos guardados y de la Historia")
    # La Historia sabe la hora BUENA de Ana y Beto porque la escribió Minecraft
    # en el log. De Caro no hay nada: ahí manda el fichero del mundo guardado.
    escribir(panel / "data/feed-historico.jsonl", "\n".join(json.dumps(e) for e in [
        {"t": ahora - 31 * DIA, "k": "sesion", "p": "Ana", "u": ANA,
         "fin": ahora - 31 * DIA + 2 * HORA, "seg": 7200},
        {"t": ahora - 9 * DIA, "k": "logro", "p": "Beto", "u": BETO, "titulo": "Algo"},
    ]) + "\n")
    escribir(panel / "data/feed.jsonl", json.dumps(
        {"t": ahora - 3 * DIA, "k": "sesion", "p": "Ana", "u": ANA,
         "fin": ahora - 3 * DIA + HORA, "seg": 3600}) + "\n")

    n = srv.vistos_rescate()
    ok(n >= 3, "el rescate apunta fechas para los tres (%d cambios)" % n)
    g = srv.vistos_leer()
    ok(abs(g[ANA]["t"] - (ahora - 3 * DIA + HORA)) < 5,
       "de Ana se coge el FIN de su última sesión, no el principio")
    ok(g[ANA]["src"] == "log", "y viene del log")
    ok(abs(g[BETO]["t"] - (ahora - 9 * DIA)) < 5,
       "de Beto vale también un logro: es una hora que escribió Minecraft")
    ok(abs(g[CARO]["t"] - (ahora - 40 * DIA)) < 5,
       "de Caro, que no sale en la Historia, se usa el mundo GUARDADO")
    ok(g[CARO]["src"] == "fichero", "y se marca como dato de fichero")

    titulo("3 · el caso de Juan: cambiar de mundo ya no borra la fecha")
    p = {x["uuid"]: x for x in srv.player_stats()}
    ok(abs(p[ANA]["last_seen"] - (ahora - 3 * DIA + HORA)) < 5,
       "Ana sigue teniendo su fecha de hace 3 días…")
    ok(p[ANA]["last_seen"] < ahora - DIA,
       "…y NO la hora en que se cambió el mundo")
    ok(p[CARO]["last_seen"] < ahora - 30 * DIA,
       "Caro, que no juega desde hace más de un mes, lo sigue diciendo")
    ok(p[ANA]["last_seen_src"] == "log", "y el panel sabe que ese dato es del log")

    titulo("4 · dentro de la misma fuente, gana la hora más nueva")
    srv.vistos_apuntar({ANA: (ahora - HORA, "panel")})
    ok(abs(srv.vistos_leer()[ANA]["t"] - (ahora - HORA)) < 5,
       "verla conectada hace una hora manda sobre lo que dijera el log")
    srv.vistos_apuntar({ANA: (ahora - 300 * DIA, "panel")})
    ok(abs(srv.vistos_leer()[ANA]["t"] - (ahora - HORA)) < 5,
       "una hora más vieja de la MISMA fuente no la pisa")
    srv.vistos_apuntar({ANA: (ahora + 40 * DIA, "panel")})
    ok(srv.vistos_leer()[ANA]["t"] < ahora + 3600,
       "y una del FUTURO se tira: un reloj torcido no manda")
    srv.vistos_apuntar({ANA: (ahora - 2 * HORA, "log")})
    ok(abs(srv.vistos_leer()[ANA]["t"] - (ahora - HORA)) < 5,
       "y una fuente PEOR no la arrastra hacia atrás")

    titulo("5 · manda la fuente, no la hora")
    # Esto es lo que arregla el fallo: el mtime de un fichero es siempre el más
    # nuevo cuando el mundo vino de un zip, y es el que menos vale.
    DANI = "44444444-4444-4444-4444-444444444444"
    t = ahora - 5 * DIA
    srv.vistos_apuntar({DANI: (t, "activo")})
    ok(srv.vistos_leer()[DANI]["src"] == "activo", "primero, el mtime de un fichero")
    srv.vistos_apuntar({DANI: (t + DIA, "fichero")})
    ok(abs(srv.vistos_leer()[DANI]["t"] - (t + DIA)) < 2,
       "entre ficheros de dos mundos gana el más nuevo, venga del que venga")
    srv.vistos_apuntar({DANI: (t - 20 * DIA, "log")})
    ok(srv.vistos_leer()[DANI]["src"] == "log", "y el log le gana a los dos")
    srv.vistos_apuntar({DANI: (ahora, "activo")})
    ok(srv.vistos_leer()[DANI]["src"] == "log",
       "un mtime de HOY NO pisa al log — ese es el fallo de Juan")
    ok(abs(srv.vistos_leer()[DANI]["t"] - (t - 20 * DIA)) < 2, "la hora se queda quieta")

    titulo("6 · a quien está dentro se le ve AHORA")
    srv.vistos_marcar_online(["Caro"])
    g = srv.vistos_leer()
    ok(abs(g[CARO]["t"] - time.time()) < 10, "Caro pasa a estar visto ahora mismo")
    ok(g[CARO]["src"] == "panel", "con fuente «panel»")
    srv.vistos_marcar_online(["NoExiste_99"])
    ok("NoExiste_99" not in json.dumps(srv.vistos_leer()),
       "un nombre que no es de nadie no ensucia el fichero")

    titulo("7 · la Historia también lo va poniendo al día sola")
    # feed_scan apunta las sesiones que va cerrando. Sin esto, la fecha solo se
    # actualizaría mientras el panel esté mirando, y se perdería lo de las
    # noches en que el panel estuvo reiniciándose.
    fuente = (REPO / "server.py").read_text()
    cuerpo = fuente.split("def feed_scan")[1].split("\ndef ")[0]
    ok("vistos_apuntar" in cuerpo,
       "feed_scan apunta la última conexión de cada sesión que cierra")
    ok("vistos_rescate" in fuente.split("def _feed_loop")[1][:600],
       "y el rescate se repite en cada arranque, por si la Historia recuperó días")

    titulo("8 · si el fichero se corrompe, no se lleva nada por delante")
    (panel / "data/vistos.json").write_text("{esto no es json")
    srv._vistos_cache["mtime"] = None
    ok(srv.vistos_leer() == {} or isinstance(srv.vistos_leer(), dict),
       "leerlo roto no revienta el panel")
    p = {x["uuid"]: x for x in srv.player_stats()}
    ok(len(p) == 3, "la lista de jugadores sigue saliendo")
    ok(srv.vistos_rescate() >= 3, "y el rescate lo reconstruye desde cero")
    ok(abs(srv.vistos_leer()[ANA]["t"] - (ahora - 3 * DIA + HORA)) < 5,
       "con la fecha buena de Ana otra vez")

    titulo("9 · un copiado en bloque no cuenta como «se conectó»")
    # El caso REAL del servidor de Juan: 57 de los 58 .dat del mundo puesto
    # tenían exactamente la misma fecha — el segundo en que se descomprimió un
    # zip de 618 MB. Con ese dato dentro, la última conexión de todos era la
    # hora de la subida.
    copia = base / "minecraft/mundos/subido-de-un-zip/mundo"
    subida = ahora - 2 * DIA
    for i in range(20):
        u = "%08d-1111-2222-3333-444444444444" % i
        escribir(copia / "players/data" / (u + ".dat"), "x", subida)
        escribir(copia / "players/stats" / (u + ".json"), "{}", subida)
    # dos que sí jugaron DESPUÉS de la subida: esos son de verdad
    solo = "99999999-1111-2222-3333-444444444444"
    escribir(copia / "players/data" / (solo + ".dat"), "x", ahora - 6 * HORA)
    escribir(copia / "players/stats" / (solo + ".json"), "{}", ahora - 6 * HORA)

    fechas = srv._fechas_de_un_mundo(copia)
    ok(len(fechas) == 1, "de 21 jugadores solo sobrevive 1 fecha (%d)" % len(fechas))
    ok(solo in fechas, "la del único que jugó después de la subida")
    ok(abs(fechas.get(solo, 0) - (ahora - 6 * HORA)) < 5, "con su hora buena")
    ok(not any(abs(t - subida) < 2 for t in fechas.values()),
       "y ni una sola con la hora del zip")

    srv.vistos_rescate()
    g = srv.vistos_leer()
    ok(not any(abs(float(v["t"]) - subida) < 2 for v in g.values()),
       "así que nadie acaba «visto» a la hora en que se subió el mundo")
    # y el .dat y el .json de una misma persona no cuentan como dos jugadores
    uno = base / "minecraft/mundos/solo-uno/mundo"
    escribir(uno / "players/data" / (solo + ".dat"), "x", ahora - 9 * DIA)
    escribir(uno / "players/stats" / (solo + ".json"), "{}", ahora - 9 * DIA)
    ok(len(srv._fechas_de_un_mundo(uno)) == 1,
       "un jugador solo, con sus dos ficheros a la misma hora, no es un copiado")

    titulo("10 · el caso REAL del servidor, con sus fechas de verdad")
    # Reconstruido de lo que salió en el servidor de Juan el 18/09/2026:
    #   world (puesto)                          57 .dat a 2026-09-16 13:40:05
    #                                           (el segundo del zip de 618 MB)
    #                                           + JEY a 2026-09-18 01:56
    #   mundos/antes-20260912-2020              44 a 2026-08-08 04:09:13 (otro zip)
    #                                           + 14 fechas individuales de verdad
    #   mundos/antes-20260916-1340              57 a 2026-09-12 20:19:34 (otro zip)
    #                                           + 966ac1cc y JEY, reales
    base2 = Path(tempfile.mkdtemp(prefix="probar-vistos-real-"))
    mc2 = base2 / "minecraft"
    JEY = "c5829560-f1a1-4013-b6f6-b57dce771189"
    N66 = "966ac1cc-aee5-47bb-9c02-2f9faf821074"
    OTRO = "4664aaff-466e-4619-a18c-3cbb085a466f"
    ZIP_HOY, ZIP_A, ZIP_B = ahora - 2 * DIA, ahora - 41 * DIA, ahora - 6 * DIA
    JEY_HOY, N66_REAL, OTRO_REAL = ahora - 14 * HORA, ahora - 3 * DIA, ahora - 13 * DIA

    def poblar(w, masa, sueltos):
        for i in range(masa):
            u = "%08d-0000-0000-0000-000000000000" % i
            escribir(w / "players/data" / (u + ".dat"), "x", sueltos[0])
        for u, t in sueltos[1].items():
            escribir(w / "players/data" / (u + ".dat"), "x", t)

    poblar(mc2 / "world", 30, (ZIP_HOY, {JEY: JEY_HOY}))
    poblar(mc2 / "mundos/antes-a/mundo", 30, (ZIP_A, {OTRO: OTRO_REAL, JEY: ahora - 6 * DIA}))
    poblar(mc2 / "mundos/antes-b/mundo", 30, (ZIP_B, {N66: N66_REAL, JEY: ahora - 5 * DIA}))

    srv.MC_DIR = mc2
    try:
        marcas = srv._vistos_de_los_ficheros()
    finally:
        srv.MC_DIR = mc
    ok(abs(marcas[JEY][0] - JEY_HOY) < 5,
       "JEY, que jugó HOY en el mundo puesto, se queda con la de hoy")
    ok(abs(marcas[N66][0] - N66_REAL) < 5, "966ac1cc con la suya de hace 3 días")
    ok(abs(marcas[OTRO][0] - OTRO_REAL) < 5,
       "y el que solo sale en el mundo más viejo, con la suya de hace 13")
    ok(not any(abs(t - z) < 2 for t, _s in marcas.values()
               for z in (ZIP_HOY, ZIP_A, ZIP_B)),
       "ninguna de las tres horas de zip sobrevive")
    ok(len(marcas) == 3, "solo quedan las 3 fechas que significan algo (%d)" % len(marcas))

    titulo("11 · la Historia parada se ve en rojo, no en verde")
    # Tres `return False` mudos hacían que la Historia se congelara con la
    # tarjeta de Sistema en verde. Sin mensajes.json no se puede leer nada.
    ok(srv.feed_scan() is False, "sin mensajes.json, feed_scan dice que NO leyó")
    ok("mensajes.json" in srv._feed_por_que["nota"],
       "y deja dicho por qué: %r" % srv._feed_por_que["nota"][:60])
    srv._salud("feed", ok=False, nota=srv._feed_por_que["nota"])
    fila = next(x for x in srv._automatismos() if x["id"] == "feed")
    ok(fila["ok"] is False, "la fila de Historia de Sistema sale en rojo")
    ok("mensajes.json" in (fila["nota"] or ""), "con el motivo a la vista")
    ok(any(x["id"] == "vistos" for x in srv._automatismos()),
       "y hay una fila propia para la última conexión")


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
