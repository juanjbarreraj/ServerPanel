#!/usr/bin/env python3
"""
Escáner de estructuras reales del mundo → marcadores para BlueMap.

Lee los archivos de región (.mca) del mundo, saca las estructuras que YA
existen (solo lo explorado/generado, nada de predicciones de semilla) y
escribe un bloque `marker-sets` en las configs de mapa de BlueMap.

Uso (en el server):
    python3 ~/panel/scripts/scan-structures.py            # escanea y escribe
    python3 ~/panel/scripts/scan-structures.py --dry      # solo muestra
"""
import json, os, re, struct, sys, zlib, gzip, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import nbt

HOME = Path.home()
WORLD = HOME / "minecraft/world"
BLUEMAP = HOME / "bluemap"
PANEL = HOME / "panel"
ICON_SRC = PANEL / "static/markers"
ICON_DST = BLUEMAP / "web/assets/markers"
OUT_JSON = PANEL / "data/structures.json"
MANUAL_JSON = PANEL / "data/markers.json"       # marcadores puestos a mano desde el panel
ICON_PX = 32                                     # tamaño del icono sobre el mapa

# Minecraft 26.2 guarda TODAS las dimensiones en world/dimensions/minecraft/<dim>/region
# (ya no existen world/region, world/DIM-1 ni world/DIM1). Se prueba el layout nuevo
# primero y el viejo como respaldo, para que el script sirva en cualquier versión.
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

# id del juego -> (icono, nombre bonito)
STRUCT_MAP = {
    "village":          ("village", "Aldea"),
    "trial_chambers":   ("trial_chambers", "Cámara de prueba"),
    "ancient_city":     ("ancient_city", "Ciudad antigua"),
    "stronghold":       ("stronghold", "Fortaleza (portal al End)"),
    "monument":         ("monument", "Monumento oceánico"),
    "mansion":          ("mansion", "Mansión del bosque"),
    "pillager_outpost": ("outpost", "Puesto de saqueadores"),
    "ruined_portal":    ("ruined_portal", "Portal en ruinas"),
    "shipwreck":        ("shipwreck", "Naufragio"),
    "desert_pyramid":   ("desert_temple", "Templo del desierto"),
    "jungle_pyramid":   ("jungle_temple", "Templo de la jungla"),
    "swamp_hut":        ("witch_hut", "Choza de bruja"),
    "igloo":            ("igloo", "Iglú"),
    "buried_treasure":  ("buried_treasure", "Tesoro enterrado"),
    "mineshaft":        ("mineshaft", "Mina abandonada"),
    "trail_ruins":      ("trail_ruins", "Ruinas del sendero"),
    "fortress":         ("nether_fortress", "Fortaleza del Nether"),
    "bastion_remnant":  ("bastion_remnant", "Bastión"),
    "end_city":         ("end_city", "Ciudad del End"),
}

def struct_kind(sid):
    """minecraft:village_plains -> village ; minecraft:ruined_portal_desert -> ruined_portal"""
    s = sid.split(":", 1)[-1]
    if s in STRUCT_MAP:
        return s
    for k in STRUCT_MAP:                      # variantes con sufijo/prefijo
        if s.startswith(k + "_") or s.endswith("_" + k):
            return k
    return None

# ---------------------------------------------------------------- región (.mca)
def read_region(path):
    """Devuelve [(chunk_x, chunk_z, root_tag)] de los chunks presentes."""
    out = []
    try:
        data = path.read_bytes()
    except Exception:
        return out
    if len(data) < 8192:
        return out
    rx, rz = [int(n) for n in re.findall(r"-?\d+", path.stem)[:2]]
    for i in range(1024):
        off, cnt = struct.unpack(">I", data[i*4:i*4+4])[0] >> 8, data[i*4+3]
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
            elif comp != 3: continue          # LZ4/custom: ignorar
            _name, root = nbt.parse(raw)
        except Exception:
            continue
        out.append((rx*32 + (i % 32), rz*32 + (i // 32), root))
    return out

def structures_in_chunk(root, cx, cz):
    """Saca las estructuras cuyo INICIO está en este chunk (evita duplicados)."""
    comp = root.v
    st = nbt.cget(comp, "structures") or nbt.cget(comp, "Structures")
    if st is None:
        return []
    starts = nbt.cget(st.v, "starts") or nbt.cget(st.v, "Starts")
    if starts is None:
        return []
    found = []
    for name_b, tag in starts.v:
        try:
            sid_tag = nbt.cget(tag.v, "id")
            sid = (sid_tag.v.decode() if sid_tag is not None else name_b.decode())
        except Exception:
            continue
        if sid.upper() == "INVALID":
            continue
        kind = struct_kind(sid)
        if not kind:
            continue
        scx = nbt.cget(tag.v, "ChunkX"); scz = nbt.cget(tag.v, "ChunkZ")
        if scx is not None and scz is not None and (scx.v != cx or scz.v != cz):
            continue                            # el inicio vive en otro chunk
        x, y, z = cx*16 + 8, 64, cz*16 + 8       # posición por defecto: centro del chunk
        ch = nbt.cget(tag.v, "Children")
        if ch is not None and getattr(ch.v, "items", None):
            bb = None
            try:
                bb = nbt.cget(ch.v.items[0], "BB")
            except Exception:
                bb = None
            if bb is not None and len(bb.v) >= 6:
                b = bb.v
                x, y, z = (b[0]+b[3])//2, (b[1]+b[4])//2, (b[2]+b[5])//2
        found.append({"kind": kind, "id": sid, "x": int(x), "y": int(y), "z": int(z)})
    return found

def scan():
    all_found = {}
    for dim, rdir in DIMS:
        if not rdir.is_dir():
            continue
        items, files = [], sorted(rdir.glob("r.*.mca"))
        for f in files:
            for cx, cz, root in read_region(f):
                items.extend(structures_in_chunk(root, cx, cz))
        # dedupe por tipo+coordenada redondeada
        seen, uniq = set(), []
        for it in items:
            k = (it["kind"], it["x"] // 16, it["z"] // 16)
            if k in seen:
                continue
            seen.add(k); uniq.append(it)
        all_found[dim] = uniq
        print(f"  {dim}: {len(files)} regiones → {len(uniq)} estructuras")
    return all_found

# ---------------------------------------------------------------- marcadores
BEGIN = "# >>> CALIFREE MARKERS (autogenerado por scan-structures.py — no editar)"
END   = "# <<< CALIFREE MARKERS"

def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')

def poi(mid, label, x, y, z, icon):
    a = ICON_PX // 2
    return (f'    {mid}: {{ type: "poi", label: "{esc(label)}", '
            f'position: {{ x: {x}, y: {y}, z: {z} }}, '
            f'icon: "assets/markers/{icon}.png", anchor: {{ x: {a}, y: {a} }} }}\n')

def build_block(structs, manual, dim):
    out = [BEGIN + "\n", "marker-sets: {\n"]
    out.append('  estructuras: {\n    label: "Estructuras"\n    toggleable: true\n'
               '    default-hidden: false\n    sorting: 0\n    markers: {\n')
    for i, s in enumerate(structs):
        icon, pretty = STRUCT_MAP[s["kind"]]
        out.append(poi(f"e{i}", pretty, s["x"], s["y"], s["z"], icon))
    out.append("    }\n  }\n")
    mine = [m for m in manual if m.get("dim", "overworld") == dim]
    if mine:
        out.append('  lugares: {\n    label: "Lugares del server"\n    toggleable: true\n'
                   '    default-hidden: false\n    sorting: 1\n    markers: {\n')
        for i, m in enumerate(mine):
            out.append(poi(f"m{i}", m.get("name", "Lugar"),
                           int(m["x"]), int(m.get("y", 64)), int(m["z"]),
                           re.sub(r"[^a-z_]", "", m.get("icon", "landmark"))))
        out.append("    }\n  }\n")
    out.append("}\n" + END + "\n")
    return "".join(out)

def write_config(conf_path, block):
    txt = conf_path.read_text() if conf_path.exists() else ""
    if BEGIN in txt and END in txt:
        txt = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "", txt, flags=re.S)
    conf_path.write_text(txt.rstrip() + "\n\n" + block)

def copy_icons():
    if not ICON_SRC.is_dir():
        print("  (sin iconos en", ICON_SRC, ")"); return
    ICON_DST.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        for p in ICON_SRC.glob("*.png"):
            Image.open(p).resize((ICON_PX, ICON_PX), Image.LANCZOS).save(ICON_DST / p.name)
    except Exception:
        import shutil
        for p in ICON_SRC.glob("*.png"):
            shutil.copy(p, ICON_DST / p.name)
    print(f"  iconos → {ICON_DST}")

def main():
    dry = "--dry" in sys.argv
    print("Escaneando el mundo…")
    t0 = time.time()
    found = scan()
    print(f"  ({time.time()-t0:.1f}s)")
    try:
        manual = json.loads(MANUAL_JSON.read_text())
    except Exception:
        manual = []
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(found))
    if dry:
        print(build_block(found.get("overworld", [])[:3], manual, "overworld"))
        return
    copy_icons()
    for dim in ("overworld", "nether", "end"):
        conf = BLUEMAP / f"config/maps/{dim}.conf"
        if not conf.exists():
            continue
        write_config(conf, build_block(found.get(dim, []), manual, dim))
        print(f"  {conf.name}: {len(found.get(dim, []))} marcadores")
    print("Listo. Los marcadores aparecen en el próximo render de BlueMap.")

if __name__ == "__main__":
    main()
