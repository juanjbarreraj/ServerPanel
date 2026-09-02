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
def escanear():
    """[(dim, nombre, id, pos, salud, fichero)] de TODO lo que tenga nombre."""
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
                    items = getattr(lista.v, "items", None) or []
                    for cruda in items:
                        for ent in recorrer(cruda):
                            n = nombre_de(ent)
                            if n:
                                hallados.append((dim, n, id_de(ent), pos_de(ent),
                                                 salud_de(ent), f.name))
    return hallados


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


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    parcial = "--parcial" in sys.argv
    solo_listar = "--listar-nombres" in sys.argv
    buscado = args[0] if args else None

    if not buscado and not solo_listar:
        print(__doc__)
        return 2

    print("\nLeyendo el mundo entero (esto tarda un rato la primera vez)…")
    todos = escanear()
    print("  → %d entidades con nombre en total\n" % len(todos))

    if solo_listar:
        for dim, n, tipo, pos, hp, _f in sorted(todos, key=lambda x: x[1].lower()):
            print("  %-28s %-16s %-9s %s" % (n, tipo, dim, pos))
        return 0

    objetivo = buscado.strip().lower()
    exactos = [h for h in todos if h[1].strip().lower() == objetivo]
    parecidos = [h for h in todos if objetivo in h[1].strip().lower() and h not in exactos]

    if exactos or (parcial and parecidos):
        print("═" * 62)
        print("  ESTÁ VIVO")
        print("═" * 62)
        for dim, n, tipo, pos, hp, f in (exactos or parecidos):
            print("  «%s»  (%s)" % (n, tipo))
            print("     dimensión : %s" % dim)
            print("     posición  : %s" % (("x %d  y %d  z %d" % pos) if pos else "?"))
            if hp is not None:
                print("     vida      : %.1f" % hp)
            print("     fichero   : %s" % f)
            if pos:
                print("     para ir   : /tp @s %d %d %d" % pos)
        return 0

    print("═" * 62)
    print("  NO EXISTE en ninguna parte del mundo")
    print("═" * 62)
    print("  Los mobs con nombre NO desaparecen solos, así que si no está en")
    print("  los ficheros del mundo es que murió. Minecraft no apunta en ningún")
    print("  sitio CÓMO murió un mob: no hay registro que consultar.\n")

    if parecidos:
        print("  Pero hay nombres parecidos — ¿es alguno de estos?")
        for dim, n, tipo, pos, hp, _f in parecidos:
            print("    «%s»  %s en %s %s" % (n, tipo, dim, pos or ""))
        print()

    print("  Nombres que SÍ hay en el mundo (por si se escribió distinto):")
    vistos = sorted({h[1] for h in todos}, key=str.lower)
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
