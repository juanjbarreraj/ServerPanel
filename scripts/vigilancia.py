#!/usr/bin/env python3
"""
Avisa en la Historia del panel cuando un JUGADOR mata un animal de alguien.

QUÉ PROBLEMA RESUELVE, Y CUÁL NO
--------------------------------
Minecraft **no registra la muerte de un mob en ningún sitio al que el servidor
pueda llegar**. Está comprobado por los dos lados:

  · en el jar: los 58 disparadores de logro que existen y ninguno es «un bicho
    ha muerto»; solo `player_killed_entity` y `entity_killed_player`;
  · en los logs de verdad del servidor: 317 muertes reconocidas, cero de un
    animal. El aviso de «se te ha muerto la mascota» va SOLO al dueño y no pasa
    por la consola.

Así que esto cubre **únicamente** las muertes causadas por un jugador. Las
demás —creeper, lava, caída, ahogarse— las detecta el censo
(`scripts/mascotas.py`) por desaparición, sin causa y unos minutos después.

CÓMO FUNCIONA
-------------
Un logro oculto **por especie**, no por bicho. Los logros se anuncian en el
chat y eso sí queda escrito en el log, que es lo único que la Historia puede
leer:

    sofidiaz has made the advancement [☠ Lobo]

La víctima se reconoce por una **etiqueta** (`cf_wolf`) que el panel pone solo,
por RCON, en cuanto el censo ve un animal domado o con nombre que no la lleva.

🔴 POR QUÉ POR ESPECIE Y NO POR BICHO (la decisión que importa)
---------------------------------------------------------------
La primera versión de este script hacía **un logro por cada bicho**. Medido en
el servidor de Juan: 97 animales con nombre → 97 logros.

Y eso importa porque, según el bytecode del jar
(`SimpleCriterionTrigger.trigger`), cada vez que un jugador mata CUALQUIER mob
el servidor recorre todos los criterios pendientes de ese disparador:

    Map<...> map = adv.getTriggerMapForType(this);
    if (map == null || map.isEmpty()) return;      // por tick no cuesta nada
    for (entry : map.entrySet())                    // …pero los recorre TODOS
        if (pred.test(entry.getValue())) …

Vanilla ya trae **89 criterios** con `player_killed_entity` (41 solo en
`kill_all_mobs`). Añadir 97 era más que duplicar ese camino. Con un logro por
especie son **8**, un +9 %, y **siguen siendo 8 con 500 mascotas**.

El otro motivo, igual de importante: por bicho, el datapack hay que
regenerarlo y hacer `/reload` cada vez que alguien doma algo. Por especie el
datapack **se instala una vez y no se vuelve a tocar jamás**: para él un lobo
nuevo es «un lobo».

Lo que se pierde: el mensaje del juego dice la especie, no el nombre. El nombre
lo pone la Historia cruzando el aviso con el censo, que sí sabe cuál faltó.

🔴 EL LOGRO SE REVOCA A SÍ MISMO
--------------------------------
Un logro se consigue UNA VEZ por jugador. Sin esto, el segundo lobo que mate
alguien no avisaría nunca. Cada logro lleva una función de recompensa con una
línea, `advancement revoke @s only …`, que lo rearma en el acto.

USO (en el servidor)
--------------------
    python3 ~/panel/scripts/vigilancia.py --ver    # enseña qué haría, no toca nada
    python3 ~/panel/scripts/vigilancia.py          # lo instala
    python3 ~/panel/scripts/vigilancia.py --quitar # lo borra

Después, una sola vez: `reload` en la consola. Nunca más.
"""
import glob, json, os, re, shutil, sys, zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

HOME  = Path.home()
MC    = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
PANEL = Path(os.environ.get("PANEL_DIR", AQUI.parent))
WORLD = MC / "world"
PACK  = WORLD / "datapacks" / "vigilancia"
NS    = "vigilancia"
MARCA = "☠"                      # con esto el panel distingue estos logros
LISTA = PANEL / "data" / "vigilados.json"

# Las especies que se nombran en el mensaje del juego. El resto de bichos con
# nombre —ranas, sniffers, aldeanos— caen en el cajón de sastre.
#
# Cada grupo tiene SU PROPIA etiqueta. Así el predicado de cada logro es solo
# «lleva esta etiqueta», sin filtro de tipo, y ningún bicho puede disparar dos
# logros a la vez (que es lo que pasaría con un cajón de sastre sin tipo).
ESPECIES = ["wolf", "cat", "parrot", "horse", "donkey", "mule", "llama"]
OTRO = "_otro"
TOPE = 12                        # tope duro de logros: ver el bloque de arriba

# Nombres de respaldo por si `mensajes.json` no está todavía. Lo normal es que
# salgan del jar, vía build-mensajes.py, que es de donde salen todos los textos
# de este panel.
RESPALDO = {"wolf": "Lobo", "cat": "Gato", "parrot": "Loro", "horse": "Caballo",
            "donkey": "Burro", "mule": "Mula", "llama": "Llama",
            OTRO: "Animal con nombre"}


def etiqueta_de(grupo):
    return "cf_" + (grupo[1:] if grupo.startswith("_") else grupo)


# ------------------------------------------------------------------- el jar
def jar_del_servidor():
    """Desde 1.18 el server.jar es solo un lanzador; el bueno está en versions/."""
    # 🔴 Por FECHA, no por nombre. Ordenando texto, «26.2» va ANTES que «26.3»
    # (y «26.10» antes que las dos), así que esto cogía el jar MÁS VIEJO.
    # Ver claude/el-jar-por-nombre.md: es la quinta vez que aparece.
    cand = sorted(glob.glob(str(MC / "versions/**/server-*.jar"), recursive=True),
                  key=os.path.getmtime, reverse=True)
    if not cand:
        cand = [str(MC / "server.jar")]
    for p in cand:
        try:
            z = zipfile.ZipFile(p)
            if any("/advancement" in n and n.endswith(".json") for n in z.namelist()):
                return p, z
        except Exception:
            pass
    raise SystemExit("No encontré un jar con logros dentro. Busqué en %s" % (MC / "versions"))


def aprender_del_jar(z):
    """Saca del jar TODO lo que cambia entre versiones. Nada de memoria."""
    nombres = z.namelist()

    # 1) ¿la carpeta es 'advancement' o 'advancements'?
    carpeta = None
    for n in nombres:
        m = re.match(r"data/minecraft/(advancements?)/", n)
        if m:
            carpeta = m.group(1)
            break
    if not carpeta:
        raise SystemExit("No encuentro la carpeta de logros dentro del jar")

    # 2) pack_format
    #
    # 🔴 Esto estaba mal y se veía: el script imprimía «pack_format ... None».
    # Mojang cambió la forma de version.json y ahora es
    #     "pack_version": {"resource_major":88, "data_major":107, "data_minor":1}
    # mientras que antes era  {"resource": 88, "data": 41}.
    # Leyendo solo la clave vieja salía None y el pack.mcmeta se escribía SIN
    # pack_format, que es la forma más rápida de que Minecraft no cargue el
    # datapack y nadie sepa por qué.
    pack_format = None
    try:
        pv = json.loads(z.read("version.json")).get("pack_version")
        if isinstance(pv, dict):
            pack_format = pv.get("data_major", pv.get("data"))
        else:
            pack_format = pv
    except Exception:
        pass

    # 3) un logro REAL que use player_killed_entity, para copiar su esquema
    molde = None
    for n in nombres:
        if not n.startswith("data/minecraft/%s/" % carpeta) or not n.endswith(".json"):
            continue
        try:
            d = json.loads(z.read(n))
        except Exception:
            continue
        for crit in (d.get("criteria") or {}).values():
            if crit.get("trigger", "").endswith("player_killed_entity"):
                molde = (n, d, crit)
                break
        if molde:
            break

    # 4) un logro con display, para saber cómo se llaman sus campos
    display = None
    for n in nombres:
        if not n.startswith("data/minecraft/%s/" % carpeta) or not n.endswith(".json"):
            continue
        try:
            d = json.loads(z.read(n))
        except Exception:
            continue
        if isinstance(d.get("display"), dict) and "icon" in d["display"]:
            display = (n, d["display"])
            break

    return {"carpeta": carpeta, "pack_format": pack_format,
            "molde": molde, "display": display}


def clave_anuncio(display):
    """announce_to_chat (nuevo) vs announceToChat (viejo). Se mira el real."""
    for k in display:
        if k.lower().replace("_", "") == "announcetochat":
            return k
    return "announce_to_chat"


def icono_como(display):
    """El icono es {"item": "..."} en las viejas y {"id": "..."} en las nuevas."""
    ic = display.get("icon") or {}
    for k in ("id", "item"):
        if k in ic:
            return k
    return "id"


# --------------------------------------------------- condición de la víctima
def condicion_victima(molde_crit, etiqueta):
    """Copia la forma EXACTA de las condiciones del logro real y le cambia el
    predicado por «lleva esta etiqueta».

    En unas versiones `entity` es un objeto-predicado y en otras una lista de
    condiciones. Se detecta mirando el logro de verdad, no adivinando.
    """
    cond = json.loads(json.dumps(molde_crit.get("conditions") or {}))   # copia
    pred = {"nbt": '{Tags:["%s"]}' % etiqueta}
    ent = cond.get("entity")
    if isinstance(ent, list):
        cond["entity"] = [{"condition": "minecraft:entity_properties",
                           "entity": "this", "predicate": pred}]
    else:
        cond["entity"] = pred
    return cond


# ------------------------------------------------------------------ nombres
def nombres_de_especie():
    """Del jar, vía `mensajes.json` (clave `entity.minecraft.<id>`), como todo
    el texto de este panel. Si aún no se ha generado, se usa el respaldo."""
    fuera = dict(RESPALDO)
    try:
        bichos = (json.loads((PANEL / "data" / "mensajes.json").read_text())
                  .get("bichos") or {})
    except Exception:
        return fuera, False
    visto = False
    for e in ESPECIES:
        n = (bichos.get(e) or {}).get("es") or (bichos.get(e) or {}).get("en")
        if n:
            fuera[e] = n
            visto = True
    return fuera, visto


# ------------------------------------------------------------------- borrar
def quitar():
    if PACK.exists():
        shutil.rmtree(PACK)
        print("datapack borrado: %s" % PACK)
        print("Haz `reload` en la consola para que el servidor se entere.")
    else:
        print("no había nada que borrar en %s" % PACK)
    if LISTA.exists():
        LISTA.unlink()
        print("y el panel deja de esperar sus avisos")
    return 0


# ------------------------------------------------------------------- el pack
def main():
    if "--quitar" in sys.argv:
        return quitar()
    solo_ver = "--ver" in sys.argv

    ruta_jar, z = jar_del_servidor()
    print("jar del servidor: %s" % Path(ruta_jar).name)
    info = aprender_del_jar(z)

    print("\nLo que he APRENDIDO del jar (no de memoria):")
    print("  carpeta de logros ...... data/<ns>/%s/" % info["carpeta"])
    print("  pack_format ............ %s" % info["pack_format"])
    if info["pack_format"] is None:
        print("\n✗ No he podido leer el pack_format del jar. Sin él, Minecraft")
        print("  puede negarse a cargar el datapack sin decir nada. Me paro.")
        return 1
    if not info["molde"]:
        print("\n✗ No encontré ningún logro que use player_killed_entity en el jar.")
        print("  Sin un molde real no me invento el formato. Párate aquí y dímelo.")
        return 1
    nom_molde, _d, crit = info["molde"]
    print("  molde de 'matar' ....... %s" % nom_molde.split("/")[-1])
    print("  disparador ............. %s" % crit.get("trigger"))
    ent = (crit.get("conditions") or {}).get("entity")
    print("  la víctima se expresa .. %s" % ("como LISTA de condiciones"
                                             if isinstance(ent, list) else "como objeto-predicado"))
    if not info["display"]:
        print("\n✗ No encontré ningún logro con 'display' para copiar el formato.")
        return 1
    nom_disp, disp = info["display"]
    k_anuncio, k_icono = clave_anuncio(disp), icono_como(disp)
    print("  molde de 'display' ..... %s" % nom_disp.split("/")[-1])
    print("  campo del anuncio ...... %s" % k_anuncio)
    print("  campo del icono ........ %s" % k_icono)

    nombres, del_jar = nombres_de_especie()
    grupos = ESPECIES + [OTRO]
    print("\nLogros que se van a generar: %d  (tope %d)" % (len(grupos), TOPE))
    for g in grupos:
        print("    %-10s etiqueta %-10s título «%s %s»"
              % (g, etiqueta_de(g), MARCA, nombres[g]))
    if not del_jar:
        print("\n  ⚠ nombres de respaldo: corre build-mensajes.py para sacarlos del jar")

    # 🔴 El tope no es decorativo. Es la promesa de rendimiento: vanilla ya trae
    # 89 criterios de este disparador y se recorren todos en cada muerte. Si
    # alguien añade especies sin pensarlo, esto se planta antes de instalarlo.
    if len(grupos) > TOPE:
        print("\n✗ %d logros pasan del tope de %d. No lo instalo." % (len(grupos), TOPE))
        print("  Vanilla ya evalúa 89 criterios de este tipo en CADA muerte;")
        print("  el presupuesto de este datapack es no pasar de un 10%% más.")
        return 1

    if solo_ver:
        print("\n(--ver: no he tocado nada)")
        return 0

    # ------------------------------------------------------------ escribir
    if PACK.exists():
        shutil.rmtree(PACK)
    carp_adv = PACK / "data" / NS / info["carpeta"]
    carp_adv.mkdir(parents=True, exist_ok=True)

    (PACK / "pack.mcmeta").write_text(json.dumps(
        {"pack": {"description": "Vigilancia de animales (panel Califree)",
                  "pack_format": info["pack_format"]}}, indent=2))

    etiquetas, titulos = {}, {}
    for g in grupos:
        et, nom = etiqueta_de(g), nombres[g]
        etiquetas[g] = et
        titulos["%s %s" % (MARCA, nom)] = g
        adv = {
            "criteria": {"matar": {"trigger": crit["trigger"],
                                   "conditions": condicion_victima(crit, et)}},
            "display": {
                "icon": {k_icono: "minecraft:skeleton_skull"},
                "title": "%s %s" % (MARCA, nom),
                "description": "Ha matado a un %s de alguien" % nom.lower(),
                "frame": "task",
                "show_toast": False,
                k_anuncio: True,
                "hidden": True,
            },
            # Sin esto solo avisaría de la PRIMERA vez que cada jugador mata un
            # animal de ese tipo. Un logro se consigue una vez y ya.
            "rewards": {"function": "%s:rearmar_%s" % (NS, et)},
        }
        (carp_adv / ("%s.json" % et)).write_text(
            json.dumps(adv, indent=2, ensure_ascii=False))

        # La carpeta de funciones pasó de `functions` a `function` en la misma
        # tanda en que los logros pasaron a `advancement`. En el jar NO hay
        # funciones, así que aquí no se puede aprender el nombre bueno como se
        # aprende todo lo demás: se escriben las DOS. Una carpeta que el
        # servidor no conozca la ignora, y así no hay nada que adivinar.
        for nombre_carpeta in ("function", "functions"):
            c = PACK / "data" / NS / nombre_carpeta
            c.mkdir(parents=True, exist_ok=True)
            (c / ("rearmar_%s.mcfunction" % et)).write_text(
                "# rearma el logro para que avise también la próxima vez\n"
                "advancement revoke @s only %s:%s\n" % (NS, et))

    LISTA.parent.mkdir(parents=True, exist_ok=True)
    LISTA.write_text(json.dumps(
        {"v": 2, "marca": MARCA, "etiquetas": etiquetas, "titulos": titulos,
         "otro": OTRO}, indent=2, ensure_ascii=False))

    print("\n" + "═" * 62)
    print("  Datapack escrito: %s" % PACK)
    print("  %d logros, uno por especie — y ya no se vuelve a tocar nunca" % len(grupos))
    print("═" * 62)
    print("""
  UN SOLO PASO, y no hay que repetirlo jamás:

      reload            (en la consola del panel)

  A partir de ahí el panel se encarga de todo: el censo ve cada animal
  domado o con nombre, le pone su etiqueta por RCON si no la lleva, y en la
  Historia sale «sofidiaz mató a Fido (Lobo)».

  Si domas un animal nuevo dentro de un mes, no hay que hacer nada.

  Para quitarlo:  python3 ~/panel/scripts/vigilancia.py --quitar
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
