#!/usr/bin/env python3
"""
Comprueba el mapa del mundo entero contra el mundo de verdad.

POR QUÉ ESTA PRUEBA Y NO OTRA
-----------------------------
El mapa nuevo calcula dónde va cada estructura a partir de la semilla, sin mirar
el mundo. Eso puede salir plausible y estar mal — que es la peor forma de
fallar: iconos bien puestos, en sitios que no son.

Pero hay una hoja de respuestas a mano: el terreno que YA se ha generado guarda
en sus ficheros las estructuras que le tocaron, escritas por el propio
Minecraft. Esto lee las dos cosas y las compara:

  · el mundo dice que hay una y el cálculo también → bien
  · el mundo dice que hay una y el cálculo no      → SE NOS ESCAPA (grave)
  · el cálculo dice que hay una y el mundo no      → sobra, o ese chunk aún no
                                                     se ha generado

POR REGIONES, NO POR LA CAJA ENTERA
-----------------------------------
La primera versión cogía la caja que abarca TODAS las regiones del disco. En el
servidor de Juan alguien había estado una vez en X ≈ -2.500.000 y otra en
Z ≈ 25.000.000, así que la caja medía millones de bloques por millones: calcular
sus candidatas se comió la memoria y el sistema mató el programa.

Ahora se mira región por región, que es donde hay mundo guardado de verdad.

Correr EN EL SERVIDOR:
    python3 ~/panel/scripts/probar-estructuras.py
    python3 ~/panel/scripts/probar-estructuras.py --rapido    # solo lo ya leído
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
PANEL = Path(os.environ.get("PANEL_DIR", Path.home() / "panel"))
CACHE = PANEL / "data" / "prueba-estructuras.json"
TOLERANCIA = 24        # bloques: el mundo apunta el centro del edificio


def cargar_escaner():
    """scan-structures.py lleva guion en el nombre y no se puede importar con
    `import`. Se carga por ruta: interesa su lector de regiones, que ya estaba
    escrito y probado."""
    ruta = AQUI / "scan-structures.py"
    if not ruta.exists():
        print("no encuentro scan-structures.py")
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
    """Lo que Minecraft dejó escrito, y en qué regiones.

    Devuelve (estructuras, regiones) donde regiones es {(rx, rz)} — NO una caja
    que las abarque todas: ver el comentario de arriba.
    """
    carpeta = esc.region_dir("overworld")
    if not carpeta.is_dir():
        print("no encuentro las regiones en %s" % carpeta)
        return [], set()
    fuera, regiones = [], set()
    ficheros = sorted(carpeta.glob("r.*.mca"))
    t0 = time.time()
    for i, f in enumerate(ficheros):
        if i % 25 == 0 and sys.stdout.isatty():
            # solo en una terminal: al redirigir a un fichero, los retornos de
            # carro dejan una línea kilométrica ilegible
            print("  leyendo regiones… %d/%d (%.0f s)" % (i, len(ficheros), time.time() - t0),
                  end="\r", flush=True)
        try:
            rx, rz = (int(n) for n in f.stem.split(".")[1:3])
        except ValueError:
            continue
        vacia = True
        for cx, cz, raiz in esc.read_region(f):
            vacia = False
            for s in esc.structures_in_chunk(raiz, cx, cz):
                fuera.append((s["kind"], s["x"], s["z"]))
        if not vacia:
            regiones.add((rx, rz))
    if sys.stdout.isatty():
        print(" " * 60, end="\r")
    return fuera, regiones


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

    # ── la hoja de respuestas ────────────────────────────────────────────
    reales = regiones = None
    if "--rapido" in sys.argv and CACHE.is_file():
        d = json.loads(CACHE.read_text())
        if str(d.get("semilla")) == str(semilla):
            reales = [tuple(x) for x in d["reales"]]
            regiones = {tuple(r) for r in d["regiones"]}
            print("\n(usando el escaneo guardado de %s)" % d.get("cuando", "?"))
    if reales is None:
        esc = cargar_escaner()
        if esc is None:
            return 1
        print("\n── lo que dice el mundo ya generado ──")
        t = time.time()
        reales, regiones = del_mundo(esc)
        print("  %d estructuras escritas en el mundo, en %d regiones (%.0f s)"
              % (len(reales), len(regiones), time.time() - t))
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({
                "semilla": str(semilla), "cuando": time.strftime("%Y-%m-%d %H:%M"),
                "reales": [list(x) for x in reales],
                "regiones": [list(r) for r in sorted(regiones)]}))
            print("  (guardado; la próxima vez, --rapido se lo salta)")
        except Exception:
            pass

    if not regiones:
        print("no hay terreno generado que comparar")
        return 1

    # ── ¿hay regiones sueltas en el quinto pino? ─────────────────────────
    xs = sorted(r[0] for r in regiones)
    zs = sorted(r[1] for r in regiones)
    def medio(v):
        return v[len(v) // 2]
    cx, cz = medio(xs), medio(zs)
    lejanas = sorted(r for r in regiones if abs(r[0] - cx) > 200 or abs(r[1] - cz) > 200)
    if lejanas:
        print("\n⚠ %d regiones MUY lejos del resto del mundo:" % len(lejanas))
        for rx, rz in lejanas[:8]:
            print("     región %d,%d  →  alrededor de X %d  Z %d"
                  % (rx, rz, rx * 512, rz * 512))
        print("   Alguien se teletransportó ahí y generó terreno. No estorba, pero")
        print("   ocupa disco y hace que el mapa 3D tenga que dibujar la nada.")

    # ── lo que calcula el panel, región por región ───────────────────────
    print("\n── lo que calcula el panel para esas mismas regiones ──")
    t = time.time()
    cajas = [(rx * 512, rz * 512, rx * 512 + 511, rz * 512 + 511) for rx, rz in sorted(regiones)]
    halladas = E.confirmar_en(srv, semilla, cajas, E.conjuntos_de("overworld"))
    calc = [(E.tipo_de(s["tipo"]), s["x"], s["z"]) for s in halladas]
    calc = [c for c in calc if c[0]]
    print("  %d calculadas en %d regiones (%.1f s)" % (len(calc), len(cajas), time.time() - t))

    # Se comparan por tipo y por cercanía: el mundo apunta el centro del
    # edificio y el cálculo el centro del chunk donde empieza.
    porTipo = {}
    for k, x, z in calc:
        porTipo.setdefault(k, []).append((x, z))

    def cerca(k, x, z):
        for cx2, cz2 in porTipo.get(k, ()):
            if abs(cx2 - x) <= TOLERANCIA and abs(cz2 - z) <= TOLERANCIA:
                return True
        return False

    SIN_REJILLA = {"mineshaft", "buried_treasure", "stronghold"}
    tipos = sorted({k for k, _, _ in reales} | {k for k, _, _ in calc})
    print("\n%-22s %8s %8s %8s %10s" % ("", "mundo", "panel", "aciertos", "se escapan"))
    print("─" * 60)
    total_r = total_a = 0
    for k in tipos:
        if solo and k != solo:
            continue
        rs = [(x, z) for kk, x, z in reales if kk == k]
        cs = porTipo.get(k, [])
        aciertos = sum(1 for x, z in rs if cerca(k, x, z))
        nota = "  (no se calcula)" if k in SIN_REJILLA else ""
        if k not in SIN_REJILLA:
            total_r += len(rs)
            total_a += aciertos
        marca = "  " if (k in SIN_REJILLA or not rs or aciertos == len(rs)) else "⚠ "
        print("%s%-20s %8d %8d %8d %10d%s"
              % (marca, k, len(rs), len(cs), aciertos, len(rs) - aciertos, nota))
    print("─" * 60)
    if total_r:
        print("acierto: %d de %d  (%.1f%%)" % (total_a, total_r, 100 * total_a / total_r))
        print()
        if total_a == total_r:
            print("✔ El cálculo encuentra TODAS las que el mundo tiene escritas.")
        else:
            print("⚠ Se escapan %d. Mira arriba de qué tipo son." % (total_r - total_a))
    print("\nLas minas, los tesoros enterrados y las fortalezas NO se calculan (van")
    print("por otra cuenta distinta), por eso salen aparte y no cuentan.")
    print("\nQue el panel calcule MÁS que el mundo es lo normal: son las que están")
    print("en trozos por explorar, que es justo para lo que sirve el mapa.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
