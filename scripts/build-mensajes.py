#!/usr/bin/env python3
"""
Construye la tabla de FRASES DEL JUEGO que el panel necesita para leer el log
del servidor y para el desplegable de efectos.

De dónde sale cada cosa (todo oficial, nada inventado de memoria):
  - plantillas de muerte ..... claves `death.*` de assets/minecraft/lang/en_us.json
                               del jar del SERVIDOR (inglés = lo que sale en el log)
  - la traducción ............ assets oficiales de Mojang (es_mx, y si no es_es)
  - entradas y salidas ....... `multiplayer.player.joined` / `.left`
  - logros ................... `chat.type.advancement.task|challenge|goal`
  - efectos .................. todas las claves `effect.minecraft.<id>`

Por qué en inglés: la consola de un servidor vanilla NO tiene idioma configurable,
así que los mensajes se escriben siempre con en_us. Se detecta con la plantilla
inglesa y se MUESTRA con la española.

Salida: panel/data/mensajes.json
Lo lee server.py para el feed (/api/feed) y para /api/effects.

Uso:  python3 ~/panel/scripts/build-mensajes.py
      python3 ~/panel/scripts/build-mensajes.py --sin-red        (solo inglés)
      python3 ~/panel/scripts/build-mensajes.py --rehacer-feed   (borra el feed
                                        para que se reconstruya con las frases
                                        nuevas; hace falta si cambian las plantillas)
"""
import glob, json, re, sys, urllib.request, zipfile
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent
MC    = Path.home() / "minecraft"
OUT   = PANEL / "data" / "mensajes.json"
SIN_RED = "--sin-red" in sys.argv

MANIFIESTO = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RECURSOS   = "https://resources.download.minecraft.net/%s/%s"
IDIOMAS_ES = ["minecraft/lang/es_mx.json", "minecraft/lang/es_es.json"]

# Los efectos que HACEN DAÑO o estorban. El jar no lo dice en ningún fichero de
# datos (va en el código, MobEffectCategory), así que esta lista es a mano. Lo
# que NO esté aquí ni en BUENOS sale marcado como "sin clasificar" y el panel
# avisa de que no sabe — mejor eso que decir que algo es inofensivo sin saberlo.
DANINOS = {
    "slowness", "mining_fatigue", "instant_damage", "nausea", "blindness",
    "hunger", "weakness", "poison", "wither", "levitation", "unluck",
    "darkness", "bad_omen", "trial_omen", "raid_omen", "infested", "oozing",
    "weaving", "wind_charged",
}
BUENOS = {
    "speed", "haste", "strength", "instant_health", "jump_boost", "regeneration",
    "resistance", "fire_resistance", "water_breathing", "invisibility",
    "night_vision", "health_boost", "absorption", "saturation", "glowing",
    "luck", "slow_falling", "conduit_power", "dolphins_grace",
    "hero_of_the_village",
    # 26.2 / 1.21.11: congela la barra de oxígeno para que no te ahogues.
    # Mojang lo clasifica como positivo. Salió como "sin clasificar" la primera
    # vez que se corrió esto en el servidor real y se comprobó en la wiki.
    "breath_of_the_nautilus",
}


def jar_del_servidor():
    """Desde 1.18 el server.jar es solo un lanzador; el bueno está en versions/."""
    cand = sorted(glob.glob(str(MC / "versions/**/server-*.jar"), recursive=True))
    if not cand:
        cand = [str(MC / "server.jar")]
    for p in cand:
        try:
            z = zipfile.ZipFile(p)
            if "assets/minecraft/lang/en_us.json" in z.namelist():
                return p, z
        except Exception:
            pass
    raise SystemExit("No encontré un jar con los textos dentro. Busqué en %s" % (MC / "versions"))


def version_de(jar_path):
    m = re.search(r"server-([\d.\w-]+)\.jar$", jar_path)
    return m.group(1) if m else None


def lang_es(version):
    if SIN_RED:
        return {}
    try:
        man = json.load(urllib.request.urlopen(MANIFIESTO, timeout=20))
        v = next((x for x in man["versions"] if x["id"] == version), None)
        if v is None:
            print("  (la versión %s no está en el manifiesto de Mojang)" % version)
            return {}
        vj = json.load(urllib.request.urlopen(v["url"], timeout=20))
        idx = json.load(urllib.request.urlopen(vj["assetIndex"]["url"], timeout=30))
        for nombre in IDIOMAS_ES:
            obj = idx["objects"].get(nombre)
            if not obj:
                continue
            h = obj["hash"]
            data = urllib.request.urlopen(RECURSOS % (h[:2], h), timeout=30).read()
            d = json.loads(data)
            print("  textos en español: %s (%d entradas)" % (nombre.split("/")[-1], len(d)))
            return d
        print("  (no encontré es_mx ni es_es en los assets)")
    except Exception as e:
        print("  (sin español: %s)" % e)
    return {}


# --------------------------------------------------------------- plantillas
NOMBRE = r"[A-Za-z0-9_]{1,16}"

# ⚠ Minecraft usa DOS formas de hueco en el mismo fichero de textos:
#     death.attack.mob            -> "%1$s was slain by %2$s"   (numerado)
#     multiplayer.player.joined   -> "%s joined the game"       (simple)
#     chat.type.advancement.task  -> "%s has made the advancement %s"
# Los numerados existen porque algunos idiomas cambian el orden. La primera
# versión de este script solo entendía los numerados, así que las muertes salían
# en el feed y las ENTRADAS y los LOGROS no: sus plantillas se quedaban sin regex
# y se descartaban en silencio. De ahí la comprobación obligatoria de abajo.
HUECO = re.compile(r"%(?:(\d)\$)?s")


def a_regex(plantilla):
    """Plantilla de Minecraft -> regex con grupos numerados.

    El grupo 1 es SIEMPRE el jugador y se limita al juego de caracteres de un
    nombre de Minecraft: así una frase de chat que por casualidad se parezca a
    una muerte no cuela. Los demás grupos son perezosos menos el último, para
    que 'A was slain by B using C' reparta bien cuando B lleva espacios.
    """
    trozos, indices, pos, seq = [], [], 0, 0
    for m in HUECO.finditer(plantilla):
        trozos.append(re.escape(plantilla[pos:m.start()]))
        seq += 1
        indices.append(int(m.group(1)) if m.group(1) else seq)
        pos = m.end()
    if not indices:
        return None, 0
    cola = re.escape(plantilla[pos:])
    ultimo = len(indices) - 1
    partes = []
    for i, idx in enumerate(indices):
        partes.append(trozos[i])
        if idx == 1:
            partes.append("(" + NOMBRE + ")")
        else:
            partes.append("(.+)" if i == ultimo else "(.+?)")
    partes.append(cola)
    return "^" + "".join(partes) + "$", len(indices)


def main():
    jar_path, z = jar_del_servidor()
    version = version_de(jar_path)
    print("jar: %s   (versión %s)" % (Path(jar_path).name, version))

    en = json.loads(z.read("assets/minecraft/lang/en_us.json"))
    print("  en_us: %d entradas" % len(en))
    es = lang_es(version)

    salida = {"version": version}

    # ---------------------------------------------------------- 1) muertes
    muertes, sin_es = [], []
    for clave, txt in en.items():
        if not clave.startswith("death.") or "%1$s" not in txt:
            continue
        rx, n = a_regex(txt)
        if not rx:
            continue
        es_txt = es.get(clave)
        if not es_txt:
            sin_es.append(clave)
        muertes.append({"clave": clave, "rx": rx, "n": n,
                        "en": txt, "es": es_txt or txt})
    # Las más específicas primero: 'X was shot by Y using Z' tiene que ganarle a
    # 'X was shot by Y', o la segunda se traga la frase y se pierde el arma.
    muertes.sort(key=lambda m: (-m["n"], -len(m["en"])))
    salida["muertes"] = muertes
    print("  muertes: %d plantillas" % len(muertes))

    # -------------------------------------------------- 2) entradas/salidas
    ses = {}
    for k, campo in (("multiplayer.player.joined", "entro"),
                     ("multiplayer.player.left", "salio")):
        txt = en.get(k)
        if not txt:
            print("  ⚠ falta la clave %s en el jar" % k)
            continue
        rx, _ = a_regex(txt)
        ses[campo] = {"rx": rx, "en": txt, "es": es.get(k, txt)}
    salida["sesion"] = ses

    # ------------------------------------------------------------ 3) logros
    logros = {}
    for k, campo in (("chat.type.advancement.task", "tarea"),
                     ("chat.type.advancement.challenge", "reto"),
                     ("chat.type.advancement.goal", "meta")):
        txt = en.get(k)
        if not txt:
            print("  ⚠ falta la clave %s en el jar" % k)
            continue
        rx, _ = a_regex(txt)
        logros[campo] = {"rx": rx, "en": txt, "es": es.get(k, txt)}
    salida["logros"] = logros

    # ----------------------------------------------------------- 4) efectos
    efectos, sin_clasificar = [], []
    for clave, txt in en.items():
        m = re.fullmatch(r"effect\.minecraft\.([a-z0-9_]+)", clave)
        if not m:
            continue
        eid = m.group(1)
        if eid in DANINOS:
            tipo = "malo"
        elif eid in BUENOS:
            tipo = "bueno"
        else:
            tipo = "desconocido"
            sin_clasificar.append(eid)
        efectos.append({"id": eid, "en": txt, "es": es.get(clave, txt), "tipo": tipo})
    efectos.sort(key=lambda e: e["es"].lower())
    salida["efectos"] = efectos
    print("  efectos: %d" % len(efectos))

    # ------------------------------------------- 5) nada silencioso
    # Si una plantilla no produce regex, el evento entero desaparece del feed sin
    # que nadie se entere. Pasó de verdad: las entradas y los logros usan "%s" y
    # el script solo entendía "%1$s", así que solo salían las muertes. Ahora eso
    # es un error duro.
    faltan = []
    if not muertes:
        faltan.append("ninguna plantilla de muerte")
    for campo in ("entro", "salio"):
        if not (ses.get(campo) or {}).get("rx"):
            faltan.append("sesión/" + campo)
    if not any((v or {}).get("rx") for v in logros.values()):
        faltan.append("logros")
    if faltan:
        print("\n✗ NO puedo leer: %s" % ", ".join(faltan))
        print("  El feed saldría incompleto. Revisa las claves en el jar antes de seguir.")
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(salida, ensure_ascii=False, indent=1))
    print("✔ %s  (%.1f KB)" % (OUT, OUT.stat().st_size / 1024))

    if sin_es:
        print("  ⚠ %d frases de muerte sin español (se mostrarán en inglés)" % len(sin_es))
    if sin_clasificar:
        print("  ⚠ efectos que no sé si hacen daño (el panel avisará): %s"
              % ", ".join(sorted(sin_clasificar)))

    comprobar(salida)

    if "--rehacer-feed" in sys.argv:
        borrados = []
        for n in ("feed.jsonl", "feed-historico.jsonl", "feed_state.json"):
            f = PANEL / "data" / n
            if f.exists():
                f.unlink(); borrados.append(n)
        print("\n🗑  feed borrado (%s) — el panel lo reconstruye solo desde los logs"
              % (", ".join(borrados) if borrados else "no había nada"))
        print("   ojo: se pierden las coordenadas de las muertes que ya se habían")
        print("   capturado en vivo; el log no las trae y no se pueden recuperar.")
    return 0


# ------------------------------------------------------------ verificación
# Frases reales de un log de Minecraft. Si el jar cambiara las plantillas y
# dejaran de casar, esto grita aquí en vez de dejar el feed medio vacío sin
# que nadie se entere.
PRUEBAS = [
    ("JEYtheFlash was slain by Zombie",                 "muerte", "JEYtheFlash"),
    ("Jakobino155 was impaled by Piglin",               "muerte", "Jakobino155"),
    ("sofidiaz fell from a high place",                 "muerte", "sofidiaz"),
    ("kkrenalga0228 was shot by Skeleton",              "muerte", "kkrenalga0228"),
    ("tommy__odd drowned",                              "muerte", "tommy__odd"),
    ("josemori67 tried to swim in lava",                "muerte", "josemori67"),
    # la variante con arma tiene que ganarle a la corta, o se pierde el arma
    ("mrt5555 was slain by Zombie using Netherite Sword", "muerte", "mrt5555"),
    ("Chiki99933 was blown up by Creeper",              "muerte", "Chiki99933"),
    ("JEYtheFlash joined the game",                     "entro",  "JEYtheFlash"),
    ("sofidiaz left the game",                          "salio",  "sofidiaz"),
    ("JEYtheFlash has made the advancement [Ir mas profundo]", "logro", "JEYtheFlash"),
    ("sofidiaz has completed the challenge [El fin?]",  "logro",  "sofidiaz"),
]


def comprobar(salida):
    print("\ncomprobación con frases reales de log:")
    fallos = 0
    compiladas = [(m, re.compile(m["rx"])) for m in salida["muertes"]]
    for linea, espero, quien in PRUEBAS:
        hallado = None
        if espero == "muerte":
            for m, rx in compiladas:
                g = rx.match(linea)
                if g:
                    extra = " + " + g.group(g.re.groups) if g.re.groups > 2 else ""
                    hallado = ("muerte", g.group(1), m["clave"] + extra)
                    break
        elif espero == "logro":
            for campo, d in salida["logros"].items():
                g = re.match(d["rx"], linea)
                if g:
                    hallado = ("logro", g.group(1), campo + " " + g.group(2))
                    break
        else:
            d = salida["sesion"].get(espero)
            g = re.match(d["rx"], linea) if d else None
            if g:
                hallado = (espero, g.group(1), espero)
        if hallado and hallado[1] == quien:
            print("  ✔ %-46s → %s" % (linea, hallado[2]))
        else:
            print("  ✗ %-46s → %s" % (linea, hallado or "NO CASÓ"))
            fallos += 1
    # y algo que NO debe casar nunca
    chat = "<JEYtheFlash> me mataron en el nether jaja"
    if any(rx.match(chat) for _, rx in compiladas):
        print("  ✗ una línea de chat casó como muerte: %s" % chat)
        fallos += 1
    else:
        print("  ✔ el chat no se confunde con una muerte")
    print("  %s" % ("todo bien" if not fallos else "⚠ %d fallo(s)" % fallos))


if __name__ == "__main__":
    sys.exit(main())
