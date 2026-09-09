#!/usr/bin/env python3
"""
Vigila a los mobs con nombre: avisa en la Historia del panel cuando alguien mata
a uno, con nombre y apellido.

CÓMO FUNCIONA, porque no es obvio
---------------------------------
Minecraft vanilla NO registra la muerte de un mob en ningún sitio. No hay línea
en el log, no hay fichero, no hay nada. Así que hay que fabricarla.

El truco: un **logro oculto por cada bicho vigilado**, que salta cuando un
jugador lo mata. Los logros SÍ se anuncian en el chat y ESO sí queda escrito en
el log del servidor, que es justo lo que la Historia del panel ya lee:

    Tazzk93 has made the advancement [☠ Mace Windu]

Cada mob muere una sola vez, así que el logro salta una vez y ya. Sin funciones
por tick, sin coste continuo: un logro solo se comprueba cuando ocurre el evento.

Para poder apuntar a un bicho concreto se le pone una ETIQUETA (`vg_0001`), y el
logro exige esa etiqueta. Las etiquetas se guardan en el mundo, así que se ponen
una vez.

⚠️ LO QUE **NO** CUBRE: las muertes que no causa un jugador. Caída, lava, un
creeper, ahogarse. En vanilla no existe ningún disparador para eso — ni con
datapack. Para esas, la única señal es que el bicho deje de aparecer en
`buscar-entidad.py`.

EL FORMATO SE LEE DEL JAR, NO SE ADIVINA
----------------------------------------
Dónde van las carpetas (`advancement` o `advancements`), cómo se llama el campo
del icono, si el anuncio es `announce_to_chat` o `announceToChat`, qué número de
`pack_format` toca… todo eso cambia entre versiones. En vez de escribirlo de
memoria, este script abre el jar del servidor, busca un logro DE VERDAD que use
`player_killed_entity` y copia su esquema exacto. Si Mojang lo cambia otra vez,
el script se adapta solo.

USO (en el servidor)
--------------------
    python3 ~/panel/scripts/vigilancia.py --ver        # solo enseña qué haría
    python3 ~/panel/scripts/vigilancia.py              # genera e instala
    python3 ~/panel/scripts/vigilancia.py --comandos   # las órdenes de etiquetar

Después: `/reload` en la consola y pegar las órdenes de `--comandos`.
"""
import glob, json, os, re, shutil, sys, zipfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))

HOME = Path.home()
MC   = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
WORLD = MC / "world"
PACK = WORLD / "datapacks" / "vigilancia"
NS   = "vigilancia"
MARCA = "☠"          # ☠ — con esto el panel distingue estos logros
LISTA = AQUI.parent / "data" / "vigilados.json"


# ------------------------------------------------------------------- el jar
def jar_del_servidor():
    cand = sorted(glob.glob(str(MC / "versions/**/server-*.jar"), recursive=True))
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

    # 2) pack_format: viene en version.json del propio jar
    pack_format = None
    try:
        vj = json.loads(z.read("version.json"))
        pv = vj.get("pack_version")
        pack_format = pv.get("data") if isinstance(pv, dict) else pv
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
    predicado por «tiene esta etiqueta».

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


# ----------------------------------------------------------------- la lista
def entidades_con_nombre():
    """Reutiliza el escáner que ya existe, para no tener dos formas distintas
    de leer el mundo."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("be", AQUI / "buscar-entidad.py")
    be = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(be)
    return [h for h in be.escanear() if h["nombre"]]


def limpio(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s.lower())[:40] or "x"


def main():
    solo_ver = "--ver" in sys.argv
    solo_cmds = "--comandos" in sys.argv

    if solo_cmds:
        try:
            datos = json.loads(LISTA.read_text())
        except Exception:
            print("Primero genera el datapack:  python3 ~/panel/scripts/vigilancia.py")
            return 2
        print("# Pega esto en la consola del panel (o en el servidor).")
        print("# Solo etiqueta a los que estén en chunks CARGADOS: repítelo de vez")
        print("# en cuando, o cuando pongas nombres nuevos. Es inofensivo repetirlo.")
        for e in datos["vigilados"]:
            print('tag @e[name="%s",tag=!%s] add %s' % (e["nombre"], e["tag"], e["tag"]))
        return 0

    ruta_jar, z = jar_del_servidor()
    print("jar del servidor: %s" % Path(ruta_jar).name)
    info = aprender_del_jar(z)

    print("\nLo que he APRENDIDO del jar (no de memoria):")
    print("  carpeta de logros ...... data/<ns>/%s/" % info["carpeta"])
    print("  pack_format ............ %s" % info["pack_format"])
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

    print("\nLeyendo el mundo para saber a quién vigilar…")
    bichos = entidades_con_nombre()
    # los maniquíes del panel no son mobs que puedan morir
    bichos = [b for b in bichos if b["tipo"] not in ("mannequin", "armor_stand")]
    print("  → %d bichos con nombre a vigilar\n" % len(bichos))

    vigilados = []
    for i, b in enumerate(bichos, 1):
        vigilados.append({"nombre": b["nombre"], "tipo": b["tipo"],
                          "tag": "vg_%04d" % i, "dim": b["dim"], "pos": b["pos"]})

    if solo_ver:
        print("Se generarían %d logros ocultos en %s" % (len(vigilados), PACK))
        for v in vigilados[:12]:
            print("  %-28s %-16s %s" % (v["nombre"], v["tipo"], v["tag"]))
        if len(vigilados) > 12:
            print("  … y %d más" % (len(vigilados) - 12))
        print("\n(--ver: no he tocado nada)")
        return 0

    # ------------------------------------------------------------ escribir
    if PACK.exists():
        shutil.rmtree(PACK)
    carp = PACK / "data" / NS / info["carpeta"]
    carp.mkdir(parents=True, exist_ok=True)

    meta = {"pack": {"description": "Vigilancia de mobs con nombre (panel Califree)"}}
    if info["pack_format"] is not None:
        meta["pack"]["pack_format"] = info["pack_format"]
    (PACK / "pack.mcmeta").write_text(json.dumps(meta, indent=2))

    for v in vigilados:
        display = {
            "icon": {k_icono: "minecraft:skeleton_skull"},
            "title": "%s %s" % (MARCA, v["nombre"]),
            "description": "Ha matado a %s" % v["nombre"],
            "frame": "task",
            "show_toast": False,
            k_anuncio: True,
            "hidden": True,
        }
        adv = {"criteria": {"matar": {"trigger": crit["trigger"],
                                      "conditions": condicion_victima(crit, v["tag"])}},
               "display": display}
        (carp / ("%s_%s.json" % (v["tag"], limpio(v["nombre"])))).write_text(
            json.dumps(adv, indent=2, ensure_ascii=False))

    LISTA.parent.mkdir(parents=True, exist_ok=True)
    LISTA.write_text(json.dumps({"marca": MARCA, "vigilados": vigilados},
                                indent=2, ensure_ascii=False))

    print("═" * 62)
    print("  Datapack escrito: %s" % PACK)
    print("  %d logros ocultos, uno por bicho" % len(vigilados))
    print("═" * 62)
    print("""
  AHORA, dos pasos en la consola del panel:

    1)  reload
        (carga el datapack. Debe decir «Reloading!» sin errores)

    2)  pega las órdenes de etiquetar:
        python3 ~/panel/scripts/vigilancia.py --comandos

  Las etiquetas solo se ponen en chunks CARGADOS. Vuelve a pegarlas cuando
  estéis cerca de las zonas donde viven los bichos, o cuando pongas nombres
  nuevos. Repetirlo no hace daño.

  A partir de ahí, cuando alguien mate a uno vigilado saldrá solo en la
  Historia del panel: «Tazzk93 mató a Mace Windu».
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
