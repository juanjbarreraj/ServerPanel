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
ICON_PX = 28                                     # tamaño del icono sobre el mapa

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

# id del juego -> (icono, nombre en español, nombre en inglés, distancia máxima, oculto por defecto)
#
# Los nombres en español son los OFICIALES de la wiki en español de Minecraft
# (es.minecraft.wiki), no traducciones propias.
#
# max_dist = a qué distancia de cámara deja de dibujarse el marcador. Es lo que
# evita que al alejar el mapa se junten cientos de iconos: los hitos grandes se
# ven de lejos, lo común solo de cerca. Menos iconos en pantalla = sin lag.
#
# oculto = la capa arranca apagada (las estructuras que salen a cientos).
STRUCT_MAP = {
    #                    icono              español                inglés                  max_dist  oculto  orden
    "ancient_city":     ("ancient_city",    "Ciudad antigua",      "Ancient City",          100000, False, 10),
    "mansion":          ("mansion",         "Mansión del bosque",  "Woodland Mansion",      100000, False, 11),
    "monument":         ("monument",        "Monumento oceánico",  "Ocean Monument",        100000, False, 12),
    "stronghold":       ("stronghold",      "Fortaleza",           "Stronghold",            100000, False, 13),
    "end_city":         ("end_city",        "Ciudad del End",      "End City",              100000, False, 14),
    "bastion_remnant":  ("bastion_remnant", "Bastión en ruinas",   "Bastion Remnant",       100000, False, 15),
    "fortress":         ("nether_fortress", "Fortaleza del Nether","Nether Fortress",       100000, False, 16),
    "trial_chambers":   ("trial_chambers",  "Cámaras de desafío",  "Trial Chambers",         20000, False, 17),
    "village":          ("village",         "Aldea",               "Village",                20000, False, 20),
    "pillager_outpost": ("outpost",         "Puesto de saqueadores","Pillager Outpost",      20000, False, 21),
    "desert_pyramid":   ("desert_temple",   "Pirámide del desierto","Desert Pyramid",         8000, False, 30),
    "jungle_pyramid":   ("jungle_temple",   "Templo de jungla",    "Jungle Temple",           8000, False, 31),
    "swamp_hut":        ("witch_hut",       "Cabaña de pantano",   "Swamp Hut",               8000, False, 32),
    "igloo":            ("igloo",           "Iglú",                "Igloo",                   8000, False, 33),
    "trail_ruins":      ("trail_ruins",     "Sendero en ruinas",   "Trail Ruins",             8000, False, 34),
    "shipwreck":        ("shipwreck",       "Naufragio",           "Shipwreck",               3000, True,  40),
    "buried_treasure":  ("buried_treasure", "Tesoro enterrado",    "Buried Treasure",         3000, True,  41),
    "ruined_portal":    ("ruined_portal",   "Portal en ruinas",    "Ruined Portal",           3000, True,  42),
    "mineshaft":        ("mineshaft",       "Mina abandonada",     "Mineshaft",               3000, True,  43),
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

# ------------------------------------------------------- caché por región
# Leer las ~340 regiones enteras tarda demasiado para hacerlo cada noche, y por
# eso antes solo se escaneaba una vez por semana: el terreno que exploraban los
# jugadores se dibujaba, pero se quedaba SIN iconos de estructuras hasta el
# siguiente escaneo.
#
# La solución: guardar lo encontrado en cada fichero .mca junto con su firma
# (fecha de modificación + tamaño). En la siguiente pasada solo se releen los
# ficheros que han cambiado, que son justo los chunks nuevos. Así el escaneo
# cabe en el trabajo de todas las noches.
CACHE_JSON = PANEL / "data/structures-cache.json"
CACHE_V = 2
FRESCA = 120        # segundos

def _cache_cargar():
    try:
        d = json.loads(CACHE_JSON.read_text())
        if d.get("v") == CACHE_V and isinstance(d.get("dims"), dict):
            return d["dims"]
    except Exception:
        pass
    return {}

def _cache_guardar(dims):
    CACHE_JSON.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_JSON.with_suffix(".tmp")
    tmp.write_text(json.dumps({"v": CACHE_V, "dims": dims}))
    tmp.replace(CACHE_JSON)        # atómico: nunca queda una caché a medias

def scan(completo=False):
    cache = {} if completo else _cache_cargar()
    all_found, nueva = {}, {}
    ahora_ts = time.time()
    for dim, rdir in DIMS:
        if not rdir.is_dir():
            continue
        antes, actual = cache.get(dim, {}), {}
        leidas = reusadas = 0
        for f in sorted(rdir.glob("r.*.mca")):
            try:
                st = f.stat()
            except OSError:
                continue
            firma = [int(st.st_mtime), st.st_size]
            guardado = antes.get(f.name)
            if guardado and guardado.get("f") == firma:
                actual[f.name] = guardado
                reusadas += 1
                continue
            items = []
            for cx, cz, root in read_region(f):
                items.extend(structures_in_chunk(root, cx, cz))
            # Si el servidor acaba de tocar el fichero puede que lo hayamos leído
            # a medio escribir. Se usa lo leído, pero se guarda con una firma
            # imposible para que la próxima vez se relea sí o sí.
            if ahora_ts - st.st_mtime < FRESCA:
                firma = [0, 0]
            actual[f.name] = {"f": firma, "i": items}
            leidas += 1

        # dedupe por tipo+coordenada redondeada
        seen, uniq = set(), []
        for datos in actual.values():
            for it in datos["i"]:
                k = (it["kind"], it["x"] // 16, it["z"] // 16)
                if k in seen:
                    continue
                seen.add(k); uniq.append(it)
        all_found[dim] = uniq
        nueva[dim] = actual
        print("  %-9s %4d regiones (%d releídas, %d de caché) → %s estructuras"
              % (dim + ":", len(actual), leidas, reusadas, format(len(uniq), ",")))
    _cache_guardar(nueva)
    return all_found

# ---------------------------------------------------------------- marcadores
BEGIN = "# >>> CALIFREE MARKERS (autogenerado por scan-structures.py — no editar)"
END   = "# <<< CALIFREE MARKERS"

def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')

def poi(mid, label, x, y, z, icon, detalle, max_dist, listed=True):
    a = ICON_PX // 2
    return (f'    {mid}: {{ type: "poi", label: "{esc(label)}", '
            f'position: {{ x: {x}, y: {y}, z: {z} }}, '
            f'icon: "assets/markers/{icon}.png", anchor: {{ x: {a}, y: {a} }}, '
            f'detail: "{esc(detalle)}", '
            f'min-distance: 0, max-distance: {max_dist}, '
            f'listed: {"true" if listed else "false"} }}\n')

def nb(s):
    """Espacios duros: el globo de BlueMap es estrecho y parte las palabras."""
    return esc(s).replace(" ", "&nbsp;")

# La tarjeta lleva su propio fondo: el globo de BlueMap es estrecho y si el texto
# se sale, sin fondo quedaría flotando sobre el mapa e ilegible. Con fondo propio
# se ve como una tarjeta entera aunque desborde. Los márgenes negativos tapan el
# relleno del globo para que no se note una caja dentro de otra.
_CARD = ('white-space:nowrap;display:inline-block;text-align:left;'
         'font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;'
         'line-height:1.35;color:#e8edf3;background:#181c21;'
         'margin:-6px -10px;padding:9px 13px;border-radius:6px')

def detalle_html(es, en, x, y, z):
    """Lo que sale al hacer clic: nombre, nombre en inglés y coordenadas, una línea cada uno."""
    return (f'<div style="{_CARD}">'
            f'<div style="font-weight:700;font-size:15px">{nb(es)}</div>'
            f'<div style="font-size:12px;opacity:.55;margin-bottom:5px">{nb(en)}</div>'
            f'<div style="font-size:13px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace">'
            f'X:&nbsp;{x}&nbsp;&nbsp;&nbsp;Y:&nbsp;{y}&nbsp;&nbsp;&nbsp;Z:&nbsp;{z}</div>'
            f'</div>')

def build_block(structs, manual, dim):
    """Una capa por tipo de estructura, para poder encender y apagar cada una."""
    out = [BEGIN + "\n", "marker-sets: {\n"]
    por_tipo = {}
    for s in structs:
        por_tipo.setdefault(s["kind"], []).append(s)
    for kind in sorted(por_tipo, key=lambda k: STRUCT_MAP[k][5]):
        icon, es, en, max_dist, oculto, orden = STRUCT_MAP[kind]
        items = por_tipo[kind]
        # con muchísimos marcadores, no llenamos también la lista lateral
        listed = len(items) <= 60
        out.append(f'  est_{kind}: {{\n    label: "{esc(es)} ({len(items)})"\n'
                   f'    toggleable: true\n    default-hidden: {"true" if oculto else "false"}\n'
                   f'    sorting: {orden}\n    markers: {{\n')
        for i, s in enumerate(items):
            out.append(poi(f"e_{kind}_{i}", es, s["x"], s["y"], s["z"], icon,
                           detalle_html(es, en, s["x"], s["y"], s["z"]), max_dist, listed))
        out.append("    }\n  }\n")
    mine = [m for m in manual if m.get("dim", "overworld") == dim]
    if mine:
        out.append('  lugares: {\n    label: "Lugares del server"\n    toggleable: true\n'
                   '    default-hidden: false\n    sorting: 0\n    markers: {\n')
        for i, m in enumerate(mine):
            x, y, z = int(m["x"]), int(m.get("y", 64)), int(m["z"])
            nombre = m.get("name", "Lugar")
            det = (f'<div style="{_CARD}">'
                   f'<div style="font-weight:700;font-size:15px;margin-bottom:5px">{nb(nombre)}</div>'
                   f'<div style="font-size:13px;font-family:ui-monospace,SFMono-Regular,Menlo,monospace">'
                   f'X:&nbsp;{x}&nbsp;&nbsp;&nbsp;Y:&nbsp;{y}&nbsp;&nbsp;&nbsp;Z:&nbsp;{z}</div></div>')
            out.append(poi(f"m{i}", nombre, x, y, z,
                           re.sub(r"[^a-z_]", "", m.get("icon", "landmark")),
                           det, 100000, True))
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
    # --rapido reutiliza el escaneo anterior (data/structures.json) sin mirar
    # siquiera las regiones. Sirve para retocar el aspecto de los marcadores en
    # segundos, no para enterarse de terreno nuevo.
    rapido = "--rapido" in sys.argv
    # --completo tira la caché por región y relee el mundo entero. Solo hace
    # falta si algo se ve raro; lo normal es el incremental.
    completo = "--completo" in sys.argv
    if rapido and OUT_JSON.exists():
        found = json.loads(OUT_JSON.read_text())
        print("Reutilizando el escaneo guardado (%s estructuras)"
              % format(sum(len(v) for v in found.values()), ","))
    else:
        if rapido:
            print("(no hay escaneo guardado todavía, toca escanear)")
        print("Escaneando el mundo…" + (" (completo, sin caché)" if completo else ""))
        t0 = time.time()
        found = scan(completo=completo)
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
    # Esto SOLO reescribe las configs de BlueMap. Para que los iconos lleguen de
    # verdad al mapa hace falta el paso --markers, que render-mapa.sh ya da solo
    # todas las noches. Corriendo este script a mano, hay que darlo aquí:
    print("Listo. Para que aparezcan en el mapa (tarda segundos, NO hace falta render):")
    print("  cd ~/bluemap && java -Xmx1536M -jar bluemap-cli.jar --markers")
    print("(el render de cada noche ya lo hace por su cuenta)")

if __name__ == "__main__":
    main()
