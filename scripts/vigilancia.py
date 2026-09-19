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

🔴 SE PRUEBA SOLO, Y SI NO PASA NO SE INSTALA
---------------------------------------------
El 18/09/2026 este datapack dejó el servidor sin arrancar, y lo peor no fue el
fallo: fue que **no había forma de verlo**. `reload` no lo detecta, porque en
esta versión los logros son datos de REGISTRO y los registros solo se cargan al
abrir el mundo. O sea que un datapack roto se ve perfecto en un servidor
encendido y tumba el siguiente arranque, días después.

Por eso esto ya no escribe en `world/datapacks/` directamente. Genera el pack
aparte, arranca con él un servidor de mentira (`probar-datapack.sh`: mundo nuevo
en /tmp, otro puerto, el mundo de verdad ni se abre) y **solo si carga** lo
mueve al mundo.

USO (en el servidor)
--------------------
    python3 ~/panel/scripts/vigilancia.py --ver    # enseña qué haría, no toca nada
    python3 ~/panel/scripts/vigilancia.py          # lo prueba y, si pasa, lo instala
    python3 ~/panel/scripts/vigilancia.py --quitar # lo borra

Después, una sola vez: `reload` en la consola. Nunca más.
"""
import glob, json, os, re, shutil, struct, subprocess, sys, zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

HOME  = Path.home()
MC    = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
PANEL = Path(os.environ.get("PANEL_DIR", AQUI.parent))
WORLD = MC / "world"
PACK  = WORLD / "datapacks" / "vigilancia"
NS    = "vigilancia"

# 🔴 El datapack NO se escribe directamente en el mundo. Se escribe aquí, se
# arranca un servidor de mentira que intente cargarlo, y solo si carga se mueve
# al mundo. El 18/09/2026 se escribió directo, cargaba mal, y el servidor se
# quedó sin arrancar — y nadie podía saberlo, porque `reload` no comprueba esto
# (los logros son datos de REGISTRO y los registros solo se cargan al abrir el
# mundo). Ver claude/vigilancia-mascotas.md.
BANCO  = PANEL / "data" / "vigilancia-pack"
PRUEBA = AQUI / "probar-datapack.sh"
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


def cadenas_de_clase(datos):
    """Los textos del pool de constantes de un .class, en orden y sin librerías.

    Hace falta para preguntarle al jar QUÉ sub-predicados de entidad existen.
    Eso no está en ningún JSON de vanilla —vanilla nunca filtra por etiqueta—,
    solo en el código que los registra.
    """
    if datos[:4] != b"\xca\xfe\xba\xbe":
        return []
    n = struct.unpack_from(">H", datos, 8)[0]
    i, fuera, k = 10, [], 1
    while k < n:
        etiqueta = datos[i]; i += 1
        if etiqueta == 1:
            largo = struct.unpack_from(">H", datos, i)[0]; i += 2
            fuera.append(datos[i:i + largo].decode("utf-8", "replace")); i += largo
        elif etiqueta in (7, 8, 16, 19, 20):  i += 2
        elif etiqueta == 15:                  i += 3
        elif etiqueta in (5, 6):              i += 8; k += 1   # long/double
        else:                                 i += 4
        k += 1
    return fuera


def predicado_de_etiquetas(z, nombres):
    """Cómo se pide «lleva esta etiqueta» en ESTA versión.

    🔴 Aquí es donde se tumbó el servidor el 18/09/2026. Yo escribí de memoria

        {"nbt": "{Tags:[\\"cf_wolf\\"]}"}

    y el arranque murió con `No key type in MapLike[{"nbt":...}]`. En esta
    versión un predicado de entidad es un MAPA de `<id registrado>: <valor>`
    —se ve en el propio vanilla: `{"minecraft:entity_type": "minecraft:blaze"}`—
    y `nbt` a secas no es uno de esos ids.

    Lo que sí hay, y es mejor, es `entity_tags`: un predicado dedicado a las
    etiquetas de scoreboard, con `any_of` / `all_of` / `none_of`. Compara
    conjuntos en vez de serializar el NBT entero del bicho en cada muerte, así
    que además de correcto es más barato.

    Devuelve una función etiqueta -> predicado, o None si no lo encuentra.
    """
    clases = [n for n in nombres
              if n.endswith(".class")
              and ("predicates/entity" in n or "critereon" in n)]
    for n in clases:
        if not n.endswith("EntityTagPredicate.class"):
            continue
        cad = cadenas_de_clase(z.read(n))
        # El nombre del campo EN EL JSON: `any_of`. Ojo, que en el pool también
        # está `anyOf`, que es el nombre del campo del record en Java y aparece
        # ANTES. Coger el primero que casara escribía `anyOf` en el datapack y
        # el logro no habría cargado — el mismo tipo de error que todo esto.
        # Se prefiere el de guion bajo, que es el que usan los códecs.
        claves = [c for c in cad if re.fullmatch(r"an[yY]_?[oO]f", c)]
        clave = next((c for c in claves if "_" in c), claves[0] if claves else "any_of")
        # y el id con el que está registrado, que sale de la clase que registra
        for m in clases:
            if not m.endswith("EntitySubPredicates.class"):
                continue
            ids = cadenas_de_clase(z.read(m))
            if "entity_tags" in ids and "entity_type" in ids:
                return (lambda et, _c=clave:
                        {"minecraft:entity_tags": {_c: [et]}}), "entity_tags"
    return None, None


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

    # 3) un logro REAL que use player_killed_entity **y que filtre a la
    #    víctima**, para copiar su esquema.
    #
    # 🔴 Antes valía cualquiera con ese disparador, y ahí estaba el fallo que
    # tumbó el servidor: el primero que salía era uno que filtra por el ARMA
    # (`killing_blow`) y no tiene `entity`. Al no haber `entity` de molde, se
    # inventaba la forma —un diccionario suelto— y encima se quedaba con el
    # `killing_blow` del molde, o sea que el logro solo habría saltado matando
    # con una carga de viento de breeze. Dos fallos de un solo descuido.
    #
    # La forma buena se copia entera de vanilla:
    #   "entity": [{"condition": "minecraft:entity_properties",
    #               "entity": "this",
    #               "predicate": {"minecraft:entity_type": "minecraft:blaze"}}]
    molde = None
    for n in sorted(nombres):
        if not n.startswith("data/minecraft/%s/" % carpeta) or not n.endswith(".json"):
            continue
        try:
            d = json.loads(z.read(n))
        except Exception:
            continue
        for crit in (d.get("criteria") or {}).values():
            if not crit.get("trigger", "").endswith("player_killed_entity"):
                continue
            ent = (crit.get("conditions") or {}).get("entity")
            if ent in (None, [], {}):          # sin víctima no sirve de molde
                continue
            molde = (n, d, crit)
            break
        if molde:
            break

    # 3b) una RAÍZ de vanilla: de ahí salen el `background` (sin él, esta
    #     versión se niega a arrancar: «Visible advancement roots must have
    #     background») y unos criterios que seguro existen en este jar.
    raiz = None
    for n in sorted(nombres):
        if not re.match(r"data/minecraft/%s/[a-z_]+/root\.json$" % carpeta, n):
            continue
        try:
            d = json.loads(z.read(n))
        except Exception:
            continue
        fondo = (d.get("display") or {}).get("background")
        if fondo and d.get("criteria"):
            raiz = {"de": n, "background": fondo, "criteria": d["criteria"]}
            break

    # 3c) cómo se pide «lleva esta etiqueta» (ver predicado_de_etiquetas)
    hacer_pred, nombre_pred = predicado_de_etiquetas(z, nombres)

    # 3d) el `pack.mcmeta`, copiado de los datapacks que Mojang mete DENTRO
    #     del jar (minecart_improvements, trade_rebalance…).
    #
    # 🔴 `pack_format` a secas ya no vale. Con el 121 de la 26.3 el servidor
    # avisa: «Pack declares support for version newer than 81, but is missing
    # mandatory fields min_format and max_format», y carga por un camino de
    # respaldo — o sea, de milagro. Los suyos llevan min_format y max_format y
    # NO llevan pack_format. Se copia su forma entera y solo se cambia la
    # descripción: así, el día que Mojang vuelva a cambiarlo, esto lo sigue.
    meta = None
    for n in sorted(nombres):
        if not re.match(r"data/minecraft/datapacks/[^/]+/pack\.mcmeta$", n):
            continue
        try:
            p = (json.loads(z.read(n)) or {}).get("pack")
        except Exception:
            continue
        if isinstance(p, dict) and any(k.endswith("format") for k in p):
            meta = {"de": n, "pack": p}
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
            "molde": molde, "display": display, "raiz": raiz,
            "hacer_pred": hacer_pred, "nombre_pred": nombre_pred, "meta": meta}


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
def condicion_victima(molde_crit, etiqueta, hacer_pred):
    """Coge el `entity` del logro real TAL CUAL y le cambia solo el predicado.

    🔴 Dos veces se tumbó el servidor por no hacer exactamente esto.

    Primero se copiaba el bloque `conditions` entero y se le metía `entity`
    dentro; como el molde traía su propio `killing_blow`, el logro heredaba «y
    además hay que matarlo con tal arma». Del molde se coge la FORMA, no las
    condiciones.

    Y luego se reconstruía el envoltorio a mano, que es lo que se rompió entre
    la 26.2 y la 26.3 — Mojang cambió la forma **entre dos versiones seguidas**:

        26.2:  "entity": [{"condition": "minecraft:entity_properties",
                           "entity": "this", "predicate": {…}}]
        26.3:  "entity":  {"type": "minecraft:entity_properties",
                           "entity": "this", "predicate": {…}}

    Lista contra objeto, y `condition` contra `type`. Reconocer «lista u
    objeto» no basta: hay que copiar el envoltorio del jar sin mirarlo y
    cambiar únicamente `predicate`. Así da igual lo que Mojang invente la
    próxima vez, mientras siga habiendo un `predicate` dentro.
    """
    ent = (molde_crit.get("conditions") or {}).get("entity")
    copia = json.loads(json.dumps(ent))                       # copia profunda
    dentro = copia[0] if isinstance(copia, list) else copia
    if not isinstance(dentro, dict) or "predicate" not in dentro:
        raise SystemExit(
            "El molde del jar no tiene un `predicate` donde esperaba:\n  %s\n"
            "No me lo invento: eso es lo que tumbó el servidor. Dímelo y miro "
            "la forma nueva." % json.dumps(ent)[:300])
    dentro["predicate"] = hacer_pred(etiqueta)
    return {"entity": [dentro] if isinstance(copia, list) else dentro}


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
        print("\n✗ No encontré ningún logro que use player_killed_entity CON un")
        print("  filtro de víctima. Sin un molde real no me invento el formato.")
        print("  Párate aquí y dímelo.")
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

    # 🔴 Los dos que faltaban el día que se cayó el servidor. Se paran en seco,
    # como el pack_format: un datapack a medias no es «casi bueno», es un
    # servidor que no arranca.
    if not info["hacer_pred"]:
        print("\n✗ Este jar no registra `entity_tags`, así que no sé pedir")
        print("  «lleva esta etiqueta» sin inventármelo — y inventármelo fue")
        print("  exactamente lo que tumbó el servidor el 18/09/2026. Me paro.")
        return 1
    print("  filtro por etiqueta .... minecraft:%s" % info["nombre_pred"])
    if not info["raiz"]:
        print("\n✗ No encontré ninguna raíz de vanilla con `background`. Esta")
        print("  versión no arranca con una raíz visible sin él. Me paro.")
        return 1
    print("  fondo de la raíz ....... %s  (de %s)"
          % (info["raiz"]["background"], info["raiz"]["de"].split("/")[-2]))
    if info["meta"]:
        print("  campos del pack.mcmeta . %s  (de %s)"
              % (", ".join(k for k in info["meta"]["pack"] if k != "description"),
                 info["meta"]["de"].split("/")[-2]))
    else:
        print("  campos del pack.mcmeta . solo pack_format (no hay datapacks "
              "dentro del jar de los que copiar la forma)")
    print("  ejemplo de condición ... %s"
          % json.dumps(condicion_victima(crit, "cf_wolf", info["hacer_pred"]),
                       ensure_ascii=False)[:150])

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
    if BANCO.exists():
        shutil.rmtree(BANCO)
    carp_adv = BANCO / "data" / NS / info["carpeta"]
    carp_adv.mkdir(parents=True, exist_ok=True)

    # la forma se copia de los datapacks de dentro del jar; si este jar no
    # trajera ninguno, se cae al campo de siempre (y la prueba lo pillaría).
    if info["meta"]:
        paquete = json.loads(json.dumps(info["meta"]["pack"]))
    else:
        paquete = {"pack_format": info["pack_format"]}
    paquete["description"] = "Vigilancia de animales (panel Califree)"
    (BANCO / "pack.mcmeta").write_text(
        json.dumps({"pack": paquete}, indent=2, ensure_ascii=False))

    # ── la raíz ──────────────────────────────────────────────────────────
    # Los ocho logros llevan `display` porque el anuncio en el chat vive ahí.
    # Un logro con `display` y sin `parent` es una RAÍZ, y esta versión exige
    # `background` en las raíces visibles — era el segundo error del arranque.
    #
    # Se podrían poner ocho fondos y tener ocho raíces, pero entonces el menú
    # de logros del juego saldría con ocho pestañas nuevas. Con una raíz propia
    # y los ocho colgando, es UNA pestaña. Sus criterios se copian tal cual de
    # la raíz de vanilla: así el disparador seguro que existe en este jar, y da
    # igual que no se cumpla nunca — un logro hijo no necesita que su padre
    # esté conseguido.
    (carp_adv / "raiz.json").write_text(json.dumps({
        "display": {
            "icon": {k_icono: "minecraft:skeleton_skull"},
            "title": "%s Vigilancia" % MARCA,
            "description": "Animales domesticados de otros jugadores",
            "background": info["raiz"]["background"],
            "frame": "task",
            "show_toast": False,
            k_anuncio: False,
        },
        "criteria": info["raiz"]["criteria"],
    }, indent=2, ensure_ascii=False))

    etiquetas, titulos = {}, {}
    for g in grupos:
        et, nom = etiqueta_de(g), nombres[g]
        etiquetas[g] = et
        titulos["%s %s" % (MARCA, nom)] = g
        adv = {
            "parent": "%s:raiz" % NS,
            "criteria": {"matar": {
                "trigger": crit["trigger"],
                "conditions": condicion_victima(crit, et, info["hacer_pred"])}},
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
            c = BANCO / "data" / NS / nombre_carpeta
            c.mkdir(parents=True, exist_ok=True)
            (c / ("rearmar_%s.mcfunction" % et)).write_text(
                "# rearma el logro para que avise también la próxima vez\n"
                "advancement revoke @s only %s:%s\n" % (NS, et))

    # ── la prueba, antes de que esto toque el mundo ──────────────────────
    #
    # 🔴 Esto no es opcional y no se salta por defecto. El 18/09/2026 el
    # datapack se escribió directo en `world/datapacks/`, `reload` no se quejó
    # —no puede: los logros son datos de registro y los registros solo se
    # cargan al abrir el mundo— y el servidor se quedó sin arrancar horas
    # después, cuando ya nadie lo relacionaba con esto.
    #
    # Lo que hace `probar-datapack.sh`: arranca un servidor aparte, con un
    # mundo nuevo en /tmp y en otro puerto, y mira si carga. Tarda un minuto y
    # pico y el mundo de verdad ni se abre.
    if "--sin-probar" in sys.argv:
        print("\n  ⚠ --sin-probar: me salto el banco de pruebas porque me lo has")
        print("    pedido. Si el datapack está mal, el servidor no arrancará la")
        print("    próxima vez que se reinicie, no ahora.")
    elif not PRUEBA.exists():
        print("\n✗ No encuentro %s." % PRUEBA)
        print("  Sin poder probarlo NO lo meto en el mundo: un datapack malo no")
        print("  se nota hasta el siguiente arranque. Despliega el panel entero")
        print("  y vuelve a correr esto.")
        print("  (El datapack generado se queda en %s)" % BANCO)
        return 1
    else:
        print("\n" + "═" * 62)
        print("  Probándolo en un mundo de mentira antes de tocar el tuyo…")
        print("═" * 62)
        r = subprocess.run(["bash", str(PRUEBA), str(BANCO)],
                           env=dict(os.environ, MC_DIR=str(MC), PANEL_DIR=str(PANEL)))
        if r.returncode == 2:
            # el banco de pruebas no llegó a arrancar: eso NO es «el datapack
            # está mal», y decirlo así mandaría a buscar donde no hay nada.
            print("\n✗ No he podido probarlo, así que no lo instalo.")
            print("  Esto no dice que el datapack esté mal: dice que la prueba no")
            print("  pudo correr (mira el motivo justo aquí arriba).")
            print("  El datapack generado se queda en %s." % BANCO)
            return 1
        if r.returncode != 0:
            print("\n✗ No lo instalo: ese datapack dejaría el servidor sin arrancar.")
            print("  Tu mundo está intacto — esto ni se ha acercado a él.")
            print("  El datapack generado se queda en %s por si quieres mirarlo." % BANCO)
            return 1

    # ── y ahora sí, al mundo ─────────────────────────────────────────────
    PACK.parent.mkdir(parents=True, exist_ok=True)
    if PACK.exists():
        shutil.rmtree(PACK)
    shutil.copytree(BANCO, PACK)

    LISTA.parent.mkdir(parents=True, exist_ok=True)
    LISTA.write_text(json.dumps(
        {"v": 2, "marca": MARCA, "etiquetas": etiquetas, "titulos": titulos,
         "otro": OTRO}, indent=2, ensure_ascii=False))

    print("\n" + "═" * 62)
    print("  Datapack probado e instalado: %s" % PACK)
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
