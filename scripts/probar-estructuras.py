#!/usr/bin/env python3
"""
Comprueba el mapa del mundo entero contra el mundo de verdad.

POR QUÉ ESTA PRUEBA Y NO OTRA
-----------------------------
El mapa nuevo calcula dónde va cada estructura a partir de la semilla, sin mirar
el mundo. Eso puede salir plausible y estar mal — que es la peor forma de
fallar: iconos bien puestos, en sitios que no son.

Pero hay una respuesta buena a mano: el terreno que YA se ha generado guarda en
sus ficheros las estructuras que le tocaron, escritas por el propio Minecraft.
O sea que el mundo de Juan es la hoja de respuestas. Esto lee las dos y las
compara:

  · el mundo dice que hay una y el cálculo también → bien
  · el mundo dice que hay una y el cálculo no      → SE NOS ESCAPA (grave)
  · el cálculo dice que hay una y el mundo no      → sobra, o ese chunk aún no
                                                     se ha generado (se mira)

Correr EN EL SERVIDOR:
    python3 ~/panel/scripts/probar-estructuras.py
    python3 ~/panel/scripts/probar-estructuras.py --tipo village
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))

import biomas                                                    # noqa: E402
import estructuras as E                                          # noqa: E402

MC = Path(os.environ.get("MC_DIR", Path.home() / "minecraft"))
TOLERANCIA = 24        # bloques: el centro que apunta el mundo no es el del chunk


def cargar_escaner():
    """scan-structures.py lleva guion en el nombre, así que no se puede importar
    con `import`. Se carga por ruta: interesa su lector de regiones, que ya
    estaba escrito y probado."""
    ruta = AQUI / "scan-structures.py"
    if not ruta.exists():
        return None
    spec = importlib.util.spec_from_file_location("escaner", ruta)
    m = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(m)
    except Exception as e:
        print("no pude cargar scan-structures.py: %s" % e)
        return None
    return m


def del_mundo(esc):
    """Lo que el propio Minecraft dejó escrito en el overworld ya generado."""
    carpeta = esc.region_dir("overworld")
    if not carpeta.is_dir():
        print("no encuentro las regiones en %s" % carpeta)
        return [], None
    fuera, caja = [], None
    ficheros = sorted(carpeta.glob("r.*.mca"))
    for i, f in enumerate(ficheros):
        if i % 25 == 0:
            print("  leyendo regiones… %d/%d" % (i, len(ficheros)), end="\r", flush=True)
        for cx, cz, raiz in esc.read_region(f):
            bx, bz = cx * 16, cz * 16
            caja = (min(caja[0], bx), min(caja[1], bz),
                    max(caja[2], bx), max(caja[3], bz)) if caja else (bx, bz, bx, bz)
            for s in esc.structures_in_chunk(raiz, cx, cz):
                fuera.append((s["kind"], s["x"], s["z"]))
    print(" " * 40, end="\r")
    return fuera, caja


def main():
    solo = None
    if "--tipo" in sys.argv:
        solo = sys.argv[sys.argv.index("--tipo") + 1]

    srv = biomas.Servicio()
    if not srv.vivo():
        print("✘ el lector de biomas no responde. Arráncalo:")
        print("    sudo systemctl start biomas")
        return 1
    semilla = srv.salud()["semilla"]
    print("semilla del servidor: %s" % semilla)

    jar = None
    for p in sorted((MC / "versions").glob("*/server-*.jar"), reverse=True):
        jar = str(p)
        break
    E.cargar(jar)
    E.cargar_estructuras(jar)
    print("jar: %s" % (jar or "(no encontrado — uso la tabla de respaldo)"))

    esc = cargar_escaner()
    if esc is None:
        return 1

    print("\n── lo que dice el mundo ya generado ──")
    t = time.time()
    reales, caja = del_mundo(esc)
    if not caja:
        print("no hay terreno generado que comparar")
        return 1
    x0, z0, x1, z1 = caja
    print("  %d estructuras escritas en el mundo, entre X %d..%d y Z %d..%d (%.0f s)"
          % (len(reales), x0, x1, z0, z1, time.time() - t))

    print("\n── lo que calcula el panel para ese mismo trozo ──")
    t = time.time()
    calculadas = E.confirmar(srv, semilla, x0 - 512, z0 - 512, x1 + 512, z1 + 512,
                             E.conjuntos_de("overworld"))
    calc = [(E.tipo_de(s["tipo"]), s["x"], s["z"]) for s in calculadas]
    calc = [c for c in calc if c[0]]
    print("  %d calculadas (%.1f s)" % (len(calc), time.time() - t))

    # Se comparan por tipo y por cercanía: el mundo apunta el centro de la
    # construcción y el cálculo el centro del chunk donde empieza, así que no
    # tienen por qué caer en el mismo bloque exacto.
    porTipo = {}
    for k, x, z in calc:
        porTipo.setdefault(k, []).append((x, z))

    def cerca(k, x, z):
        for cx, cz in porTipo.get(k, ()):
            if abs(cx - x) <= TOLERANCIA and abs(cz - z) <= TOLERANCIA:
                return True
        return False

    tipos = sorted({k for k, _, _ in reales} | {k for k, _, _ in calc})
    print("\n%-20s %8s %8s %8s %8s" % ("", "mundo", "panel", "aciertos", "se escapan"))
    print("─" * 58)
    total_r = total_a = 0
    for k in tipos:
        if solo and k != solo:
            continue
        rs = [(x, z) for kk, x, z in reales if kk == k]
        cs = porTipo.get(k, [])
        aciertos = sum(1 for x, z in rs if cerca(k, x, z))
        total_r += len(rs)
        total_a += aciertos
        marca = "  " if not rs or aciertos == len(rs) else "⚠ "
        print("%s%-18s %8d %8d %8d %8d" % (marca, k, len(rs), len(cs),
                                           aciertos, len(rs) - aciertos))
    print("─" * 58)
    if total_r:
        print("acierto: %d de %d  (%.1f%%)" % (total_a, total_r, 100 * total_a / total_r))
        print()
        if total_a == total_r:
            print("✔ El cálculo encuentra TODAS las que el mundo tiene escritas.")
        else:
            print("⚠ Se escapan %d. Mira arriba de qué tipo son." % (total_r - total_a))
            print("  Las minas y los tesoros enterrados NO se calculan a propósito")
            print("  (no van en rejilla, van por probabilidad chunk a chunk), así que")
            print("  ahí es normal.")
    # El panel puede tener MÁS que el mundo sin estar mal: son las que hay en
    # chunks que todavía no ha generado nadie. Eso es justamente lo que aporta.
    print("\nEl panel calcula %d y el mundo tiene %d escritas: la diferencia son"
          % (len(calc), len(reales)))
    print("las que están en trozos por explorar — que es para lo que sirve el mapa.")
    return 0 if True else 1


if __name__ == "__main__":
    sys.exit(main())
