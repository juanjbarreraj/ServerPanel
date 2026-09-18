#!/usr/bin/env python3
"""
El datapack de vigilancia: que sea barato, que no haya que mantenerlo, y que la
frase acabe entera en la Historia.

POR QUÉ ASÍ
-----------
La versión anterior hacía **un logro por bicho**. Medido en el servidor de
Juan: 97 animales con nombre. Y según el bytecode del jar, en CADA muerte de
cualquier mob el servidor recorre todos los criterios pendientes de ese
disparador — de los que vanilla ya trae 89. Añadir 97 era más que duplicarlo.

Esta versión hace **un logro por especie**: ocho, y siguen siendo ocho con 500
mascotas. Aquí se comprueban las cuatro cosas de las que depende que eso
funcione:

  1. que de verdad sean pocos, y que el tope se respete;
  2. que cada logro se REVOQUE a sí mismo — un logro se consigue una vez por
     jugador, así que sin esto solo avisaría del primer lobo que mate cada uno;
  3. que el panel sepa poner las etiquetas solo, que es lo que hace que un
     animal domado el mes que viene quede vigilado sin tocar nada;
  4. que el aviso del juego («mató a un lobo») y el censo («faltó Fido») se
     crucen y salga una sola línea: «sofidiaz mató a Fido».

Correr:  python3 scripts/probar-vigilancia.py
"""
import importlib.util, json, os, shutil, subprocess, sys, tempfile, time, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
fallos, pasadas = [], 0
DUEÑO_UUID = "aaaaaaaa-1111-2222-3333-444444444444"
OTRO_UUID = "bbbbbbbb-1111-2222-3333-444444444444"


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


# ---------------------------------------------------------- un jar de mentira
# Con las MISMAS formas que el de verdad: la carpeta en singular, el
# `pack_version` nuevo (que es donde estaba el fallo del pack_format), un logro
# con `player_killed_entity` cuya víctima va como LISTA de condiciones, y otro
# con display. Un fixture cómodo es un fixture que miente.
#
# 🔴 Las formas de aquí son las del jar DE VERDAD, comprobadas contra
# server-26.2.jar el 18/09/2026. Si este fixture miente, la prueba pasa y el
# servidor no arranca — que es exactamente lo que ocurrió: el molde de antes
# llevaba `{"type": "minecraft:ghast"}`, que es la forma VIEJA, y el generador
# nunca tuvo que vérselas con la nueva.
PRED_VANILLA = {"minecraft:entity_type": "minecraft:ghast"}
MOLDE_MATAR = {
    "criteria": {"x": {"trigger": "minecraft:player_killed_entity",
                       "conditions": {"entity": [
                           {"condition": "minecraft:entity_properties",
                            "entity": "this",
                            "predicate": PRED_VANILLA}]}}}}
# Uno que dispara igual pero NO filtra a la víctima, y que además arrastra un
# `killing_blow`. Va el primero por orden alfabético a propósito: es el molde
# que se cogía antes, y con él el logro salía con «y además mátalo con una
# carga de viento», aparte de con el predicado en la forma equivocada.
MOLDE_TRAMPA = {
    "criteria": {"x": {"trigger": "minecraft:player_killed_entity",
                       "conditions": {"killing_blow": {
                           "direct_entity": {
                               "minecraft:entity_type": "minecraft:breeze_wind_charge"}}}}}}
MOLDE_DISPLAY = {"display": {"icon": {"id": "minecraft:map"}, "title": "x",
                             "description": "x", "frame": "task",
                             "announce_to_chat": True}}
MOLDE_RAIZ = {"display": {"icon": {"id": "minecraft:grass_block"}, "title": "r",
                          "description": "r", "frame": "task",
                          "background": "minecraft:gui/advancements/backgrounds/stone"},
              "criteria": {"crafting_table": {"trigger": "minecraft:inventory_changed"}}}


def clase_falsa(textos):
    """Un .class con esos textos en el pool de constantes.

    No pretende ser cargable: lo que se prueba es el lector del pool, que es
    como `vigilancia.py` averigua qué sub-predicados registra el jar. Eso no
    está en ningún JSON de vanilla, solo en el código.
    """
    import struct
    cuerpo = b""
    for t in textos:
        b = t.encode("utf-8")
        cuerpo += b"\x01" + struct.pack(">H", len(b)) + b
    return (b"\xca\xfe\xba\xbe" + struct.pack(">HH", 0, 65)
            + struct.pack(">H", len(textos) + 1) + cuerpo)


def jar_falso(ruta: Path, version="26.3", con_etiquetas=True):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("version.json", json.dumps(
            {"id": version, "pack_version": {"resource_major": 88, "resource_minor": 0,
                                             "data_major": 107, "data_minor": 1}}))
        z.writestr("data/minecraft/advancement/adventure/blowback.json",
                   json.dumps(MOLDE_TRAMPA))
        z.writestr("data/minecraft/advancement/nether/kill_a_mob.json",
                   json.dumps(MOLDE_MATAR))
        z.writestr("data/minecraft/advancement/adventure/adventuring_time.json",
                   json.dumps(MOLDE_DISPLAY))
        z.writestr("data/minecraft/advancement/story/root.json",
                   json.dumps(MOLDE_RAIZ))
        base = "net/minecraft/advancements/predicates/entity/"
        if con_etiquetas:
            # Ojo al orden: `anyOf` (el campo del record) va ANTES que `any_of`
            # (el del JSON) en el pool de verdad. Coger el primero escribía la
            # clave equivocada.
            z.writestr(base + "EntityTagPredicate.class",
                       clase_falsa(["anyOf", "allOf", "noneOf",
                                    "any_of", "all_of", "none_of"]))
            z.writestr(base + "EntitySubPredicates.class",
                       clase_falsa(["entity_type", "location", "distance", "nbt",
                                    "flags", "equipment", "entity_tags"]))


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-vigilancia-"))
    mc, panel = base / "minecraft", base / "panel"
    (panel / "data").mkdir(parents=True, exist_ok=True)
    (mc / "world/datapacks").mkdir(parents=True, exist_ok=True)
    (mc / "world/dimensions/minecraft/overworld/entities").mkdir(parents=True, exist_ok=True)
    (mc / "world/players/data").mkdir(parents=True, exist_ok=True)
    (mc / "world/players/stats").mkdir(parents=True, exist_ok=True)
    (mc / "logs").mkdir(parents=True, exist_ok=True)
    (mc / "mundos").mkdir(parents=True, exist_ok=True)
    (mc / "mundos/activo.json").write_text(json.dumps({"slug": "t1"}))
    jar_falso(mc / "versions/26.3/server-26.3.jar")
    print("escenario en %s" % base)

    (mc / "usercache.json").write_text(json.dumps([
        {"uuid": DUEÑO_UUID, "name": "Tazzk93"},
        {"uuid": OTRO_UUID, "name": "sofidiaz"}]))
    (mc / "whitelist.json").write_text((mc / "usercache.json").read_text())
    (mc / "logs/latest.log").write_text("[10:00:00] [Server thread/INFO]: Done (1s)!\n")
    (panel / "data/mensajes.json").write_text(json.dumps({
        "version": "26.3",
        "muertes": [{"clave": "death.attack.mob", "n": 2,
                     "rx": r"^([A-Za-z0-9_]{1,16}) was slain by (.+)$",
                     "en": "%1$s was slain by %2$s", "es": "%1$s fue asesinado por %2$s"}],
        "sesion": {"entro": {"rx": r"^([A-Za-z0-9_]{1,16}) joined the game$",
                             "en": "%s joined the game", "es": "%s se unió"},
                   "salio": {"rx": r"^([A-Za-z0-9_]{1,16}) left the game$",
                             "en": "%s left the game", "es": "%s se fue"}},
        "logros": {"tarea": {"rx": r"^([A-Za-z0-9_]{1,16}) has made the advancement (.+)$",
                             "en": "%s has made the advancement %s", "es": "%s logró %s"}},
        "efectos": [],
        "bichos": {"wolf": {"en": "Wolf", "es": "Lobo"},
                   "cat": {"en": "Cat", "es": "Gato"},
                   "parrot": {"en": "Parrot", "es": "Loro"}},
    }, ensure_ascii=False))

    entorno = dict(os.environ, MC_DIR=str(mc), PANEL_DIR=str(panel))

    def corre(*args):
        return subprocess.run([sys.executable, str(REPO / "scripts" / "vigilancia.py")]
                              + list(args), capture_output=True, text=True, env=entorno)

    titulo("1 · --ver no toca nada, y lo aprende del jar")
    r = corre("--ver")
    ok(r.returncode == 0, "sale bien (%d)" % r.returncode)
    ok("pack_format ............ 107" in r.stdout,
       "lee el pack_format del `data_major` nuevo, no del campo viejo que ya no existe")
    ok("LISTA de condiciones" in r.stdout,
       "y detecta que la víctima va como lista, copiándolo del molde real")
    ok(not (mc / "world/datapacks/vigilancia").exists(), "no ha escrito nada")

    titulo("2 · pocos logros, y con tope")
    r = corre()
    ok(r.returncode == 0, "se instala (%d): %s" % (r.returncode, r.stderr[-120:]))
    pack = mc / "world/datapacks/vigilancia"
    advs = sorted(p for p in (pack / "data/vigilancia/advancement").glob("*.json")
                  if p.name != "raiz.json")
    ok(len(advs) == 8, "8 logros, uno por especie (%d)" % len(advs))
    ok(len(advs) <= 12, "por debajo del tope, que es la promesa de rendimiento")
    # vanilla ya trae 89 criterios de este disparador: esto es un +9 %
    ok(len(advs) * 100 // 89 <= 10, "es un %d%% sobre lo que ya hace vanilla"
       % (len(advs) * 100 // 89))

    titulo("3 · el pack.mcmeta lleva pack_format")
    meta = json.loads((pack / "pack.mcmeta").read_text())
    ok(meta["pack"].get("pack_format") == 107,
       "con el número bueno (%s) — sin él Minecraft puede no cargarlo y no decir nada"
       % meta["pack"].get("pack_format"))

    titulo("4 · cada logro se revoca a sí mismo")
    # Un logro se consigue UNA VEZ por jugador. Sin revocarlo, el segundo lobo
    # que mate alguien no avisaría jamás. Es el fallo que no se vería hasta el
    # segundo lobo, o sea semanas después.
    uno = json.loads((pack / "data/vigilancia/advancement/cf_wolf.json").read_text())
    fn = (uno.get("rewards") or {}).get("function")
    ok(fn == "vigilancia:rearmar_cf_wolf", "el logro llama a su función: %r" % fn)
    hallado = []
    for carpeta in ("function", "functions"):
        f = pack / "data/vigilancia" / carpeta / "rearmar_cf_wolf.mcfunction"
        if f.exists():
            hallado.append(carpeta)
            ok("advancement revoke @s only vigilancia:cf_wolf" in f.read_text(),
               "y la función lo revoca (%s/)" % carpeta)
    ok(len(hallado) == 2,
       "la función se escribe con los DOS nombres de carpeta: en el jar no hay "
       "ninguna de la que copiar el bueno, así que no se adivina (%s)" % hallado)

    titulo("5 · la víctima se reconoce por etiqueta, no por bicho")
    cond = uno["criteria"]["matar"]["conditions"]
    ok(isinstance(cond.get("entity"), list),
       "se ha copiado la forma del molde de verdad (lista de condiciones)")
    pred = cond["entity"][0]["predicate"]
    ok(pred == {"minecraft:entity_tags": {"any_of": ["cf_wolf"]}},
       "y el predicado es el `entity_tags` que registra el jar: %s" % json.dumps(pred))
    ok("minecraft:ghast" not in json.dumps(cond),
       "sin rastro del predicado del molde, que era de otro bicho")

    # ── lo que tumbó el servidor el 18/09/2026 ──────────────────────────
    # Tres cosas, y las tres se escribieron de memoria en vez de mirarlas.
    ok("nbt" not in json.dumps(cond),
       "NO se usa `nbt`: en esta versión no es un sub-predicado y el arranque "
       "moría con «No key type in MapLike[{\"nbt\":…}]»")
    ok(list(cond.keys()) == ["entity"],
       "y del molde se copia la FORMA, no sus condiciones: nada de heredar el "
       "`killing_blow` de un logro que iba de matar con carga de viento (%s)"
       % list(cond.keys()))
    ok("breeze_wind_charge" not in json.dumps(cond),
       "en concreto, el arma del molde trampa no se cuela")
    raiz = json.loads((pack / "data/vigilancia/advancement/raiz.json").read_text())
    ok(raiz["display"].get("background"),
       "la raíz lleva `background`: sin él esta versión NO ARRANCA («Visible "
       "advancement roots must have background»)")
    ok(uno.get("parent") == "vigilancia:raiz",
       "y los ocho cuelgan de ella, así que el menú de logros gana UNA pestaña "
       "y no ocho")
    ok(all("background" in json.loads(a.read_text()).get("display", {})
           or json.loads(a.read_text()).get("parent")
           for a in (pack / "data/vigilancia/advancement").glob("*.json")),
       "ningún logro se queda siendo raíz visible sin fondo")
    titulos = {json.loads((pack / "data/vigilancia/advancement" / a.name).read_text())
               ["display"]["title"] for a in advs}
    ok("☠ Lobo" in titulos, "el título sale en español, del jar vía mensajes.json")
    ok(all(x.startswith("☠") for x in titulos),
       "y todos llevan la marca por la que el panel los distingue de un logro normal")

    titulo("6 · el panel sabe qué etiqueta le toca a cada uno")
    lista = json.loads((panel / "data/vigilados.json").read_text())
    ok(lista["etiquetas"].get("wolf") == "cf_wolf", "lobo → cf_wolf")
    ok(lista["etiquetas"].get("_otro") == "cf_otro",
       "y hay un cajón de sastre para ranas, sniffers y demás bichos con nombre")
    ok(lista["titulos"].get("☠ Lobo") == "wolf",
       "y del título del logro se puede volver a la especie")
    # cada grupo con SU etiqueta: si compartieran una, un lobo dispararía dos
    # logros a la vez (el suyo y el cajón de sastre)
    ok(len(set(lista["etiquetas"].values())) == len(lista["etiquetas"]),
       "ninguna etiqueta se repite entre grupos")

    titulo("7 · el censo dice quién no lleva su etiqueta todavía")
    sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("ms", REPO / "scripts" / "mascotas.py")
    ms = importlib.util.module_from_spec(spec)
    os.environ.update(MC_DIR=str(mc), PANEL_DIR=str(panel))
    spec.loader.exec_module(ms)
    ms.ESPERA = 0
    pm = importlib.util.spec_from_file_location("pm", REPO / "scripts" / "probar-mascotas.py")
    pmod = importlib.util.module_from_spec(pm)
    sys.argv = ["x"]
    pm.loader.exec_module(pmod)

    ow = mc / "world/dimensions/minecraft/overworld/entities"
    D = pmod.uuid_ints(77)
    pmod.escribir_region(ow / "r.0.0.mca", [
        pmod.bicho(1, "wolf", nombre="Fido", dueño=D),
        pmod.bicho(2, "cat", dueño=D),
        pmod.bicho(3, "frog", nombre="Bizcocho"),
    ])
    r = ms.pasada()
    faltan = {f["uuid"]: f["etiqueta"] for f in r["sin_etiqueta"]}
    ok(len(faltan) == 3, "los tres están sin etiquetar (%d)" % len(faltan))
    ok(faltan.get(pmod.uuid_txt(pmod.uuid_ints(1))) == "cf_wolf", "al lobo le toca cf_wolf")
    ok(faltan.get(pmod.uuid_txt(pmod.uuid_ints(3))) == "cf_otro",
       "y a la rana con nombre, el cajón de sastre")

    titulo("8 · y deja de pedirla en cuanto la lleva")
    # Esto es lo que hace que no haya mantenimiento: el censo lee el NBT, así
    # que ve solo quién falta. Nadie tiene que acordarse de nada.
    import nbt as _n
    pmod.escribir_region(ow / "r.0.0.mca", [
        dict_a_lista := pmod.bicho(1, "wolf", nombre="Fido", dueño=D)
        + [[b"Tags", _n.Tag(_n.TAG_LIST, _n.NList(_n.TAG_STRING, [b"cf_wolf"]))]],
        pmod.bicho(2, "cat", dueño=D),
        pmod.bicho(3, "frog", nombre="Bizcocho"),
    ])
    r = ms.pasada()
    faltan = {f["uuid"] for f in r["sin_etiqueta"]}
    ok(pmod.uuid_txt(pmod.uuid_ints(1)) not in faltan, "el lobo ya no se pide")
    ok(len(faltan) == 2, "y los otros dos sí (%d)" % len(faltan))

    titulo("9 · sin el datapack puesto, el censo sigue igual")
    (panel / "data/vigilados.json").rename(panel / "data/vigilados.bak")
    r = ms.pasada()
    ok(r["sin_etiqueta"] == [], "no pide etiquetas que nadie va a mirar")
    (panel / "data/vigilados.bak").rename(panel / "data/vigilados.json")

    # ── el panel de verdad ───────────────────────────────────────────────────
    titulo("10 · el aviso del juego llega a la Historia")
    os.environ["PANEL_SECRET"] = "secreto-de-prueba-0123456789"
    sp = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(srv)
    M = srv.mensajes()
    r = srv._interpretar("sofidiaz has made the advancement [☠ Lobo]", M)
    ok(r and r[0] == "mobmuerto", "el logro se lee como muerte de animal, no como trofeo")
    ok(r and r[1] == "sofidiaz", "con quién lo mató")
    ok(r and r[2].get("grupo") == "wolf", "y de qué especie era: %s" % (r and r[2]))
    # el formato viejo, un logro por bicho, se sigue entendiendo
    r = srv._interpretar("sofidiaz has made the advancement [☠ Mace Windu]", M)
    ok(r and r[2].get("victima") == "Mace Windu",
       "y un aviso del datapack VIEJO se sigue entendiendo, por los feeds de antes")

    titulo("11 · cruzar el aviso con el censo: la frase entera")
    ahora = time.time()
    srv._añadir(srv.FEED_F, [{"t": ahora - 240, "k": "mobmuerto", "p": "sofidiaz",
                              "u": OTRO_UUID, "grupo": "wolf"}])
    srv._feed_mascotas([{"n": "Fido", "tipo": "wolf", "dueño": DUEÑO_UUID,
                         "dim": "overworld", "pos": [10, 64, 10], "t": ahora}])
    ev = srv.feed_eventos(50)
    fidos = [e for e in ev if e.get("victima") == "Fido"]
    ok(len(fidos) == 1, "sale UNA línea, no dos (%d)" % len(fidos))
    ok(fidos and fidos[0].get("por") == "sofidiaz",
       "y dice quién fue: %s" % (fidos[0].get("por") if fidos else None))
    ok(fidos and abs(fidos[0]["t"] - (ahora - 240)) < 2,
       "con la hora del momento exacto, no la de cuando el censo se enteró")
    genericos = [e for e in ev if e.get("k") == "mobmuerto" and e.get("grupo")]
    ok(not genericos, "y el aviso genérico deja de salir: lo tapa la línea buena")

    titulo("12 · lo que no se puede cruzar, se deja como está")
    srv._añadir(srv.FEED_F, [{"t": ahora - 100, "k": "mobmuerto", "p": "sofidiaz",
                              "u": OTRO_UUID, "grupo": "cat"}])
    ev = srv.feed_eventos(50)
    sueltos = [e for e in ev if e.get("k") == "mobmuerto" and e.get("grupo") == "cat"]
    ok(len(sueltos) == 1,
       "mientras el censo no confirme cuál era, el aviso sale solo")
    # y una muerte del censo de OTRA especie no se lo queda
    srv._feed_mascotas([{"n": "Kiko", "tipo": "parrot", "dueño": DUEÑO_UUID,
                         "dim": "overworld", "pos": [1, 2, 3], "t": ahora}])
    kiko = [e for e in srv.feed_eventos(50) if e.get("victima") == "Kiko"]
    ok(kiko and not kiko[0].get("por"),
       "un loro no se empareja con el aviso de un gato")

    titulo("13 · un aviso no puede servir para dos muertes")
    srv._feed_mascotas([
        {"n": "Micifuz", "tipo": "cat", "dueño": DUEÑO_UUID, "dim": "overworld",
         "pos": [1, 2, 3], "t": ahora},
        {"n": "Pelusa", "tipo": "cat", "dueño": DUEÑO_UUID, "dim": "overworld",
         "pos": [4, 5, 6], "t": ahora},
    ])
    ev = srv.feed_eventos(50)
    conculpa = [e for e in ev if e.get("victima") in ("Micifuz", "Pelusa") and e.get("por")]
    ok(len(conculpa) == 1,
       "solo uno de los dos gatos se lleva al matador (%d)" % len(conculpa))

    titulo("14 · quitarlo es una orden")
    r = corre("--quitar")
    ok(r.returncode == 0 and not pack.exists(), "el datapack desaparece")
    ok(not (panel / "data/vigilados.json").exists(),
       "y el panel deja de esperar sus avisos")

    titulo("15 · si no puede aprenderlo del jar, NO escribe nada")
    # La lección del 18/09/2026: un datapack a medias no es «casi bueno», es un
    # servidor que no arranca. Y el arranque falla horas o días después de
    # escribirlo, cuando ya nadie lo relaciona. Así que se para aquí.
    otro = Path(tempfile.mkdtemp(prefix="probar-vigilancia-cojo-"))
    (otro / "minecraft/world/datapacks").mkdir(parents=True)
    (otro / "panel/data").mkdir(parents=True)
    jar_falso(otro / "minecraft/versions/26.3/server-26.3.jar", con_etiquetas=False)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "vigilancia.py")],
                       capture_output=True, text=True,
                       env=dict(os.environ, MC_DIR=str(otro / "minecraft"),
                                PANEL_DIR=str(otro / "panel")))
    ok(r.returncode != 0, "jar sin `entity_tags`: se planta (código %d)" % r.returncode)
    ok("entity_tags" in r.stdout, "y dice exactamente qué le falta")
    ok(not (otro / "minecraft/world/datapacks/vigilancia").exists(),
       "y NO deja un datapack a medias en el mundo")

    # y lo mismo si no hay de dónde sacar el fondo de la raíz
    sin_raiz = Path(tempfile.mkdtemp(prefix="probar-vigilancia-sinraiz-"))
    (sin_raiz / "minecraft/world/datapacks").mkdir(parents=True)
    (sin_raiz / "panel/data").mkdir(parents=True)
    jar = sin_raiz / "minecraft/versions/26.3/server-26.3.jar"
    jar_falso(jar)
    sin = zipfile.ZipFile(jar)
    guarda = {n: sin.read(n) for n in sin.namelist() if "story/root.json" not in n}
    sin.close()
    with zipfile.ZipFile(jar, "w") as z:
        for n, d in guarda.items():
            z.writestr(n, d)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "vigilancia.py")],
                       capture_output=True, text=True,
                       env=dict(os.environ, MC_DIR=str(sin_raiz / "minecraft"),
                                PANEL_DIR=str(sin_raiz / "panel")))
    ok(r.returncode != 0, "jar sin raíz con fondo: se planta (código %d)" % r.returncode)
    ok(not (sin_raiz / "minecraft/world/datapacks/vigilancia").exists(),
       "tampoco escribe nada")
    shutil.rmtree(otro, ignore_errors=True)
    shutil.rmtree(sin_raiz, ignore_errors=True)


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
