#!/usr/bin/env python3
"""
Construye la tabla de nombres de BIOMAS del servidor (español + inglés).

De dónde sale cada cosa (todo oficial, nada inventado):
  - la lista de biomas ..... data/minecraft/worldgen/biome/ del jar del SERVIDOR
  - los textos en inglés ... assets/minecraft/lang/en_us.json del mismo jar
  - los textos en español .. assets oficiales de Mojang (es_mx, y si no es_es)

Salida: panel/data/biomas.json   {"minecraft:plains": {"es": "...", "en": "..."}}
Lo lee server.py en /api/biome, que es lo que rellena el bioma en el popup del mapa.

Uso:  python3 ~/panel/scripts/build-biomes.py
      python3 ~/panel/scripts/build-biomes.py --sin-red   (solo inglés)
"""
import glob, json, re, sys, urllib.request, zipfile
from pathlib import Path

PANEL = Path(__file__).resolve().parent.parent
MC    = Path.home() / "minecraft"
OUT   = PANEL / "data" / "biomas.json"
SIN_RED = "--sin-red" in sys.argv

MANIFIESTO = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
RECURSOS   = "https://resources.download.minecraft.net/%s/%s"
IDIOMAS_ES = ["minecraft/lang/es_mx.json", "minecraft/lang/es_es.json"]


def jar_del_servidor():
    """Desde 1.18 el server.jar es solo un lanzador; el bueno está en versions/."""
    cand = sorted(glob.glob(str(MC / "versions/**/server-*.jar"), recursive=True))
    if not cand:
        cand = [str(MC / "server.jar")]
    for p in cand:
        try:
            z = zipfile.ZipFile(p)
            if any(n.startswith("data/minecraft/worldgen/biome/") for n in z.namelist()):
                return p, z
        except Exception:
            pass
    raise SystemExit("No encontré un jar con los biomas dentro. Busqué en %s" % (MC / "versions"))


def version_de(jar_path):
    m = re.search(r"server-([\d.\w-]+)\.jar$", jar_path)
    return m.group(1) if m else None


def lang_es(version):
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
            d = json.loads(data)
            print("  textos en español: %s (%d entradas)" % (nombre.split("/")[-1], len(d)))
            return d
        print("  (no encontré es_mx ni es_es en los assets)")
    except Exception as e:
        print("  (sin español: %s)" % e)
    return {}


def main():
    jar_path, z = jar_del_servidor()
    version = version_de(jar_path)
    print("jar: %s   (versión %s)" % (Path(jar_path).name, version))

    ids = sorted({n.split("/")[-1][:-5]
                  for n in z.namelist()
                  if n.startswith("data/minecraft/worldgen/biome/") and n.endswith(".json")})
    print("  biomas en el jar: %d" % len(ids))

    try:
        en = json.loads(z.read("assets/minecraft/lang/en_us.json"))
    except Exception as e:
        print("  (sin en_us dentro del jar: %s)" % e)
        en = {}
    es = lang_es(version)

    tabla, sin_es = {}, []
    for bid in ids:
        clave = "biome.minecraft." + bid
        bonito = bid.replace("_", " ").title()
        n_en = en.get(clave, bonito)
        n_es = es.get(clave)
        if not n_es:
            sin_es.append(bid)
            n_es = n_en
        tabla["minecraft:" + bid] = {"es": n_es, "en": n_en}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(tabla, ensure_ascii=False, indent=1))
    print("✔ %s  (%d biomas, %.1f KB)" % (OUT, len(tabla), OUT.stat().st_size / 1024))
    if sin_es:
        print("  ⚠ sin traducción al español (%d): %s" % (len(sin_es), ", ".join(sin_es)))

    comparar_con_logros(tabla)
    listar(tabla)
    return 0


# ------------------------------------------------------- comprobar con logros
def comparar_con_logros(tabla):
    """Los nombres de bioma salen del MISMO fichero de idioma que usan los logros
    (biome.minecraft.<id>), así que tienen que coincidir exactamente. Esto lo
    comprueba de verdad, criterio por criterio, en vez de darlo por hecho."""
    f = PANEL / "data" / "advancements.json"
    if not f.exists():
        print("\n  (no hay data/advancements.json todavía: corre build-advancements.py "
              "para poder comparar los nombres con los de los logros)")
        return
    try:
        cat = json.loads(f.read_text())
    except Exception as e:
        print("\n  (advancements.json ilegible: %s)" % e)
        return

    comprobados, difs = {}, []
    for l in cat.get("logros", []):
        for crit, et in (l.get("labels") or {}).items():
            bid = "minecraft:" + crit.split(":")[-1]
            if bid not in tabla:
                continue
            comprobados[bid] = l["id"]
            for idi in ("es", "en"):
                mio, suyo = tabla[bid].get(idi), et.get(idi)
                if suyo and mio != suyo:
                    difs.append((bid, idi, suyo, mio, l["id"]))

    print("\n  comparación con los logros: %d biomas aparecen en logros "
          "(p. ej. 'Es hora de aventurarse')" % len(comprobados))
    if not difs:
        print("  ✔ todos los nombres coinciden exactamente con los de los logros")
    else:
        print("  ✗ %d nombres NO coinciden:" % len(difs))
        for bid, idi, en_logros, en_mapa, quien in difs:
            print("     %-32s [%s]  logros: %-26s mapa: %-26s (%s)"
                  % (bid, idi, en_logros, en_mapa, quien))


def listar(tabla):
    """Lista completa para revisar a ojo contra el juego."""
    print("\n  nombres generados (%d):" % len(tabla))
    for bid, v in sorted(tabla.items(), key=lambda kv: kv[1]["es"]):
        print("     %-28s %-32s %s" % (bid.split(":")[-1], v["es"], v["en"]))


if __name__ == "__main__":
    sys.exit(main())
