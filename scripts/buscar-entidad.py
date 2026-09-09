#!/usr/bin/env python3
"""
Busca una entidad con nombre por TODO el mundo, esté cargada o no.

El problema que resuelve: `@e[name="..."]` en la consola solo ve los chunks que
el servidor tiene en memoria en ese momento. Si el bicho está en una zona donde
no hay nadie, el juego dirá «entity not found» aunque esté vivo y coleando. Este
script lee los ficheros del mundo directamente, así que da una respuesta de
verdad.

  python3 ~/panel/scripts/buscar-entidad.py "Mace Windu"
  python3 ~/panel/scripts/buscar-entidad.py "Mace" --parcial
  python3 ~/panel/scripts/buscar-entidad.py --listar-nombres
  python3 ~/panel/scripts/buscar-entidad.py --caballos          <- por SEÑAS
  python3 ~/panel/scripts/buscar-entidad.py --tipo villager

Si a un bicho le cambiaron el nombre, buscarlo por nombre no sirve de nada.
`--caballos` saca TODOS los caballos del mundo con su color, si llevan armadura
y de qué, si tienen silla, si están domados y de quién son. Un caballo negro con
armadura de diamante y silla se reconoce a la primera en esa lista.

Si no lo encuentra, enseña TODOS los nombres que sí hay (los apellidos y las
mayúsculas se escriben mal más a menudo de lo que uno cree) y quién ha matado
mobs de ese tipo, que es la única pista que deja Minecraft.
"""
import glob, gzip, json, os, re, struct, sys, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nbt

HOME  = Path.home()
MC    = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
WORLD = MC / "world"

# Mismo reparto de dimensiones que scan-structures.py: 26.2 lo guarda todo bajo
# dimensions/minecraft/<dim>/, y se prueba el layout viejo como respaldo.
_NEW = {"overworld": "dimensions/minecraft/overworld",
        "nether":    "dimensions/minecraft/the_nether",
        "end":       "dimensions/minecraft/the_end"}
_OLD = {"overworld": ".", "nether": "DIM-1", "end": "DIM1"}


def carpetas(dim, cual):
    """cual = 'entities' (1.17+) o 'region' (mundos viejos y bloques)."""
    for base in (_NEW[dim], _OLD[dim]):
        p = WORLD / base / cual
        if p.is_dir():
            return p
    return None


# ------------------------------------------------------- leer una región .mca
# Copiado a propósito de scan-structures.py: este script tiene que poder correr
# solo, sin importar nada que no sea nbt.py.
def leer_region(path):
    out = []
    try:
        data = path.read_bytes()
    except Exception:
        return out
    if len(data) < 8192:
        return out
    for i in range(1024):
        off = struct.unpack(">I", data[i*4:i*4+4])[0] >> 8
        cnt = data[i*4+3]
        if off == 0 or cnt == 0:
            continue
        p = off * 4096
        if p + 5 > len(data):
            continue
        ln = struct.unpack(">I", data[p:p+4])[0]
        comp = data[p+4]
        raw = data[p+5:p+4+ln]
        try:
            if comp == 1:   raw = gzip.decompress(raw)
            elif comp == 2: raw = zlib.decompress(raw)
            elif comp != 3: continue
            _n, root = nbt.parse(raw)
        except Exception:
            continue
        out.append(root)
    return out


# ------------------------------------------------------------------ el nombre
def _texto(v):
    """CustomName viene como cadena JSON en las versiones viejas y como
    componente de texto (compuesto) en las nuevas. Se aceptan las dos."""
    if isinstance(v, bytes):
        s = v.decode("utf-8", "replace")
        if s.startswith("{") or s.startswith("["):
            try:
                d = json.loads(s)
                return _de_json(d)
            except Exception:
                pass
        return s.strip('"')
    if isinstance(v, list):                      # compuesto: [[nombre, Tag],…]
        t = nbt.cget(v, "text")
        if t is not None:
            return _texto(t.v)
        partes = []
        extra = nbt.cget(v, "extra")
        if extra is not None and getattr(extra.v, "items", None):
            for it in extra.v.items:
                partes.append(_texto(it))
        return "".join(partes)
    return str(v)


def _de_json(d):
    if isinstance(d, str):
        return d
    if isinstance(d, list):
        return "".join(_de_json(x) for x in d)
    if isinstance(d, dict):
        s = d.get("text", "")
        for x in d.get("extra", []) or []:
            s += _de_json(x)
        return s
    return ""


def nombre_de(ent):
    cn = nbt.cget(ent, "CustomName")
    if cn is None:
        return None
    n = _texto(cn.v).strip()
    return n or None


def id_de(ent):
    t = nbt.cget(ent, "id")
    if t is None:
        return "?"
    return (t.v.decode() if isinstance(t.v, bytes) else str(t.v)).replace("minecraft:", "")


def pos_de(ent):
    t = nbt.cget(ent, "Pos")
    try:
        x, y, z = [float(v) for v in t.v.items[:3]]
        return int(x), int(y), int(z)
    except Exception:
        return None


def salud_de(ent):
    for clave in ("Health", "health"):
        t = nbt.cget(ent, clave)
        if t is not None:
            try:
                return float(t.v)
            except Exception:
                pass
    return None


# --------------------------------------------------- señas de un caballo
# El color va empaquetado en `Variant`: el byte bajo es el pelaje y el
# siguiente las manchas. Es la forma oficial desde 1.13.
PELAJE = {0: "blanco", 1: "crema", 2: "alazán", 3: "marrón",
          4: "NEGRO", 5: "gris", 6: "marrón oscuro"}
MANCHAS = {0: "sin manchas", 1: "manchas blancas", 2: "campo blanco",
           3: "puntos blancos", 4: "puntos negros"}
ARMADURAS = {"leather_horse_armor": "cuero", "iron_horse_armor": "hierro",
             "golden_horse_armor": "oro", "diamond_horse_armor": "DIAMANTE"}


def _id_item(t):
    """Saca el id de un item, mire donde mire."""
    if t is None:
        return None
    v = t.v
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace").replace("minecraft:", "")
    if isinstance(v, list):
        for clave in ("id", "Id"):
            sub = nbt.cget(v, clave)
            if sub is not None:
                return _id_item(sub)
    return None


def equipo_de(ent):
    """(armadura, silla) mirando TODOS los sitios donde han vivido.

    Minecraft ha movido esto de sitio varias veces: `ArmorItem`/`SaddleItem` en
    las versiones viejas, `body_armor_item` desde 1.20.5, y un compuesto
    `equipment` con ranuras con nombre en las nuevas. Se prueban todos, así el
    script no se rompe al actualizar el servidor.
    """
    armadura = silla = None
    for clave in ("ArmorItem", "body_armor_item", "BodyArmorItem"):
        v = _id_item(nbt.cget(ent, clave))
        if v and v not in ("air",):
            armadura = v
    for clave in ("SaddleItem", "Saddle"):
        t = nbt.cget(ent, clave)
        if t is not None:
            v = _id_item(t)
            if v and v != "air":
                silla = v
            elif t.t == nbt.TAG_BYTE and t.v:      # `Saddle` a veces es un 0/1
                silla = "saddle"
    eq = nbt.cget(ent, "equipment")
    if eq is not None and isinstance(eq.v, list):
        for ranura, tag in eq.v:
            r = ranura.decode("utf-8", "replace") if isinstance(ranura, bytes) else str(ranura)
            v = _id_item(tag)
            if not v or v == "air":
                continue
            if r in ("saddle",):
                silla = v
            elif r in ("body", "armor", "chest"):
                armadura = v
    return armadura, silla


def señas_caballo(ent):
    """Texto corto con color, manchas, equipo y si está domado."""
    partes = []
    var = nbt.cget(ent, "Variant")
    if var is not None:
        try:
            n = int(var.v)
            partes.append(PELAJE.get(n & 0xFF, "color %d" % (n & 0xFF)))
            m = (n >> 8) & 0xFF
            if m:
                partes.append(MANCHAS.get(m, "manchas %d" % m))
        except Exception:
            pass
    armadura, silla = equipo_de(ent)
    partes.append("armadura de " + ARMADURAS.get(armadura, armadura) if armadura else "sin armadura")
    partes.append("CON SILLA" if silla else "sin silla")
    t = nbt.cget(ent, "Tame")
    if t is not None:
        partes.append("domado" if t.v else "salvaje")
    return " · ".join(partes)


def dueño_de(ent, nombres):
    t = nbt.cget(ent, "Owner") or nbt.cget(ent, "OwnerUUID")
    if t is None:
        return None
    v = t.v
    if isinstance(v, bytes):
        return nombres.get(v.decode().lower()) or v.decode()[:8]
    if isinstance(v, list) and len(v) == 4:        # UUID como 4 enteros
        u = "".join("%08x" % (x & 0xFFFFFFFF) for x in v)
        guion = "%s-%s-%s-%s-%s" % (u[0:8], u[8:12], u[12:16], u[16:20], u[20:32])
        return nombres.get(guion) or guion[:8]
    return None


def recorrer(ent, prof=0):
    """Devuelve la entidad y todo lo que lleve encima: un caballo puede ir
    dentro de una barca, y entonces la barca es la entidad de arriba."""
    yield ent
    if prof > 4:
        return
    p = nbt.cget(ent, "Passengers")
    if p is not None and getattr(p.v, "items", None):
        for sub in p.v.items:
            yield from recorrer(sub, prof + 1)


# ------------------------------------------------------------------- escaneo
def nombres_jugadores():
    m = {}
    for c in (MC / "usercache.json", MC / "whitelist.json"):
        try:
            for e in json.loads(c.read_text()):
                m[(e.get("uuid") or "").lower()] = e.get("name")
        except Exception:
            pass
    return m


def escanear(tipo=None):
    """Recorre el mundo entero.

    Sin `tipo`: devuelve solo lo que tenga nombre puesto.
    Con `tipo` (p. ej. "horse"): devuelve TODAS las de ese tipo, tengan nombre
    o no — que es lo que hace falta cuando a un bicho le cambiaron el nombre.
    """
    jug = nombres_jugadores()
    hallados = []
    for dim in ("overworld", "nether", "end"):
        for cual in ("entities", "region"):      # region: mundos pre-1.17
            carp = carpetas(dim, cual)
            if not carp:
                continue
            ficheros = sorted(carp.glob("r.*.mca"))
            if not ficheros:
                continue
            print("  mirando %-9s %-8s  %d ficheros" % (dim, cual, len(ficheros)))
            for f in ficheros:
                for root in leer_region(f):
                    lista = nbt.cget(root.v, "Entities")
                    if lista is None:            # los chunks de bloques no traen
                        continue
                    for cruda in (getattr(lista.v, "items", None) or []):
                        for ent in recorrer(cruda):
                            t = id_de(ent)
                            n = nombre_de(ent)
                            if tipo:
                                if tipo not in t:
                                    continue
                            elif not n:
                                continue
                            hallados.append({
                                "dim": dim, "nombre": n, "tipo": t,
                                "pos": pos_de(ent), "vida": salud_de(ent),
                                "señas": señas_caballo(ent) if "horse" in t or "donkey" in t
                                          or "mule" in t or "llama" in t or "camel" in t else "",
                                "dueño": dueño_de(ent, jug), "fichero": f.name,
                            })
    return hallados


def pinta_lista(lista):
    """Lo más llamativo primero: con nombre, luego con armadura, luego silla."""
    def peso(h):
        s = h["señas"] or ""
        return (0 if h["nombre"] else 1,
                0 if "armadura de" in s and "sin armadura" not in s else 1,
                0 if "CON SILLA" in s else 1)
    for h in sorted(lista, key=peso):
        p = h["pos"]
        print("  %-24s %-11s %s" % (h["nombre"] or "(sin nombre)", h["tipo"], h["dim"]))
        if h["señas"]:
            print("      %s" % h["señas"])
        linea = "      x %d  y %d  z %d" % p if p else "      posición ?"
        if h["vida"] is not None:
            linea += "   ·   vida %.1f" % h["vida"]
        if h["dueño"]:
            linea += "   ·   de %s" % h["dueño"]
        print(linea)
        if p:
            print("      /tp @s %d %d %d" % p)
        print()


# ------------------------------- la única pista que deja Minecraft: las stats
def quien_mato(tipo):
    """Minecraft NO apunta la muerte de un mob en ningún log. Lo único que
    queda es el contador de 'mobs matados' de cada jugador."""
    for cand in (MC / "world/players/stats", MC / "world/stats"):
        if cand.is_dir():
            carp = cand
            break
    else:
        return []
    nombres = {}
    for c in (MC / "usercache.json", MC / "whitelist.json"):
        try:
            for e in json.loads(c.read_text()):
                nombres[(e.get("uuid") or "").lower()] = e.get("name")
        except Exception:
            pass
    fuera = []
    for f in sorted(carp.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except Exception:
            continue
        n = ((d.get("stats") or {}).get("minecraft:killed") or {}).get("minecraft:" + tipo)
        if n:
            uuid = f.stem.lower()
            fuera.append((nombres.get(uuid) or uuid[:8], n))
    return sorted(fuera, key=lambda x: -x[1])


def _valor(bandera):
    """--tipo horse   o   --tipo=horse"""
    for i, a in enumerate(sys.argv):
        if a == bandera and i + 1 < len(sys.argv):
            return sys.argv[i + 1]
        if a.startswith(bandera + "="):
            return a.split("=", 1)[1]
    return None


def main():
    tipo = _valor("--tipo")
    if "--caballos" in sys.argv:
        tipo = "horse"
    args = [a for a in sys.argv[1:]
            if not a.startswith("--") and a != tipo]
    parcial = "--parcial" in sys.argv
    solo_listar = "--listar-nombres" in sys.argv
    buscado = args[0] if args else None

    if not buscado and not solo_listar and not tipo:
        print(__doc__)
        return 2

    # ---------------- por TIPO: no hace falta saber el nombre ----------------
    if tipo:
        print("\nBuscando todos los «%s» del mundo (con nombre o sin él)…" % tipo)
        todos = escanear(tipo=tipo)
        print("\n" + "═" * 62)
        print("  %d encontrados" % len(todos))
        print("═" * 62 + "\n")
        if not todos:
            print("  Ninguno. Si buscabas caballos, prueba también con")
            print("  --tipo donkey, --tipo mule o --tipo llama.\n")
            return 1
        pinta_lista(todos)
        print("  Ordenados: primero los que tienen nombre, luego los que llevan")
        print("  armadura, luego los ensillados. Los tuyos deberían salir arriba.\n")
        return 0

    print("\nLeyendo el mundo entero (esto tarda un rato la primera vez)…")
    todos = escanear()
    print("  → %d entidades con nombre en total\n" % len(todos))

    if solo_listar:
        pinta_lista(todos)
        return 0

    objetivo = buscado.strip().lower()
    exactos = [h for h in todos if (h["nombre"] or "").strip().lower() == objetivo]
    parecidos = [h for h in todos
                 if objetivo in (h["nombre"] or "").strip().lower() and h not in exactos]

    if exactos or (parcial and parecidos):
        print("═" * 62)
        print("  ESTÁ VIVO")
        print("═" * 62 + "\n")
        pinta_lista(exactos or parecidos)
        return 0

    print("═" * 62)
    print("  NO EXISTE ninguno con ese nombre")
    print("═" * 62)
    print("  Los mobs con nombre NO desaparecen solos, así que si no está en")
    print("  los ficheros del mundo es que murió O que le cambiaron el nombre.")
    print("  Minecraft no apunta en ningún sitio CÓMO murió un mob.\n")
    print("  ➜ Si sabes cómo era (color, armadura, silla), búscalo por sus")
    print("    señas en vez de por el nombre:")
    print("        python3 ~/panel/scripts/buscar-entidad.py --caballos\n")

    if parecidos:
        print("  Hay nombres parecidos — ¿es alguno de estos?")
        for h in parecidos:
            print("    «%s»  %s en %s %s" % (h["nombre"], h["tipo"], h["dim"], h["pos"] or ""))
        print()

    print("  Nombres que SÍ hay en el mundo (por si se escribió distinto):")
    vistos = sorted({h["nombre"] for h in todos if h["nombre"]}, key=str.lower)
    for n in vistos[:40]:
        print("    · %s" % n)
    if len(vistos) > 40:
        print("    … y %d más (usa --listar-nombres para verlos todos)" % (len(vistos) - 40))

    print("\n  La única pista que queda: quién ha matado caballos.")
    matados = quien_mato("horse")
    if matados:
        for quien, n in matados:
            print("    %-18s %d caballo(s)" % (quien, n))
        print("\n  Ojo: eso cuenta TODOS los caballos que ha matado esa persona,")
        print("  no dice cuál. Y si murió de caída, lava, un creeper o ahogado,")
        print("  no cuenta para nadie y no hay forma de saberlo.")
    else:
        print("    Nadie ha matado ningún caballo. Murió por su cuenta:")
        print("    caída, lava, un mob, ahogado… eso no lo apunta nadie.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
