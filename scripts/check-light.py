#!/usr/bin/env python3
"""
Diagnóstico: ¿por qué hay huecos negros en el mapa?

Recorre los archivos de región y cuenta cuántos chunks EXISTEN y cuántos de
esos tienen datos de luz. Así se distingue:
  - hueco por zona nunca visitada  → el chunk ni existe (normal)
  - hueco por falta de luz         → el chunk existe pero sin luz (BlueMap lo omite)

Uso (en el server):  python3 ~/panel/scripts/check-light.py
"""
import gzip, re, struct, sys, zlib
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nbt

WORLD = Path.home() / "minecraft/world"

# MC 26.2: todo vive en world/dimensions/minecraft/<dim>/region.
# Versiones viejas: world/region, world/DIM-1/region, world/DIM1/region.
_NEW = {"overworld": "dimensions/minecraft/overworld",
        "nether":    "dimensions/minecraft/the_nether",
        "end":       "dimensions/minecraft/the_end"}
_OLD = {"overworld": ".", "nether": "DIM-1", "end": "DIM1"}

def region_dir(dim):
    for p in (WORLD / _NEW[dim] / "region", WORLD / _OLD[dim] / "region"):
        if p.is_dir():
            return p
    return WORLD / _NEW[dim] / "region"

DIMS = [(d, region_dir(d)) for d in ("overworld", "nether", "end")]

def chunks_of(path):
    try:
        data = path.read_bytes()
    except Exception:
        return
    if len(data) < 8192:
        return
    nums = [int(n) for n in re.findall(r"-?\d+", path.stem)[:2]]
    if len(nums) < 2:
        return
    rx, rz = nums
    for i in range(1024):
        head = struct.unpack(">I", data[i*4:i*4+4])[0]
        off, cnt = head >> 8, head & 0xFF
        if off == 0 or cnt == 0:
            continue
        p = off * 4096
        if p + 5 > len(data):
            continue
        ln = struct.unpack(">I", data[p:p+4])[0]
        comp, raw = data[p+4], data[p+5:p+4+ln]
        try:
            if comp == 1:   raw = gzip.decompress(raw)
            elif comp == 2: raw = zlib.decompress(raw)
            elif comp != 3: continue
            _n, root = nbt.parse(raw)
        except Exception:
            continue
        yield rx*32 + (i % 32), rz*32 + (i // 32), root

def has_light(root):
    """True si el chunk trae datos de luz (lo que BlueMap necesita)."""
    c = root.v
    flag = nbt.cget(c, "isLightOn") or nbt.cget(c, "IsLightOn")
    if flag is not None:
        return bool(flag.v)
    secs = nbt.cget(c, "sections") or nbt.cget(c, "Sections")
    if secs is not None and getattr(secs.v, "items", None):
        for s in secs.v.items:
            try:
                if nbt.cget(s, "SkyLight") is not None or nbt.cget(s, "BlockLight") is not None:
                    return True
            except Exception:
                pass
    return False

for dim, rdir in DIMS:
    if not rdir.is_dir():
        continue
    files = sorted(rdir.glob("r.*.mca"))
    total = lit = 0
    status = Counter()
    dark_pts = []
    for f in files:
        for cx, cz, root in chunks_of(f):
            total += 1
            st = nbt.cget(root.v, "Status")
            if st is not None:
                try: status[st.v.decode().split(":")[-1]] += 1
                except Exception: pass
            if has_light(root):
                lit += 1
            elif len(dark_pts) < 400000:
                dark_pts.append((cx, cz))
    dark = total - lit
    print(f"\n=== {dim.upper()} ===")
    print(f"  regiones: {len(files)}   chunks generados: {total:,}")
    print(f"  con luz:  {lit:,}   SIN luz (huecos negros): {dark:,}"
          f"  ({(dark/total*100 if total else 0):.1f}%)")
    if status:
        print("  estados:", ", ".join(f"{k}={v:,}" for k, v in status.most_common(5)))
    if dark_pts:
        xs = [p[0] for p in dark_pts]; zs = [p[1] for p in dark_pts]
        print(f"  zona sin luz (bloques): x de {min(xs)*16:,} a {max(xs)*16:,}"
              f" · z de {min(zs)*16:,} a {max(zs)*16:,}")
        # muestra unos cuantos puntos representativos para ir a volar por ahí
        step = max(1, len(dark_pts) // 8)
        muestra = [f"({p[0]*16},{p[1]*16})" for p in dark_pts[::step]][:8]
        print("  ejemplos de coordenadas:", " ".join(muestra))
print("\nSi 'SIN luz' es ~0, los huecos negros son zonas que nadie ha visitado (normal).")
print("Si 'SIN luz' es alto, son chunks importados sin datos de luz.")
