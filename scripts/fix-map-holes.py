#!/usr/bin/env python3
"""
Encuentra los tiles que BlueMap NO renderizo (manchas negras) y marca solo las
regiones afectadas para que el proximo render las rehaga.

  python3 ~/fix-map-holes.py           -> solo reporta
  python3 ~/fix-map-holes.py --fix     -> marca las regiones (touch + borra su rstate)
"""
import gzip, json, os, re, struct, sys, time, zlib
from pathlib import Path

HOME = Path.home()
sys.path.insert(0, str(HOME / "panel"))
import nbt

WORLD = HOME / "minecraft/world"
WEB   = Path("/var/www/bluemap-web/maps")
DIMS  = {"overworld": "dimensions/minecraft/overworld",
         "nether":    "dimensions/minecraft/the_nether",
         "end":       "dimensions/minecraft/the_end"}
FIX = "--fix" in sys.argv

def existing_tiles(mapdir):
    """Reconstruye (tx,tz) desde rutas tipo tiles/0/x-3/1/z2/4.prbm.gz"""
    base = mapdir / "tiles/0"
    out = set()
    if not base.is_dir():
        return out
    for p in base.rglob("*.prbm*"):
        parts = list(p.relative_to(base).parts)
        parts[-1] = parts[-1].split(".")[0]
        xs, zs, cur = [], [], None
        for c in parts:
            if c.startswith("x"):   cur, c = xs, c[1:]
            elif c.startswith("z"): cur, c = zs, c[1:]
            if cur is None: continue
            cur.append(c)
        try:
            out.add((int("".join(xs)), int("".join(zs))))
        except Exception:
            pass
    return out

def chunks_of(path):
    try:
        d = path.read_bytes()
    except Exception:
        return
    if len(d) < 8192:
        return
    n = [int(v) for v in re.findall(r"-?\d+", path.stem)[:2]]
    if len(n) < 2:
        return
    rx, rz = n
    for i in range(1024):
        h = struct.unpack(">I", d[i*4:i*4+4])[0]
        off, cnt = h >> 8, h & 0xFF
        if off == 0 or cnt == 0:
            continue
        p = off * 4096
        if p + 5 > len(d):
            continue
        ln = struct.unpack(">I", d[p:p+4])[0]
        cm, raw = d[p+4], d[p+5:p+4+ln]
        try:
            if cm == 1:   raw = gzip.decompress(raw)
            elif cm == 2: raw = zlib.decompress(raw)
            elif cm != 3: continue
            _x, root = nbt.parse(raw)
        except Exception:
            continue
        yield rx*32 + (i % 32), rz*32 + (i // 32), root

def is_full_lit(root):
    st = nbt.cget(root.v, "Status")
    try:
        if st is None or st.v.decode().split(":")[-1] != "full":
            return False
    except Exception:
        return False
    fl = nbt.cget(root.v, "isLightOn") or nbt.cget(root.v, "IsLightOn")
    return bool(fl.v) if fl is not None else False

def rstate_paths(mapdir, rx, rz):
    """rstate usa el mismo troceo por digitos: 12 -> x1/2"""
    def split(v):
        s = str(v)
        neg = s.startswith("-")
        if neg: s = s[1:]
        parts = list(s)
        if neg: parts[0] = "-" + parts[0]
        return parts
    xs, zs = split(rx), split(rz)
    d = mapdir / "rstate"
    for i, c in enumerate(xs):
        d = d / (("x" + c) if i == 0 else c)
    stem = "".join(zs)
    zp = split(rz)
    dd = d
    for i, c in enumerate(zp[:-1]):
        dd = dd / (("z" + c) if i == 0 else c)
    last = zp[-1] if len(zp) > 1 else ("z" + zp[-1])
    return [dd / (last + ".tiles.dat"), dd / (last + ".chunks.dat")]

total_missing, total_regions, touched = 0, 0, 0
for dim, sub in DIMS.items():
    rdir = WORLD / sub / "region"
    mapdir = WEB / dim
    sfile = mapdir / "settings.json"
    if not rdir.is_dir() or not sfile.exists():
        print("  (salto %s)" % dim); continue
    cfg = json.loads(sfile.read_text())
    tsz = cfg["hires"]["tileSize"]; tr = cfg["hires"].get("translate", [0, 0])
    have = existing_tiles(mapdir)
    print("\n=== %s ===" % dim.upper())
    print("  tile = %dx%d bloques, offset %s · tiles en disco: %s"
          % (tsz[0], tsz[1], tr, format(len(have), ",")))
    bad_regions, missing, sample = [], 0, []
    for f in sorted(rdir.glob("r.*.mca")):
        want = set()
        for cx, cz, root in chunks_of(f):
            if not is_full_lit(root):
                continue
            x0, z0 = cx*16, cz*16
            for tx in range(int((x0-tr[0])//tsz[0]), int((x0+15-tr[0])//tsz[0])+1):
                for tz in range(int((z0-tr[1])//tsz[1]), int((z0+15-tr[1])//tsz[1])+1):
                    want.add((tx, tz))
        gone = want - have
        if gone:
            bad_regions.append(f); missing += len(gone)
            if len(sample) < 6:
                tx, tz = sorted(gone)[0]
                sample.append((tx*tsz[0]+tr[0], tz*tsz[1]+tr[1]))
    print("  tiles que DEBERIAN existir y no estan: %s" % format(missing, ","))
    print("  regiones afectadas: %d de %d" % (len(bad_regions), len(list(rdir.glob('r.*.mca')))))
    if sample:
        print("  ejemplos (bloques):", " ".join("(%d,%d)" % s for s in sample))
    total_missing += missing; total_regions += len(bad_regions)
    if FIX and bad_regions:
        now = time.time()
        for f in bad_regions:
            os.utime(f, (now, now))
            n = [int(v) for v in re.findall(r"-?\d+", f.stem)[:2]]
            for p in rstate_paths(mapdir, n[0], n[1]):
                try:
                    if p.exists(): p.unlink()
                except Exception:
                    pass
            touched += 1
        print("  -> %d regiones marcadas para re-render" % len(bad_regions))

print("\nTOTAL: %s tiles faltantes en %d regiones." % (format(total_missing, ","), total_regions))
if FIX:
    print("Marcadas %d regiones. Ahora lanza el render:" % touched)
    print("  cd ~/bluemap && screen -dmS bmfix bash -c 'nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar -r >> render.log 2>&1'")
else:
    print("Nada modificado. Para marcarlas:  python3 ~/fix-map-holes.py --fix")
