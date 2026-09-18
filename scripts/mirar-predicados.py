#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Le pregunta AL JAR cómo se escribe un predicado de entidad en esta versión.

Por qué existe
──────────────
El 18/09/2026 el datapack de vigilancia tumbó el servidor al arrancar. El log
lo dijo exacto:

    No key type in MapLike[{"nbt":"{Tags:[\\"cf_mule\\"]}"}]
    ...; Visible advancement roots must have background

Dos fallos, los dos por escribir de memoria en vez de mirar:

  1. En esta versión un predicado de entidad ya NO es `{"nbt": "..."}`. Se ve
     en el propio molde que copié, que venía del jar y sí cargaba:

         "direct_entity": {"minecraft:entity_type": "minecraft:breeze_wind_charge"}

     O sea: un MAPA de `<id de sub-predicado>: <valor>`. `nbt` a secas no es
     uno de esos ids, así que el códec intentó despacharlo por `type` y no lo
     encontró.
  2. Un logro con `display` y sin `parent` es una RAÍZ, y una raíz visible
     necesita `background`.

Este guion no arregla nada: saca del jar los datos con los que se arregla, para
no volver a adivinar. Lo que imprime se pega en el chat tal cual.

    python3 scripts/mirar-predicados.py
"""
import glob, json, os, re, struct, sys, zipfile
from pathlib import Path

HOME = Path.home()
MC   = Path(os.environ.get("MC_DIR", HOME / "minecraft"))


def jar_del_servidor():
    # 🔴 Por FECHA, no por nombre: ordenando texto «26.2» va antes que «26.3».
    # Ver claude/el-jar-por-nombre.md.
    cands = glob.glob(str(MC / "versions" / "*" / "server-*.jar"))
    cands += glob.glob(str(MC / "server.jar"))
    cands = [c for c in cands if os.path.isfile(c)]
    if not cands:
        print("✗ no encuentro el jar del servidor en %s" % MC)
        sys.exit(1)
    return max(cands, key=os.path.getmtime)


# ─────────────────────────────────────────── las cadenas de un fichero .class
def cadenas_de_clase(datos):
    """Los UTF8 del pool de constantes, en orden. Sin librerías."""
    if datos[:4] != b"\xca\xfe\xba\xbe":
        return []
    n = struct.unpack_from(">H", datos, 8)[0]
    i, fuera, k = 10, [], 1
    while k < n:
        etiqueta = datos[i]; i += 1
        if etiqueta == 1:                                    # UTF8
            largo = struct.unpack_from(">H", datos, i)[0]; i += 2
            fuera.append(datos[i:i + largo].decode("utf-8", "replace")); i += largo
        elif etiqueta in (7, 8, 16, 19, 20):  i += 2
        elif etiqueta == 15:                  i += 3
        elif etiqueta in (5, 6):              i += 8; k += 1  # long/double: 2 huecos
        else:                                 i += 4
        k += 1
    return fuera


def main():
    ruta = jar_del_servidor()
    print("jar: %s\n" % Path(ruta).name)
    z = zipfile.ZipFile(ruta)
    nombres = z.namelist()

    # ── 1 · el background de una raíz de verdad ──────────────────────────
    print("═" * 64)
    print("1 · BACKGROUND de las raíces de vanilla")
    print("═" * 64)
    raices = [n for n in nombres
              if re.search(r"/advancements?/[a-z_]+/root\.json$", n)]
    for n in sorted(raices)[:6]:
        try:
            d = json.loads(z.read(n)).get("display") or {}
        except Exception as e:
            print("  %s: no se pudo leer (%s)" % (n, e)); continue
        print("  %-52s %s" % (n.split("/advancement")[-1], d.get("background")))
    if not raices:
        print("  (ninguna; carpetas vistas: %s)"
              % sorted({n.split("/")[2] for n in nombres
                        if n.startswith("data/minecraft/") and n.count("/") > 2})[:12])

    # ── 2 · un logro de vanilla que filtre a la VÍCTIMA ──────────────────
    print()
    print("═" * 64)
    print("2 · Cómo filtra vanilla a la víctima en player_killed_entity")
    print("═" * 64)
    enseñados = 0
    for n in nombres:
        if not re.search(r"/advancements?/.*\.json$", n):
            continue
        try:
            adv = json.loads(z.read(n))
        except Exception:
            continue
        for clave, c in (adv.get("criteria") or {}).items():
            if "player_killed_entity" not in str(c.get("trigger")):
                continue
            ent = (c.get("conditions") or {}).get("entity")
            if ent in (None, [], {}):
                continue
            print("  %s · criterio %r" % (n.split("/")[-1], clave))
            print("    entity = %s" % json.dumps(ent, ensure_ascii=False)[:300])
            enseñados += 1
            break
        if enseñados >= 6:
            break
    if not enseñados:
        print("  (vanilla no filtra la víctima en ningún logro de este jar)")

    # ── 3 · los ids de sub-predicado que existen ─────────────────────────
    print()
    print("═" * 64)
    print("3 · Ids de sub-predicado de entidad que registra el jar")
    print("═" * 64)
    # Se busca la clase que registre a la vez `entity_type` y alguno de los
    # nombres clásicos. Ahí saldrá cómo se llama de verdad el de NBT.
    pistas = ("entity_type", "distance", "location", "effects", "flags",
              "equipment", "vehicle", "passenger", "team", "type_specific",
              "nbt", "periodic_tick", "movement", "slots", "components",
              "predicates", "targeted_entity", "stepping_on", "lightning",
              "fishing_hook", "raider", "sheep", "slime")
    encontradas = []
    for n in nombres:
        if not n.endswith(".class"):
            continue
        if "critereon" not in n and "predicate" not in n.lower():
            continue
        try:
            cad = cadenas_de_clase(z.read(n))
        except Exception:
            continue
        hay = [c for c in cad if c in pistas]
        if "entity_type" in hay and len(hay) >= 4:
            encontradas.append((n, hay, cad))
    for n, hay, cad in encontradas[:4]:
        print("\n  %s" % n)
        print("    ids: %s" % ", ".join(sorted(set(hay))))
        # cualquier cadena con pinta de id, por si el nombre del de NBT no
        # estaba en la lista de pistas
        otros = sorted({c for c in cad
                        if re.fullmatch(r"[a-z][a-z0-9_]{2,24}", c)
                        and c not in hay})
        print("    otras cadenas cortas: %s" % ", ".join(otros[:40]))
    if not encontradas:
        print("  (no la he encontrado; dime y lo busco de otra forma)")

    # ── 4 · ¿usa vanilla `nbt` en algún sitio? ───────────────────────────
    print()
    print("═" * 64)
    print("4 · Dónde usa vanilla la clave `nbt` en sus datos")
    print("═" * 64)
    vistos = 0
    for n in nombres:
        if not (n.startswith("data/minecraft/") and n.endswith(".json")):
            continue
        try:
            crudo = z.read(n).decode("utf-8", "replace")
        except Exception:
            continue
        if '"nbt"' not in crudo and '"minecraft:nbt"' not in crudo:
            continue
        for m in re.finditer(r'"(minecraft:)?nbt"\s*:', crudo):
            ini = max(0, m.start() - 90)
            print("  %s\n    …%s…" % (n.split("data/minecraft/")[-1],
                                      crudo[ini:m.end() + 60].replace("\n", " ")))
            vistos += 1
            break
        if vistos >= 5:
            break
    if not vistos:
        print("  (vanilla no usa `nbt` en ningún dato de este jar)")

    print("\n" + "═" * 64)
    print("Pega todo esto en el chat.")


if __name__ == "__main__":
    main()
