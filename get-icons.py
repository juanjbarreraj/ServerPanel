#!/usr/bin/env python3
"""get-icons v3 — extrae los assets oficiales 26.2 Y renderiza los íconos 3D
del inventario tal como los dibuja el juego:

- ítems planos: su textura oficial (item/generated), como siempre
- bloques: render isométrico REAL leyendo los modelos oficiales del cliente
  (models/item + models/block, cadenas de parents, elements, uv por cara,
  sombreado de caras del juego)
- cofres / ender chest / shulkers / cabezas / escudo: modelos de entidad
  (geometría del juego + sus texturas oficiales de entity/)
- extrae trims, glint encantado y texturas de estandarte para escudos

Salida: icons/item, icons/block, icons/trims, icons/misc, icons/entity,
        icons/render (los 3D), icons/render/_sheet.png (hoja de control)
"""
import io, json, math, re, urllib.request, zipfile
from pathlib import Path
from PIL import Image

PANEL = Path(__file__).resolve().parent
ICONS = PANEL / "icons"
VER = "26.2"

# ------------------------------------------------------------------ descarga
MAN = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
print(f"buscando Minecraft {VER}…")
m = json.load(urllib.request.urlopen(MAN))
v = next(x for x in m["versions"] if x["id"] == VER)
vj = json.load(urllib.request.urlopen(v["url"]))
print("descargando el cliente oficial (~30 MB, una vez)…")
data = urllib.request.urlopen(vj["downloads"]["client"]["url"]).read()
z = zipfile.ZipFile(io.BytesIO(data))
NAMES = set(z.namelist())

TEXP = "assets/minecraft/textures/"
MODP = "assets/minecraft/models/"
KEEP_TEX = ("item/", "block/", "trims/items/", "trims/color_palettes/",
            "misc/enchanted_glint_item", "entity/chest/", "entity/shulker/", "entity/bed/", "entity/banner",
            "entity/shield", "entity/skeleton/", "entity/zombie/zombie",
            "entity/creeper/creeper", "entity/piglin/piglin",
            "entity/player/wide/steve", "entity/enderdragon/dragon")
n_tex = 0
for name in z.namelist():
    if name.startswith(TEXP) and name.endswith(".png"):
        rel = name[len(TEXP):]
        if any(rel.startswith(k) or rel == k + ".png" for k in KEEP_TEX):
            out = ICONS / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(name))
            n_tex += 1
print(f"{n_tex} texturas extraídas")

MODELS = {}
for name in z.namelist():
    if name.startswith(MODP) and name.endswith(".json"):
        rel = name[len(MODP):-5]          # p.ej. item/stone, block/cube_all
        try:
            MODELS[rel] = json.loads(z.read(name))
        except Exception:
            pass
print(f"{len(MODELS)} modelos cargados")

# definiciones de ítems del sistema moderno (assets/minecraft/items/) — aquí
# es donde los bloques apuntan directo a su modelo de bloque
ITEMS_DEF = {}
ITEMP = "assets/minecraft/items/"
for name in z.namelist():
    if name.startswith(ITEMP) and name.endswith(".json"):
        try:
            ITEMS_DEF[name[len(ITEMP):-5]] = json.loads(z.read(name))
        except Exception:
            pass
print(f"{len(ITEMS_DEF)} definiciones de ítems cargadas")

def pick_model(node, depth=0):
    """Desenreda el árbol de definición moderno hasta un modelo concreto.
       -> ("model", ruta) | ("special", tipo) | None"""
    if not isinstance(node, dict) or depth > 10:
        return None
    t = (node.get("type") or "").replace("minecraft:", "")
    if t == "model":
        return ("model", node.get("model"))
    if t == "special":
        st = ((node.get("model") or {}).get("type") or "").replace("minecraft:", "")
        return ("special", st)
    if t == "composite":
        parts = []
        for mm in (node.get("models") or []):
            r = pick_model(mm, depth + 1)
            if r and r[0] == "model" and r[1]:
                tr = ((mm.get("transformation") or {}).get("translation")) or [0, 0, 0]
                parts.append((r[1], tr))
            elif r and r[0] == "special":
                return r
        if len(parts) == 1:
            return ("model", parts[0][0])
        if parts:
            return ("composite", parts)
        return None
    if t == "condition":
        return pick_model(node.get("on_false") or node.get("on_true"), depth + 1)
    if t == "select":
        if node.get("fallback"):
            r = pick_model(node["fallback"], depth + 1)
            if r:
                return r
        for c in (node.get("cases") or []):
            r = pick_model(c.get("model"), depth + 1)
            if r:
                return r
        return None
    if t == "range_dispatch":
        if node.get("fallback"):
            r = pick_model(node["fallback"], depth + 1)
            if r:
                return r
        for e in (node.get("entries") or []):
            r = pick_model(e.get("model"), depth + 1)
            if r:
                return r
        return None
    for k in ("model", "fallback", "base"):
        if isinstance(node.get(k), dict):
            r = pick_model(node[k], depth + 1)
            if r:
                return r
    return None

# ------------------------------------------------------------------ proyección isométrica del inventario
CANVAS = 64
# proyección del inventario del juego: rotY 45° + rotX 30° ortográfica
# (rombo superior 2:1, aristas verticales comprimidas cos30) — como el GUI vanilla
CX, CY, CYV = 0.70711, 0.35355, 0.86603
SCALE = 2.38
OX, OY = CANVAS / 2, CANVAS / 2 + 2.5   # centrado exacto: cubo 16³ ocupa y∈[1.5,61.9]

def project(x, y, z):
    sx = (x - z) * CX * SCALE + OX
    sy = ((x + z) * CY - y * CYV) * SCALE + OY
    return sx, sy

def _find_coeffs(dest, src):
    """dest: 4 puntos destino; src: 4 puntos fuente -> 8 coeficientes PERSPECTIVE
    (resuelto con eliminación gaussiana pura, sin numpy)."""
    A, B = [], []
    for (X, Y), (x, y) in zip(dest, src):
        A.append([x, y, 1, 0, 0, 0, -X * x, -X * y]); B.append(X)
        A.append([0, 0, 0, x, y, 1, -Y * x, -Y * y]); B.append(Y)
    n = 8
    M = [row[:] + [b] for row, b in zip(A, B)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return [M[i][n] for i in range(n)]

def _shade(img, f):
    if f >= 0.999:
        return img
    r, g, b, a = img.split()
    lut = [min(255, int(i * f)) for i in range(256)]
    return Image.merge("RGBA", (r.point(lut), g.point(lut), b.point(lut), a))

GRASS_TINT = (145, 189, 89)     # tinte de bioma por defecto (0x91BD59), como el GUI del juego

def draw_face(canvas, tex, crop, quad, shade, flip=False, tint=None):
    """crop=(u1,v1,u2,v2) px de tex; quad=4 puntos destino TL,TR,BR,BL en canvas."""
    u1, v1, u2, v2 = [int(round(c)) for c in crop]
    if u2 <= u1 or v2 <= v1:
        return
    face = tex.crop((u1, v1, u2, v2)).convert("RGBA")
    if tint:
        r, g, b, a = face.split()
        face = Image.merge("RGBA", (r.point([i * tint[0] // 255 for i in range(256)]),
                                    g.point([i * tint[1] // 255 for i in range(256)]),
                                    b.point([i * tint[2] // 255 for i in range(256)]), a))
    if flip:
        face = face.transpose(Image.FLIP_LEFT_RIGHT)
    # upscala para que el warp no muerda pixeles
    fw, fh = max(1, face.width) * 8, max(1, face.height) * 8
    face = face.resize((fw, fh), Image.NEAREST)
    src = [(0, 0), (fw, 0), (fw, fh), (0, fh)]
    co = _find_coeffs(src, quad)   # mapea salida->fuente
    if co is None:
        return
    warped = face.transform((CANVAS, CANVAS), Image.PERSPECTIVE, co, resample=Image.NEAREST)
    canvas.alpha_composite(_shade(warped, shade))

SH_UP, SH_S, SH_E = 1.0, 0.80, 0.608

def render_groups(groups):
    """groups: lista de (elements, texture_for, offset_xyz). Auto-encuadra modelos
    que sobresalen del bloque unitario (p.ej. camas = 2 bloques). -> 64x64 RGBA"""
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    faces = []
    for elements, tf, off in groups:
        ox, oy, oz = off
        for el in elements:
            fx, fy, fz = el["from"]; tx, ty, tz = el["to"]
            x1, y1, z1 = fx + ox, fy + oy, fz + oz
            x2, y2, z2 = tx + ox, ty + oy, tz + oz
            fs = el.get("faces", {})
            def uv(face, dflt):
                return fs.get(face, {}).get("uv") or dflt
            if "up" in fs:
                c = [(x1, y2, z1), (x2, y2, z1), (x2, y2, z2), (x1, y2, z2)]
                faces.append(((x1+x2)/2 + (z1+z2)/2 + y2*0.05, fs["up"],
                              uv("up", [fx, fz, tx, tz]), c, SH_UP, False, tf))
            if "south" in fs:
                c = [(x1, y2, z2), (x2, y2, z2), (x2, y1, z2), (x1, y1, z2)]
                faces.append((z2 + (x1+x2)/2*0.9 + (y1+y2)/2*0.03, fs["south"],
                              uv("south", [fx, 16-ty, tx, 16-fy]), c, SH_S, False, tf))
            if "east" in fs:
                c = [(x2, y2, z2), (x2, y2, z1), (x2, y1, z1), (x2, y1, z2)]
                faces.append((x2 + (z1+z2)/2*0.9 + (y1+y2)/2*0.03, fs["east"],
                              uv("east", [fz, 16-ty, tz, 16-fy]), c, SH_E, True, tf))
    if not faces:
        return canvas
    pts = [p for f in faces for p in f[3]]
    mnx, mxx = min(p[0] for p in pts), max(p[0] for p in pts)
    mny, mxy = min(p[1] for p in pts), max(p[1] for p in pts)
    mnz, mxz = min(p[2] for p in pts), max(p[2] for p in pts)
    ext = max(mxx - mnx, mxy - mny, mxz - mnz, 16.0)
    s = 16.0 / ext
    cx, cy, cz = (mnx+mxx)/2, (mny+mxy)/2, (mnz+mxz)/2
    def norm(pt):
        return ((pt[0]-cx)*s + 8, (pt[1]-cy)*s + 8, (pt[2]-cz)*s + 8)
    faces.sort(key=lambda f: f[0])
    for _d, fdef, uvv, corners, shade, flip, tf in faces:
        tex, tw, th = tf(fdef.get("texture", ""))
        if tex is None:
            continue
        crop = [uvv[0] * tw / 16, uvv[1] * th / 16, uvv[2] * tw / 16, uvv[3] * th / 16]
        tint = GRASS_TINT if fdef.get("tintindex") is not None else None
        quad = [project(*norm(c)) for c in corners]
        draw_face(canvas, tex, crop, quad, shade, flip, tint)
    return canvas

def render_elements(elements, texture_for):
    return render_groups([(elements, texture_for, (0, 0, 0))])

# ------------------------------------------------------------------ resolución de modelos
def _norm(ref):
    return ref.replace("minecraft:", "")

def resolve_model(model_id):
    """-> (parent_final, textures_map, elements|None)"""
    textures, elements, cur = {}, None, _norm(model_id)
    final = ""
    for _ in range(9):
        mdl = MODELS.get(cur)
        if mdl is None:
            final = cur
            break
        for k, tv in (mdl.get("textures") or {}).items():
            textures.setdefault(k, tv)
        if elements is None and mdl.get("elements"):
            elements = mdl["elements"]
        p = mdl.get("parent")
        if not p:
            final = cur
            break
        final = _norm(p)
        cur = final
    return final, textures, elements

_texcache = {}
def load_tex(path_rel):
    t = _texcache.get(path_rel)
    if t is None:
        f = ICONS / (path_rel + ".png")
        if not f.exists():
            _texcache[path_rel] = (None, 0, 0)
            return (None, 0, 0)
        im = Image.open(f).convert("RGBA")
        if im.height > im.width:      # texturas animadas (frames apilados): primer frame
            im = im.crop((0, 0, im.width, im.width))
        t = (im, im.width, im.height)
        _texcache[path_rel] = t
    return t

ENTITY_FAILS = []
def load_tex_any(*paths):
    """prueba varias rutas; si todas fallan lo registra para el diagnóstico final."""
    for p in paths:
        t = load_tex(p)
        if t[0] is not None:
            return t
    ENTITY_FAILS.append(" | ".join(paths))
    return (None, 0, 0)

def make_texture_for(textures):
    def texture_for(ref):
        seen = 0
        while isinstance(ref, str) and ref.startswith("#") and seen < 8:
            ref = textures.get(ref[1:], "")
            seen += 1
        if not ref:
            return (None, 0, 0)
        return load_tex(_norm(ref))
    return texture_for

# ------------------------------------------------------------------ modelos de entidad (geometría del juego, texturas oficiales)
def entity_face(tex, crop, quad, shade, flip=False, canvas=None):
    draw_face(canvas, tex, crop, quad, shade, flip)

def render_chest(tex_path):
    t, _w, _h = load_tex_any(tex_path)
    if t is None:
        return None
    cv = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    # base 14x10x14 (uv origen 0,19) — dibujada primero
    b = dict(x1=1, y1=0, z1=1, x2=15, y2=9.6, z2=15)
    qS = [project(b["x1"], b["y2"], b["z2"]), project(b["x2"], b["y2"], b["z2"]),
          project(b["x2"], b["y1"], b["z2"]), project(b["x1"], b["y1"], b["z2"])]
    entity_face(t, (14, 33, 28, 43), qS, SH_S, canvas=cv)
    qE = [project(b["x2"], b["y2"], b["z2"]), project(b["x2"], b["y2"], b["z1"]),
          project(b["x2"], b["y1"], b["z1"]), project(b["x2"], b["y1"], b["z2"])]
    entity_face(t, (28, 33, 42, 43), qE, SH_E, True, canvas=cv)
    # tapa 14x5x14 (uv origen 0,0)
    l = dict(x1=1, y1=9.6, z1=1, x2=15, y2=14, z2=15)
    qU = [project(l["x1"], l["y2"], l["z1"]), project(l["x2"], l["y2"], l["z1"]),
          project(l["x2"], l["y2"], l["z2"]), project(l["x1"], l["y2"], l["z2"])]
    entity_face(t, (14, 0, 28, 14), qU, SH_UP, canvas=cv)
    qS = [project(l["x1"], l["y2"], l["z2"]), project(l["x2"], l["y2"], l["z2"]),
          project(l["x2"], l["y1"], l["z2"]), project(l["x1"], l["y1"], l["z2"])]
    entity_face(t, (14, 14, 28, 19), qS, SH_S, canvas=cv)
    qE = [project(l["x2"], l["y2"], l["z2"]), project(l["x2"], l["y2"], l["z1"]),
          project(l["x2"], l["y1"], l["z1"]), project(l["x2"], l["y1"], l["z2"])]
    entity_face(t, (28, 14, 42, 19), qE, SH_E, True, canvas=cv)
    # cerradura al frente
    k = dict(x1=7, y1=7, x2=9, y2=11, z=15.05)
    qK = [project(k["x1"], k["y2"], k["z"]), project(k["x2"], k["y2"], k["z"]),
          project(k["x2"], k["y1"], k["z"]), project(k["x1"], k["y1"], k["z"])]
    entity_face(t, (1, 1, 3, 5), qK, SH_S, canvas=cv)
    return cv

def render_shulker(color):
    t, _w, _h = load_tex_any(f"entity/shulker/shulker_{color}" if color else "entity/shulker/shulker")
    if t is None:
        return None
    cv = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    base = dict(x1=0.5, y1=0, z1=0.5, x2=15.5, y2=8, z2=15.5)
    qS = [project(base["x1"], base["y2"], base["z2"]), project(base["x2"], base["y2"], base["z2"]),
          project(base["x2"], base["y1"], base["z2"]), project(base["x1"], base["y1"], base["z2"])]
    entity_face(t, (16, 44, 32, 52), qS, SH_S, canvas=cv)
    qE = [project(base["x2"], base["y2"], base["z2"]), project(base["x2"], base["y2"], base["z1"]),
          project(base["x2"], base["y1"], base["z1"]), project(base["x2"], base["y1"], base["z2"])]
    entity_face(t, (32, 44, 48, 52), qE, SH_E, True, canvas=cv)
    lid = dict(x1=0.5, y1=4, z1=0.5, x2=15.5, y2=16, z2=15.5)
    qU = [project(lid["x1"], lid["y2"], lid["z1"]), project(lid["x2"], lid["y2"], lid["z1"]),
          project(lid["x2"], lid["y2"], lid["z2"]), project(lid["x1"], lid["y2"], lid["z2"])]
    entity_face(t, (16, 0, 32, 16), qU, SH_UP, canvas=cv)
    qS = [project(lid["x1"], lid["y2"], lid["z2"]), project(lid["x2"], lid["y2"], lid["z2"]),
          project(lid["x2"], lid["y1"], lid["z2"]), project(lid["x1"], lid["y1"], lid["z2"])]
    entity_face(t, (16, 16, 32, 28), qS, SH_S, canvas=cv)
    qE = [project(lid["x2"], lid["y2"], lid["z2"]), project(lid["x2"], lid["y2"], lid["z1"]),
          project(lid["x2"], lid["y1"], lid["z1"]), project(lid["x2"], lid["y1"], lid["z2"])]
    entity_face(t, (32, 16, 48, 28), qE, SH_E, True, canvas=cv)
    return cv

def render_head(tex_path, uv_scale=1.0, top=(8, 0, 16, 8), front=(8, 8, 16, 16), side=(16, 8, 24, 16)):
    t, _w, _h = load_tex_any(tex_path)
    if t is None:
        return None
    s = uv_scale
    top = tuple(c * s for c in top); front = tuple(c * s for c in front); side = tuple(c * s for c in side)
    cv = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    b = dict(x1=3, y1=1.5, z1=3, x2=13, y2=11.5, z2=13)
    qU = [project(b["x1"], b["y2"], b["z1"]), project(b["x2"], b["y2"], b["z1"]),
          project(b["x2"], b["y2"], b["z2"]), project(b["x1"], b["y2"], b["z2"])]
    entity_face(t, top, qU, SH_UP, canvas=cv)
    qS = [project(b["x1"], b["y2"], b["z2"]), project(b["x2"], b["y2"], b["z2"]),
          project(b["x2"], b["y1"], b["z2"]), project(b["x1"], b["y1"], b["z2"])]
    entity_face(t, front, qS, SH_S, canvas=cv)
    qE = [project(b["x2"], b["y2"], b["z2"]), project(b["x2"], b["y2"], b["z1"]),
          project(b["x2"], b["y1"], b["z1"]), project(b["x2"], b["y1"], b["z2"])]
    entity_face(t, side, qE, SH_E, True, canvas=cv)
    return cv

BANNER_QUAD = [(18, 5), (44, 10), (44, 57), (18, 52)]
BANNER_FRONT = (1, 1, 21, 41)
DYE = {"white": (249,255,254), "orange": (249,128,29), "magenta": (199,78,189),
    "light_blue": (58,179,218), "yellow": (254,216,61), "lime": (128,199,31),
    "pink": (243,139,170), "gray": (71,79,82), "light_gray": (157,157,151),
    "cyan": (22,156,156), "purple": (137,50,184), "blue": (60,68,170),
    "brown": (131,84,50), "green": (94,124,22), "red": (176,46,38), "black": (29,29,33)}

def render_banner(color):
    t, _w, _h = load_tex_any("entity/banner/base", "entity/banner_base", "entity/banner/banner_base")
    if t is None:
        return None
    cv = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw_face(cv, t, BANNER_FRONT, BANNER_QUAD, 1.0, tint=DYE.get(color))
    return cv

SHIELD_QUAD = [(17, 5), (45, 11), (45, 59), (17, 53)]
SHIELD_FRONT = (2, 2, 14, 24)

def render_shield_base():
    t, _w, _h = load_tex_any("entity/shield_base_nopattern", "entity/shield/base_nopattern", "entity/shield/shield_base_nopattern")
    if t is None:
        return None
    cv = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw_face(cv, t, SHIELD_FRONT, SHIELD_QUAD, 1.0)
    return cv

# ------------------------------------------------------------------ construcción
REND = ICONS / "render"
REND.mkdir(parents=True, exist_ok=True)
n3d = flat = ent = skipped = 0

special = 0
all_ids = sorted(set(ITEMS_DEF) | {k[5:] for k in MODELS if k.startswith("item/")})
for iid in all_ids:
    ref = None
    if iid in ITEMS_DEF:
        pk = pick_model(ITEMS_DEF[iid].get("model"))
        if pk and pk[0] == "model" and pk[1]:
            ref = _norm(pk[1])
        elif pk and pk[0] == "composite":
            groups = []
            for ref2, tr in pk[1]:
                _f2, tex2, el2 = resolve_model(_norm(ref2))
                if el2:
                    groups.append((el2, make_texture_for(tex2), tuple(16*v for v in tr)))
            if groups:
                try:
                    img = render_groups(groups)
                    if img.getbbox():
                        img.save(REND / f"{iid}.png"); n3d += 1
                        continue
                except Exception:
                    pass
            skipped += 1
            continue
        elif pk and pk[0] == "special":
            special += 1                   # cofres/cabezas/escudo: pase de entidades
            continue
    if ref is None:
        ref = "item/" + iid if ("item/" + iid) in MODELS else None
    if ref is None:
        skipped += 1
        continue
    final, textures, elements = resolve_model(ref)
    out = REND / f"{iid}.png"
    try:
        if final.endswith(("generated", "handheld", "template_spawn_egg")):
            flat += 1                      # plano: lo sirve la ruta item/ normal
            # …pero si su textura vive con OTRO nombre (p.ej. enchanted_golden_apple
            # usa la textura de golden_apple), deja una copia con el nombre del ítem
            if not (ICONS / "item" / f"{iid}.png").exists():
                tref = textures.get("layer0") or textures.get("layer1")
                seen = 0
                while isinstance(tref, str) and tref.startswith("#") and seen < 6:
                    tref = textures.get(tref[1:]); seen += 1
                if tref:
                    srcf = ICONS / (_norm(tref) + ".png")
                    if srcf.exists():
                        out.write_bytes(srcf.read_bytes())
        elif elements:
            img = render_elements(elements, make_texture_for(textures))
            if img.getbbox():
                img.save(out)
                n3d += 1
            else:
                skipped += 1
        else:
            skipped += 1                   # builtin/entity y similares: pase de entidades o fallback
    except Exception:
        skipped += 1

ENTITY = {"chest": ("entity/chest/normal", render_chest),
          "trapped_chest": ("entity/chest/trapped", render_chest),
          "ender_chest": ("entity/chest/ender", render_chest)}
for cid, (tex, fn) in ENTITY.items():
    img = fn(tex)
    if img:
        img.save(REND / f"{cid}.png"); ent += 1
COLORS = ["white","orange","magenta","light_blue","yellow","lime","pink","gray",
          "light_gray","cyan","purple","blue","brown","green","red","black"]
for c in COLORS:
    img = render_shulker(c)
    if img:
        img.save(REND / f"{c}_shulker_box.png"); ent += 1
    img = render_banner(c)
    if img:
        img.save(REND / f"{c}_banner.png"); ent += 1
img = render_shulker("")
if img:
    img.save(REND / "shulker_box.png"); ent += 1
HEADS = {"skeleton_skull": "entity/skeleton/skeleton",
         "wither_skeleton_skull": "entity/skeleton/wither_skeleton",
         "zombie_head": "entity/zombie/zombie",
         "creeper_head": "entity/creeper/creeper",
         "piglin_head": "entity/piglin/piglin",
         "player_head": "entity/player/wide/steve"}
for hid, tex in HEADS.items():
    img = render_head(tex)
    if img:
        img.save(REND / f"{hid}.png"); ent += 1
img = render_head("entity/enderdragon/dragon", uv_scale=1.0,
                  top=(112, 30, 128, 46), front=(112, 46, 128, 62), side=(128, 46, 144, 62))
if img:
    img.save(REND / "dragon_head.png"); ent += 1
img = render_shield_base()
if img:
    img.save(REND / "shield.png"); ent += 1

print(f"renderizados: {n3d} bloques 3D + {ent} entidades · {flat} planos · {special} especiales · {skipped} sin render (fallback)")
if ENTITY_FAILS:
    print("[!] TEXTURAS DE ENTIDAD NO ENCONTRADAS (pasa esta lista a Claude):")
    for f in ENTITY_FAILS:
        print("    -", f)

# hoja de control
SHEET = ["stone","grass_block","oak_planks","oak_slab","oak_stairs","oak_fence","glass",
         "tnt","crafting_table","furnace","magma_block","dragon_egg","enchanting_table",
         "chest","ender_chest","trapped_chest","purple_shulker_box","red_shulker_box",
         "skeleton_skull","wither_skeleton_skull","zombie_head","creeper_head","player_head",
         "dragon_head","shield","red_bed","cyan_bed","red_banner","white_banner","enchanted_golden_apple"]
cell, cols = 72, 7
rows = (len(SHEET) + cols - 1) // cols
sheet = Image.new("RGBA", (cols * cell, rows * cell), (34, 34, 40, 255))
for i, sid in enumerate(SHEET):
    f = REND / f"{sid}.png"
    if not f.exists():
        continue
    im = Image.open(f).convert("RGBA")
    x, y = (i % cols) * cell + 4, (i // cols) * cell + 4
    sheet.alpha_composite(im, (x, y))
sheet.save(REND / "_sheet.png")
print(f"hoja de control: icons/render/_sheet.png — ábrela en https://tu-panel/icons/_sheet.png")

# limpia el caché de composites de armadura (se regeneran con texturas frescas)
comp = ICONS / "composited"
if comp.is_dir():
    for f in comp.glob("*.png"):
        f.unlink()
