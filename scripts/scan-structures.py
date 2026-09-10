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
# MC_DIR y PANEL_DIR mandan sobre lo de casa: es lo que usa el resto del panel,
# y sin ello esto solo funciona si se corre con el usuario correcto — lo que
# hace imposible probarlo contra una copia del mundo.
MC = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
WORLD = MC / "world"
BLUEMAP = Path(os.environ.get("BLUEMAP_DIR", HOME / "bluemap"))
PANEL = Path(os.environ.get("PANEL_DIR", HOME / "panel"))
ICON_SRC = PANEL / "static/markers"
ICON_DST = BLUEMAP / "web/assets/markers"
OUT_JSON = PANEL / "data/structures.json"
GENS_JSON = PANEL / "data/spawners.json"   # generadores del terreno explorado
MANUAL_JSON = PANEL / "data/markers.json"       # marcadores puestos a mano desde el panel
# Tamaño con el que se VEN los marcadores sobre el mapa. Los ficheros de
# static/markers/ son de 64 px nativos y ahora se copian tal cual, sin reducir:
# BlueMap dibuja el icono a su tamaño natural, así que el tamaño de pantalla lo
# fija el CSS de static/biomas.js (`.mk-hd`). Ver copy_icons() para el porqué.
ICON_PX = 32

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

# La tabla de iconos y nombres vive en estructuras.py, que la comparten este
# programa y el mapa del mundo entero del panel. Estuvo duplicada y era
# cuestión de tiempo que se separaran.
from estructuras import TIPOS as STRUCT_MAP, tipo_de as struct_kind

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

# ------------------------------------------------- generadores de monstruos
#
# Los generadores (las «monster rooms» y los de las minas, fortalezas y
# fortines) NO se pueden calcular de la semilla: son decoración que Minecraft
# coloca DESPUÉS de excavar las cuevas, así que para saber dónde hay uno habría
# que generar el mundo entero bloque a bloque. Chunkbase tampoco los tiene.
#
# Pero en el mundo ya explorado están escritos, como cualquier otro bloque con
# datos. Aquí se leen de `block_entities` del chunk, que es la misma lista que
# ya se abre para las estructuras: sale casi gratis.
_SPAWNER = {"minecraft:mob_spawner", "mob_spawner", "MobSpawner"}


def _texto(tag):
    if tag is None:
        return None
    v = tag.v
    return v.decode("utf-8", "replace") if isinstance(v, bytes) else (v if isinstance(v, str) else None)


def _mob_de(payload):
    """Qué bicho sale de este generador, sin el `minecraft:`.

    El nombre del campo ha cambiado varias veces (EntityId → SpawnData.id →
    SpawnData.entity.id → spawn_data.entity.id), y un mundo que viene de una
    versión vieja puede traer cualquiera de ellos. Se prueban todos.
    """
    for camino in (("SpawnData", "entity", "id"), ("spawn_data", "entity", "id"),
                   ("SpawnData", "id"), ("EntityId",)):
        nodo, cuerpo = None, payload
        try:
            for paso in camino:
                nodo = nbt.cget(cuerpo, paso)
                if nodo is None:
                    break
                cuerpo = nodo.v
            v = _texto(nodo)
            if v:
                return v.split(":")[-1]
        except Exception:
            continue
    # algunos traen la lista de posibles en vez del que toca ahora
    try:
        pot = nbt.cget(payload, "SpawnPotentials") or nbt.cget(payload, "spawn_potentials")
        primero = pot.v.items[0]
        d = nbt.cget(primero, "data")
        ent = nbt.cget(d.v if d is not None else primero, "entity")
        return _texto(nbt.cget(ent.v, "id")).split(":")[-1]
    except Exception:
        return "desconocido"


def spawners_in_chunk(root):
    """[{mob, x, y, z}] de los generadores que hay en este chunk."""
    be = nbt.cget(root.v, "block_entities") or nbt.cget(root.v, "BlockEntities")
    if be is None or not getattr(be.v, "items", None):
        return []
    fuera = []
    for tag in be.v.items:
        try:
            ident = nbt.cget(tag, "id")
            if ident is None:
                continue
            v = ident.v.decode() if isinstance(ident.v, bytes) else str(ident.v)
            if v not in _SPAWNER:
                continue
            x = nbt.cget(tag, "x"); y = nbt.cget(tag, "y"); z = nbt.cget(tag, "z")
            if x is None or z is None:
                continue
            fuera.append({"mob": _mob_de(tag),
                          "x": int(x.v), "y": int(y.v) if y is not None else 40,
                          "z": int(z.v)})
        except Exception:
            continue
    return fuera


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
CACHE_V = 3        # 3: además de estructuras, generadores
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
    all_found, all_gens, nueva = {}, {}, {}
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
            items, gens = [], []
            for cx, cz, root in read_region(f):
                items.extend(structures_in_chunk(root, cx, cz))
                gens.extend(spawners_in_chunk(root))
            # Si el servidor acaba de tocar el fichero puede que lo hayamos leído
            # a medio escribir. Se usa lo leído, pero se guarda con una firma
            # imposible para que la próxima vez se relea sí o sí.
            if ahora_ts - st.st_mtime < FRESCA:
                firma = [0, 0]
            actual[f.name] = {"f": firma, "i": items, "g": gens}
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
        vistos, gens = set(), []
        for datos in actual.values():
            for g in (datos.get("g") or []):
                k = (g["x"], g["y"], g["z"])
                if k in vistos:
                    continue
                vistos.add(k); gens.append(g)
        all_gens[dim] = gens
        nueva[dim] = actual
        print("  %-9s %4d regiones (%d releídas, %d de caché) → %s estructuras, %s generadores"
              % (dim + ":", len(actual), leidas, reusadas,
                 format(len(uniq), ","), format(len(gens), ",")))
    _cache_guardar(nueva)
    return all_found, all_gens

# ---------------------------------------------------------------- marcadores
BEGIN = "# >>> CALIFREE MARKERS (autogenerado por scan-structures.py — no editar)"
END   = "# <<< CALIFREE MARKERS"

def esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')

def poi(mid, label, x, y, z, icon, detalle, max_dist, listed=True):
    a = ICON_PX // 2
    # `classes` está documentado en BlueMap y es lo que nos da un asidero fiable
    # para el CSS: static/biomas.js usa `.mk-hd` para enseñar los iconos de 64 px
    # a 32 y que se vean nítidos en pantallas retina.
    return (f'    {mid}: {{ type: "poi", label: "{esc(label)}", '
            f'position: {{ x: {x}, y: {y}, z: {z} }}, '
            f'icon: "assets/markers/{icon}.png", anchor: {{ x: {a}, y: {a} }}, '
            f'classes: ["mk-hd"], '
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
    """Lo que se pinta encima del mapa 3D.

    SOLO los lugares que pone la gente (base, peligro, granja, tienda).

    Los iconos de estructuras —aldeas, monumentos, ciudades antiguas…— ya NO
    van aquí: se fueron al mapa del mundo entero, donde salen todas y no solo
    las del terreno explorado, y donde se pueden encender y apagar por tipo sin
    recargar nada. Tenerlos en los dos sitios era pedir que un día dijeran
    cosas distintas.

    `structs` se sigue recibiendo porque el escaneo del mundo se sigue
    haciendo: es lo que comprueba que el cálculo por semilla acierta (ver
    probar-estructuras.py).
    """
    out = [BEGIN + "\n", "marker-sets: {\n"]
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
    """Copia los iconos TAL CUAL, sin reducirlos.

    Antes se reducían de 64 a 28 px con LANCZOS y se veían borrosos. Dos motivos
    encadenados:

      1. LANCZOS reduciendo 64→28 (ni siquiera es una división entera) emborrona
         los bordes duros del dibujo antes de que salga del servidor;
      2. en una pantalla retina, esos 28 px se pintan sobre 56 puntos físicos, o
         sea que el navegador AMPLÍA al doble una imagen ya machacada.

    Sirviendo los 64 px nativos y dejando que el CSS los enseñe a 32, la retina
    pinta 64 puntos físicos desde una imagen de 64: uno por uno, sin inventarse
    nada. En una pantalla normal el navegador reduce 64→32, que es una división
    exacta y sale limpia.
    """
    if not ICON_SRC.is_dir():
        print("  (sin iconos en", ICON_SRC, ")"); return
    ICON_DST.mkdir(parents=True, exist_ok=True)
    import shutil
    n = 0
    for p in ICON_SRC.glob("*.png"):
        shutil.copy2(p, ICON_DST / p.name)
        n += 1
    print(f"  {n} iconos a resolución nativa → {ICON_DST}")

def main():
    dry = "--dry" in sys.argv
    # --rapido reutiliza el escaneo anterior (data/structures.json) sin mirar
    # siquiera las regiones. Sirve para retocar el aspecto de los marcadores en
    # segundos, no para enterarse de terreno nuevo.
    rapido = "--rapido" in sys.argv
    # --completo tira la caché por región y relee el mundo entero. Solo hace
    # falta si algo se ve raro; lo normal es el incremental.
    completo = "--completo" in sys.argv
    gens = None
    if rapido and OUT_JSON.exists():
        found = json.loads(OUT_JSON.read_text())
        print("Reutilizando el escaneo guardado (%s estructuras)"
              % format(sum(len(v) for v in found.values()), ","))
    else:
        if rapido:
            print("(no hay escaneo guardado todavía, toca escanear)")
        print("Escaneando el mundo…" + (" (completo, sin caché)" if completo else ""))
        t0 = time.time()
        found, gens = scan(completo=completo)
        print(f"  ({time.time()-t0:.1f}s)")
    try:
        manual = json.loads(MANUAL_JSON.read_text())
    except Exception:
        manual = []
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(found))
    # Los generadores van a su propio fichero: los lee la pestaña Explorar para
    # pintarlos en el mapa. Con --rapido no se recalculan, así que no se pisa
    # lo que hubiera guardado.
    if gens is not None:
        GENS_JSON.write_text(json.dumps(gens))
        print("  %s generadores → %s" % (format(sum(len(v) for v in gens.values()), ","),
                                         GENS_JSON.name))
    if dry:
        print(build_block(found.get("overworld", [])[:3], manual, "overworld"))
        return
    copy_icons()
    for dim in ("overworld", "nether", "end"):
        conf = BLUEMAP / f"config/maps/{dim}.conf"
        if not conf.exists():
            continue
        write_config(conf, build_block(found.get(dim, []), manual, dim))
        # los que se PUBLICAN son los lugares puestos a mano, no las estructuras
        # del escaneo: decir aquí el número de estructuras hacía creer que se
        # estaban poniendo en el mapa 3D, que es justo lo que ya no pasa
        n = sum(1 for m in manual if m.get("dim", "overworld") == dim)
        print(f"  {conf.name}: {n} lugar(es) del server "
              f"({len(found.get(dim, []))} estructuras escaneadas, no van al mapa 3D)")
    # Esto SOLO reescribe las configs de BlueMap. Para que los iconos lleguen de
    # verdad al mapa hace falta el paso --markers, que render-mapa.sh ya da solo
    # todas las noches. Corriendo este script a mano, hay que darlo aquí:
    print("Listo. Para que aparezcan en el mapa (tarda segundos, NO hace falta render):")
    print("  cd ~/bluemap && java -Xmx1536M -jar bluemap-cli.jar --markers")
    print("(el render de cada noche ya lo hace por su cuenta)")

if __name__ == "__main__":
    main()
