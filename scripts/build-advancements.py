#!/usr/bin/env python3
"""
Construye el catálogo de LOGROS del servidor.

De dónde sale cada cosa (todo oficial, nada inventado):
  - la lista de logros .......... data/minecraft/advancement/ del jar del SERVIDOR
  - los textos en inglés ........ assets/minecraft/lang/en_us.json del mismo jar
  - los textos en español ....... assets oficiales de Mojang (es_mx, y si no es_es)
  - los nombres de mobs, biomas
    y objetos de cada criterio ... los mismos ficheros de idioma

Salida: panel/data/advancements.json  (lo lee server.py; no vuelve a abrir el jar)

Uso:  python3 ~/panel/scripts/build-advancements.py
      python3 ~/panel/scripts/build-advancements.py --sin-red   (solo inglés)
"""
import glob, json, re, sys, urllib.request, zipfile
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent
MC    = Path.home() / "minecraft"
OUT   = PANEL / "data" / "advancements.json"
SIN_RED = "--sin-red" in sys.argv

MANIFIESTO = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RECURSOS   = "https://resources.download.minecraft.net/%s/%s"
IDIOMAS_ES = ["minecraft/lang/es_mx.json", "minecraft/lang/es_es.json"]

# ------------------------------------------------------------------ el jar
def jar_del_servidor():
    """Desde 1.18 el server.jar es solo un lanzador; el bueno está en versions/."""
    cand = sorted(glob.glob(str(MC / "versions/**/server-*.jar"), recursive=True))
    if not cand:
        cand = [str(MC / "server.jar")]
    for p in cand:
        try:
            z = zipfile.ZipFile(p)
            if any(n.startswith("data/minecraft/advancement/") for n in z.namelist()):
                return p, z
        except Exception:
            pass
    raise SystemExit("No encontré un jar con los logros dentro. Busqué en %s" % (MC / "versions"))

def version_de(jar_path):
    m = re.search(r"server-([\d.\w-]+)\.jar$", jar_path)
    return m.group(1) if m else None

# ------------------------------------------------------------------ idiomas
def lang_es(version):
    """Baja el fichero de idioma oficial. Devuelve {} si no hay red."""
    if SIN_RED:
        return {}
    try:
        man = json.load(urllib.request.urlopen(MANIFIESTO, timeout=20))
        v = next((x for x in man["versions"] if x["id"] == version), None)
        if v is None:
            print("  (la versión %s no está en el manifiesto de Mojang)" % version)
            return {}
        vj = json.load(urllib.request.urlopen(v["url"], timeout=20))
        idx = json.load(urllib.request.urlopen(vj["assetIndex"]["url"], timeout=30))
        for nombre in IDIOMAS_ES:
            obj = idx["objects"].get(nombre)
            if not obj:
                continue
            h = obj["hash"]
            data = urllib.request.urlopen(RECURSOS % (h[:2], h), timeout=30).read()
            print("  textos en español: %s (%d entradas)"
                  % (nombre.split("/")[-1], len(json.loads(data))))
            return json.loads(data)
        print("  (no encontré es_mx ni es_es en los assets)")
    except Exception as e:
        print("  (sin español: %s)" % e)
    return {}

def bonito(s):
    return s.split(":")[-1].replace("_", " ").strip().capitalize()

def texto(lang, clave, respaldo):
    v = lang.get(clave)
    return v if isinstance(v, str) and v else respaldo

def traduce_componente(c, lang, respaldo=""):
    """Los títulos vienen como {"translate": "..."} o a veces texto plano."""
    if isinstance(c, str):
        return c
    if isinstance(c, dict):
        if "translate" in c:
            return texto(lang, c["translate"], respaldo or bonito(c["translate"]))
        if "text" in c:
            return c["text"]
    return respaldo

# los criterios se llaman de formas distintas segun el logro:
#   minecraft:blaze (mob) · minecraft:beach (bioma) · apple (objeto) · netherite_armor (inventado)
PREFIJOS = ["entity.minecraft.%s", "biome.minecraft.%s", "item.minecraft.%s", "block.minecraft.%s"]

def etiqueta_criterio(crit, lang):
    base = crit.split(":")[-1]
    for p in PREFIJOS:
        v = lang.get(p % base)
        if isinstance(v, str) and v:
            return v
    return None

# ------------------------------------------------------------------ construir
def main():
    jar_path, z = jar_del_servidor()
    ver = version_de(jar_path) or "?"
    print("jar: %s  (versión %s)" % (Path(jar_path).name, ver))

    nombres = sorted(n for n in z.namelist()
                     if n.startswith("data/minecraft/advancement/")
                     and n.endswith(".json") and "/recipes/" not in n)
    print("  logros: %d" % len(nombres))

    en = json.loads(z.read("assets/minecraft/lang/en_us.json"))
    print("  textos en inglés: %d entradas" % len(en))
    es = lang_es(ver)

    logros, sin_es = [], 0
    for n in nombres:
        d = json.loads(z.read(n))
        rel = n[len("data/minecraft/advancement/"):-len(".json")]   # ej: adventure/kill_all_mobs
        disp = d.get("display") or {}
        if not disp:
            continue                       # los "root" invisibles no se muestran
        t_en = traduce_componente(disp.get("title"), en)
        d_en = traduce_componente(disp.get("description"), en)
        t_es = traduce_componente(disp.get("title"), es, t_en) if es else t_en
        d_es = traduce_componente(disp.get("description"), es, d_en) if es else d_en
        if es and t_es == t_en:
            sin_es += 1

        criterios = list((d.get("criteria") or {}).keys())
        # requirements = lista de grupos; cada grupo se cumple con CUALQUIERA de los
        # criterios que contiene, y hay que cumplir TODOS los grupos.
        # Si no viene, es un grupo por criterio (o sea, todos obligatorios).
        req = d.get("requirements") or [[c] for c in criterios]

        etiquetas = {}
        for c in criterios:
            e_en = etiqueta_criterio(c, en) or bonito(c)
            e_es = (etiqueta_criterio(c, es) if es else None) or e_en
            etiquetas[c] = {"es": e_es, "en": e_en}

        logros.append({
            "id": rel,
            "tab": rel.split("/")[0],
            "icon": (disp.get("icon") or {}).get("id", "").split(":")[-1],
            "frame": disp.get("frame", "task"),
            "hidden": bool(disp.get("hidden")),
            "parent": (d.get("parent") or "").split(":")[-1] or None,
            "title": {"es": t_es, "en": t_en},
            "desc":  {"es": d_es, "en": d_en},
            "criteria": criterios,
            "requirements": req,
            "labels": etiquetas,
        })

    pestanas = {}
    for l in logros:
        pestanas[l["tab"]] = pestanas.get(l["tab"], 0) + 1

    salida = {
        "version": ver,
        "idiomas": ["es", "en"] if es else ["en"],
        "total": len(logros),
        "por_pestana": pestanas,
        "logros": logros,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(salida, ensure_ascii=False))
    print("  por pestaña: %s" % ", ".join("%s=%d" % kv for kv in sorted(pestanas.items())))
    if es:
        print("  sin traducir al español: %d de %d" % (sin_es, len(logros)))
    print("  con varios pasos (>1 grupo): %d"
          % sum(1 for l in logros if len(l["requirements"]) > 1))
    iconos = sorted({l["icon"] for l in logros if l["icon"]})
    print("  iconos distintos: %d" % len(iconos))
    print("→ %s  (%.0f KB)" % (OUT, OUT.stat().st_size / 1024))

if __name__ == "__main__":
    main()
