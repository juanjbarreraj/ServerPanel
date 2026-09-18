#!/usr/bin/env python3
"""
Que la muerte de una mascota llegue a la Historia — y que una que sigue viva NO.

POR QUÉ ASÍ
-----------
Minecraft no apunta la muerte de ningún bicho, así que la única señal es que
deje de estar en los ficheros del mundo. Eso convierte «se murió» en «no lo
encuentro», y ahí caben cinco formas de equivocarse: que se haya ido a otro
chunk que aún no se ha guardado, que se haya ido al Nether, que vaya montado en
una barca, que sea un loro posado en un hombro (y entonces no es una entidad del
mundo, sino un trozo del `.dat` del jugador), o que se haya cambiado de mundo.

Cada una de esas cinco tiene aquí su comprobación, porque **anunciar una muerte
que no fue es peor que tardar en anunciar la que sí**: no se puede desmentir en
el feed, y a quien se le ha muerto un lobo no le hace gracia.

Se escriben ficheros de región `.mca` de verdad —cabecera, sectores y chunks
comprimidos— y se lee con el `mascotas.py` real.

Correr:  python3 scripts/probar-mascotas.py
"""
import importlib.util, json, os, struct, sys, tempfile, time, zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import nbt                                                     # noqa: E402

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


# ------------------------------------------------------------ NBT a mano
def S(s):   return nbt.Tag(nbt.TAG_STRING, s.encode())
def D(x):   return nbt.Tag(nbt.TAG_DOUBLE, x)
def F(x):   return nbt.Tag(nbt.TAG_FLOAT, x)
def IA(v):  return nbt.Tag(nbt.TAG_INT_ARRAY, list(v))


def uuid_ints(n):
    """Cuatro enteros deterministas, para poder predecir el UUID en la prueba."""
    return [n, n + 1000, n + 2000, n + 3000]


def uuid_txt(v):
    u = "".join("%08x" % (x & 0xFFFFFFFF) for x in v)
    return "%s-%s-%s-%s-%s" % (u[0:8], u[8:12], u[12:16], u[16:20], u[20:32])


DUEÑO = uuid_ints(77)


def bicho(n, tipo, pos=(10, 64, 10), nombre=None, dueño=None, vida=8.0,
          pasajeros=None):
    it = [[b"id", S("minecraft:" + tipo)],
          [b"UUID", IA(uuid_ints(n))],
          [b"Pos", nbt.Tag(nbt.TAG_LIST,
                           nbt.NList(nbt.TAG_DOUBLE, [float(x) for x in pos]))]]
    if vida is not None:
        it.append([b"Health", F(vida)])
    if nombre:
        it.append([b"CustomName", S(json.dumps({"text": nombre}))])
    if dueño:
        it.append([b"Owner", IA(dueño)])
    if pasajeros:
        it.append([b"Passengers", nbt.Tag(nbt.TAG_LIST,
                                          nbt.NList(nbt.TAG_COMPOUND, pasajeros))])
    return it


def escribir_region(path: Path, entidades, cx=0, cz=0):
    """Un `.mca` de verdad: cabecera de 8 KiB, sectores de 4 KiB y zlib."""
    chunk = [[b"DataVersion", nbt.Tag(nbt.TAG_INT, 4325)],
             [b"Position", IA([cx, cz])],
             [b"Entities", nbt.Tag(nbt.TAG_LIST,
                                   nbt.NList(nbt.TAG_COMPOUND, entidades))]]
    crudo = zlib.compress(nbt.serialize(b"", nbt.Tag(nbt.TAG_COMPOUND, chunk)))
    cuerpo = struct.pack(">I", len(crudo) + 1) + b"\x02" + crudo
    sectores = (len(cuerpo) + 4095) // 4096
    cuerpo += b"\x00" * (sectores * 4096 - len(cuerpo))
    i = (cx & 31) + (cz & 31) * 32
    cab = bytearray(8192)
    cab[i * 4:i * 4 + 4] = struct.pack(">I", (2 << 8) | sectores)
    cab[4096 + i * 4:4096 + i * 4 + 4] = struct.pack(">I", int(time.time()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(cab) + cuerpo)


def jugador_con_loro(path: Path, loro=None):
    it = [[b"Health", F(20.0)]]
    if loro:
        it.append([b"ShoulderEntityLeft", nbt.Tag(nbt.TAG_COMPOUND, loro)])
    path.parent.mkdir(parents=True, exist_ok=True)
    nbt.save(path, b"", nbt.Tag(nbt.TAG_COMPOUND, it), gz=True)


# ---------------------------------------------------------------- escenario
LOBO, GATO, LORO, OVEJA, MANIQUI, FLECHA, ALDEANO = 1, 2, 3, 4, 5, 6, 7


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-mascotas-"))
    mc, panel = base / "minecraft", base / "panel"
    ow = mc / "world/dimensions/minecraft/overworld/entities"
    nether = mc / "world/dimensions/minecraft/the_nether/entities"
    print("escenario en %s" % base)

    (mc / "mundos").mkdir(parents=True, exist_ok=True)
    (mc / "mundos/activo.json").write_text(json.dumps({"slug": "temporada-1"}))
    (panel / "data").mkdir(parents=True, exist_ok=True)
    jugador_con_loro(mc / "world/players/data/11111111-1111-1111-1111-111111111111.dat")

    def poblar(*extra):
        escribir_region(ow / "r.0.0.mca", [
            bicho(LOBO, "wolf", nombre="Fido", dueño=DUEÑO),
            bicho(GATO, "cat", dueño=DUEÑO),
            bicho(OVEJA, "sheep"),                          # ni nombre ni dueño
            bicho(MANIQUI, "armor_stand", nombre="Maniquí de Jey"),
            bicho(FLECHA, "arrow", dueño=DUEÑO, vida=None),  # tiene dueño, no vida
            bicho(ALDEANO, "villager", nombre="Pepe"),
        ] + list(extra))
    poblar()
    escribir_region(nether / "r.0.0.mca", [], cx=0, cz=0)

    os.environ.update(MC_DIR=str(mc), PANEL_DIR=str(panel))
    spec = importlib.util.spec_from_file_location("ms", REPO / "scripts" / "mascotas.py")
    ms = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ms)                  # ← el mascotas.py DE VERDAD
    ms.ESPERA = 0                                # sin esperas en la prueba

    titulo("1 · quién cuenta como mascota y quién no")
    hay = ms.mirar(ms.ficheros_de_region())
    nombres = sorted((f.get("n") or f["tipo"]) for f in hay.values())
    ok(len(hay) == 3, "salen 3 de los 6 bichos del mundo: %s" % nombres)
    ok("Fido" in nombres, "el lobo con nombre y dueño")
    ok("cat" in nombres, "el gato domesticado, aunque no tenga nombre")
    ok("Pepe" in nombres, "el aldeano con nametag")
    ok("sheep" not in nombres, "la oveja del campo NO: ni nombre ni dueño")
    ok(not any(f["tipo"] == "armor_stand" for f in hay.values()),
       "un maniquí no es una mascota, aunque tenga nombre")
    ok(not any(f["tipo"] == "arrow" for f in hay.values()),
       "y una flecha tampoco: tiene dueño, pero no tiene vida")

    titulo("2 · la primera pasada solo apunta; no mata a nadie")
    r = ms.pasada()
    ok(r["primera"], "se sabe que es la primera")
    ok(not r["muertas"], "nadie dado por muerto (%d)" % len(r["muertas"]))
    ok(len(ms.cargar()["bichos"]) == 3, "y quedan los 3 apuntados")

    titulo("3 · el lobo se muere")
    escribir_region(ow / "r.0.0.mca", [
        bicho(GATO, "cat", dueño=DUEÑO),
        bicho(OVEJA, "sheep"),
        bicho(ALDEANO, "villager", nombre="Pepe"),
    ])
    ms.ESPERA = 3600                              # de momento, prudente
    r = ms.pasada()
    ok(not r["muertas"], "a la primera NO se anuncia: puede ser un chunk a medio guardar")
    ok(uuid_txt(uuid_ints(LOBO)) in ms.cargar()["sospechosos"], "queda como sospechoso")
    ms.ESPERA = 0
    r = ms.pasada()
    ok(len(r["muertas"]) == 1, "en la siguiente pasada sí (%d)" % len(r["muertas"]))
    m = (r["muertas"] or [{}])[0]
    ok(m.get("n") == "Fido", "y se sabe quién era: %r" % m.get("n"))
    ok(m.get("tipo") == "wolf", "de qué especie")
    ok(m.get("dueño") == uuid_txt(DUEÑO), "y de quién era")
    ok(m.get("pos") == [10, 64, 10] or tuple(m.get("pos") or ()) == (10, 64, 10),
       "con el último sitio donde se le vio: %s" % (m.get("pos"),))
    ok(not ms.cargar()["sospechosos"], "y deja de estar en sospechosos")
    r = ms.pasada()
    ok(not r["muertas"], "y no se anuncia dos veces")

    titulo("4 · irse al Nether no es morirse")
    escribir_region(ow / "r.0.0.mca", [
        bicho(OVEJA, "sheep"),
        bicho(ALDEANO, "villager", nombre="Pepe"),
    ])
    escribir_region(nether / "r.0.0.mca", [bicho(GATO, "cat", dueño=DUEÑO)])
    r = ms.pasada()
    ok(not r["muertas"], "el gato no ha muerto (%s)"
       % [x.get("tipo") for x in r["muertas"]])
    ok(ms.cargar()["bichos"][uuid_txt(uuid_ints(GATO))]["dim"] == "nether",
       "y el censo sabe que ahora está en el Nether")

    titulo("5 · ir montado en una barca tampoco")
    escribir_region(ow / "r.0.0.mca", [
        bicho(OVEJA, "sheep"),
        bicho(99, "boat", vida=None,
              pasajeros=[bicho(ALDEANO, "villager", nombre="Pepe")]),
    ])
    r = ms.pasada()
    ok(not r["muertas"], "Pepe va de pasajero, no está muerto (%s)"
       % [x.get("n") for x in r["muertas"]])

    titulo("6 · un loro en el hombro no es una entidad del mundo")
    escribir_region(ow / "r.0.0.mca", [bicho(LORO, "parrot", nombre="Kiko", dueño=DUEÑO),
                                       bicho(OVEJA, "sheep")])
    ms.pasada()
    ok(uuid_txt(uuid_ints(LORO)) in ms.cargar()["bichos"], "el loro está fichado")
    # se lo sube al hombro: desaparece del mundo y aparece dentro del .dat
    escribir_region(ow / "r.0.0.mca", [bicho(OVEJA, "sheep")])
    jugador_con_loro(mc / "world/players/data/11111111-1111-1111-1111-111111111111.dat",
                     loro=bicho(LORO, "parrot", nombre="Kiko", dueño=DUEÑO))
    r = ms.pasada()
    ok(not r["muertas"], "subírselo al hombro no lo mata (%s)"
       % [x.get("n") for x in r["muertas"]])

    titulo("7 · y al bajarse puede volver con otro UUID")
    jugador_con_loro(mc / "world/players/data/11111111-1111-1111-1111-111111111111.dat")
    escribir_region(ow / "r.0.0.mca", [
        bicho(555, "parrot", nombre="Kiko", dueño=DUEÑO),     # mismo loro, UUID nuevo
        bicho(OVEJA, "sheep")])
    r = ms.pasada()
    ok(not r["muertas"], "se reconoce que es el mismo Kiko (%s)"
       % [x.get("n") for x in r["muertas"]])
    b = ms.cargar()["bichos"]
    ok(uuid_txt(uuid_ints(555)) in b and uuid_txt(uuid_ints(LORO)) not in b,
       "y el censo se queda con el UUID nuevo")

    titulo("8 · cambiar de mundo no mata a nadie")
    (mc / "mundos/activo.json").write_text(json.dumps({"slug": "temporada-2"}))
    escribir_region(ow / "r.0.0.mca", [])          # mundo nuevo, sin bichos
    r = ms.pasada()
    ok(r["cambio_de_mundo"], "se nota que es otro mundo")
    ok(not r["muertas"], "y NO se entierra a las mascotas del mundo anterior (%d)"
       % len(r["muertas"]))
    ok(ms.cargar()["mundo"] == "temporada-2", "el censo pasa a ser del mundo nuevo")

    titulo("9 · lo caro solo se hace cuando hace falta")
    escribir_region(ow / "r.0.0.mca", [bicho(LOBO, "wolf", nombre="Fido", dueño=DUEÑO)])
    escribir_region(nether / "r.0.0.mca", [bicho(GATO, "cat", dueño=DUEÑO)])
    ms.pasada()                                    # primera del mundo nuevo
    # se toca UN fichero sin que falte nadie: es lo que pasa el 99 % del tiempo
    escribir_region(nether / "r.0.0.mca", [bicho(GATO, "cat", dueño=DUEÑO)])
    r = ms.pasada()
    ok(r["leidos"] < r["total"], "una pasada normal lee solo lo que cambió (%d de %d)"
       % (r["leidos"], r["total"]))
    ok(not r["completo"], "y no repasa el mundo entero")
    escribir_region(ow / "r.0.0.mca", [])          # ahora sí falta alguien
    r = ms.pasada()
    ok(r["completo"], "pero en cuanto falta alguien, se repasa todo antes de decir nada")

    titulo("10 · se le pide al juego que guarde antes de enterrar a nadie")
    pedidos = []
    escribir_region(ow / "r.0.0.mca", [bicho(GATO, "cat", dueño=DUEÑO)])
    ms.pasada()
    escribir_region(ow / "r.0.0.mca", [])
    ms.pasada(guardar_ya=lambda: pedidos.append(1))
    ok(pedidos, "se llamó a `save-all flush` (%d)" % len(pedidos))
    pedidos.clear()
    escribir_region(ow / "r.0.0.mca", [bicho(GATO, "cat", dueño=DUEÑO)])
    ms.pasada(guardar_ya=lambda: pedidos.append(1))
    escribir_region(nether / "r.0.0.mca", [])
    ms.pasada(guardar_ya=lambda: pedidos.append(1))
    ok(not pedidos, "y NO se molesta al juego cuando no falta nadie")

    titulo("11 · los ficheros de BLOQUES no se tocan")
    # Desde 1.17 `region/` son los bloques: cientos de megas que se reescriben
    # cada vez que alguien pone o quita algo. Si el censo los mirara,
    # descomprimiría el mundo entero cada tres minutos para no encontrar nada.
    escribir_region(mc / "world/dimensions/minecraft/overworld/region/r.0.0.mca", [])
    claves = [c for c, _d, _f in ms.ficheros_de_region()]
    ok(not any("/region/" in c for c in claves),
       "con `entities/` presente no se lee `region/`: %s" % claves)

    titulo("12 · un lobo que sigue a su dueño de región en región no cuesta nada")
    # Es el caso normal, no el raro: el bicho «falta» un rato porque el chunk
    # de destino aún no se ha guardado. Si por cada una de esas se repasara el
    # mundo entero y se pidiera `save-all flush`, pasear al perro costaría un
    # repaso completo cada tres minutos.
    ms.ESPERA = 3600
    escribir_region(ow / "r.0.0.mca", [bicho(LOBO, "wolf", nombre="Fido", dueño=DUEÑO)])
    escribir_region(nether / "r.0.0.mca", [])
    ms.pasada()
    pedidos.clear()
    escribir_region(ow / "r.0.0.mca", [])                    # sale de esa región…
    r = ms.pasada(guardar_ya=lambda: pedidos.append(1))
    ok(not r["completo"], "al desaparecer no se repasa el mundo entero de golpe")
    ok(not pedidos, "ni se le pide al juego que guarde")
    ok(not r["muertas"], "ni se anuncia nada")
    escribir_region(nether / "r.0.0.mca",                    # …y aparece en la otra
                    [bicho(LOBO, "wolf", nombre="Fido", dueño=DUEÑO)])
    r = ms.pasada(guardar_ya=lambda: pedidos.append(1))
    ok(not r["completo"] and not pedidos,
       "y cuando reaparece se resuelve leyendo solo lo que cambió")
    ok(uuid_txt(uuid_ints(LOBO)) not in ms.cargar()["sospechosos"],
       "deja de estar en sospechosos")
    ok(ms.cargar()["bichos"][uuid_txt(uuid_ints(LOBO))]["dim"] == "nether",
       "con su sitio nuevo apuntado")


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
