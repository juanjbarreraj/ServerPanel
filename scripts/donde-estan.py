#!/usr/bin/env python3
"""
¿Dónde está cada jugador, y dónde están los portales del Nether?

Existe para contestar una pregunta muy concreta: cuando falta una mascota y
aparece «lejísimos», ¿está lejos de verdad, o es que la referencia estaba mal?

Un portal del Nether divide las coordenadas entre 8. Nether 804, 2607 es
Overworld 6432, 20856 — o sea que dos sitios del Nether separados por 1400
bloques pueden ser dos bases que en el Overworld están a 18.000 bloques. Mirando
solo los números del Nether, «al lado» y «lejísimos» se confunden.

Esto saca, de los ficheros del mundo (sin RCON, sin que nadie esté conectado):

  · dónde está cada jugador y en qué dimensión;
  · su equivalencia en la otra dimensión, dividida o multiplicada por 8;

  python3 ~/panel/scripts/donde-estan.py
"""
import json, os, sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
import nbt

MC = Path(os.environ.get("MC_DIR", Path.home() / "minecraft"))
DIM_CORTA = {"minecraft:overworld": "overworld",
             "minecraft:the_nether": "nether",
             "minecraft:the_end": "end"}


def nombres_jugadores():
    m = {}
    for c in (MC / "usercache.json", MC / "whitelist.json"):
        try:
            for e in json.loads(c.read_text()):
                m[(e.get("uuid") or "").lower()] = e.get("name")
        except Exception:
            pass
    return m


def _texto(tag):
    if tag is None:
        return None
    v = tag.v
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)


def jugadores():
    """[(nombre, dim, (x, y, z))] leído del disco."""
    jug = nombres_jugadores()
    fuera = []
    # el panel mueve los mundos, así que las dos rutas que ha usado
    for sub in ("world/playerdata", "world/players/data"):
        carp = MC / sub
        if not carp.is_dir():
            continue
        for f in sorted(carp.glob("*.dat")):
            try:
                _n, root, _gz = nbt.load(str(f))
            except Exception:
                continue
            pos = nbt.cget(root.v, "Pos")
            if pos is None or not getattr(pos.v, "items", None):
                continue
            try:
                x, y, z = (int(c) for c in pos.v.items[:3])
            except Exception:
                continue
            dim = _texto(nbt.cget(root.v, "Dimension")) or "?"
            fuera.append((jug.get(f.stem.lower(), f.stem[:8]),
                          DIM_CORTA.get(dim, dim), (x, y, z)))
        if fuera:
            break
    return fuera


def equivalencia(dim, p):
    x, y, z = p
    if dim == "nether":
        return "overworld", (x * 8, y, z * 8)
    if dim == "overworld":
        return "nether", (x // 8, y, z // 8)
    return None, None


def main():
    if sys.argv[1:]:
        print("\n✗ Esto no lleva opciones.\n")
        return 2

    js = jugadores()
    print("\n" + "═" * 62)
    print("  DÓNDE ESTÁ CADA UNO  (última posición guardada en el disco)")
    print("═" * 62 + "\n")
    if not js:
        print("  No he encontrado datos de jugadores en %s\n" % MC)
        return 1
    for nombre, dim, p in sorted(js):
        print("  %-18s %-10s x %d  y %d  z %d" % ((nombre, dim) + p))
        otra, q = equivalencia(dim, p)
        if otra:
            print("  %-18s %-10s x %d  y %d  z %d   ← el mismo sitio, al otro lado"
                  % ("", otra, q[0], q[1], q[2]))
        print()

    print("  🔴 Un portal divide entre 8. Dos puntos del Nether separados por")
    print("     1.000 bloques son 8.000 en el Overworld: si una mascota aparece")
    print("     «lejísimos» en el Nether, compárala con ESTA tabla antes de")
    print("     sacar conclusiones.\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
