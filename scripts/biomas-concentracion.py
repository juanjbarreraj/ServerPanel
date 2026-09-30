#!/usr/bin/env python3
"""
Dónde se juntan más biomas distintos en un mismo sitio.

Le pregunta al lector de biomas (el mismo que dibuja la pestaña Explorar) por
una rejilla de puntos, y luego pasa una ventana cuadrada contando cuántos
biomas DISTINTOS caen dentro. El sitio con más, gana.

  python3 scripts/biomas-concentracion.py                  # ±5000 alrededor del spawn
  python3 scripts/biomas-concentracion.py --radio 10000
  python3 scripts/biomas-concentracion.py --ventana 256    # un sitio más pequeño
  python3 scripts/biomas-concentracion.py --solo-medir     # solo dice cuánto tardaría

🔴 PRIMERO MIDE, LUEGO PREGUNTA
-------------------------------
El coste no se puede calcular a ojo: depende de la máquina, de cuántos hilos
tenga el lector y de qué parte del mundo sea. Así que lo primero que hace es
pedir un cuadro pequeño, cronometrarlo, y decir cuánto va a tardar el de
verdad. Si no te compensa, cancelas antes de empezar.

QUÉ CUENTA COMO «UN SITIO»
--------------------------
Una ventana cuadrada de `--ventana` bloques de lado (por defecto 384, que es
más o menos lo que se abarca desde una colina). Dentro de esa ventana se
cuentan los biomas distintos. No es lo mismo «muchos biomas cerca» que «muchos
biomas a la vista», y esto último es lo que se suele querer.

Se enseñan los mejores sitios SIN SOLAPARSE entre ellos: si no, los diez
primeros serían el mismo rincón movido un paso cada vez.

POR QUÉ EL PASO POR DEFECTO ES 16
---------------------------------
Minecraft define los biomas en celdas de 4×4 bloques, así que muestrear más
fino que eso no añade información, solo tiempo. 16 pierde algún bioma diminuto
(un charco de río de dos celdas) a cambio de ir 16 veces más rápido que 4.
Con `--paso 4` se ve todo, y tarda lo que tarda.
"""
import argparse, json, math, sys, time, urllib.request
from collections import Counter

BASE = "http://127.0.0.1:25580"
TOPE_N = 1024                     # el máximo que acepta /cuadro


def pedir(ruta, binario=False, espera=600):
    try:
        with urllib.request.urlopen(BASE + ruta, timeout=espera) as r:
            return r.read() if binario else json.loads(r.read())
    except Exception as e:
        print("\n✗ No he podido hablar con el lector de biomas (%s)." % e)
        print("  Compruébalo así:   curl -s %s/salud" % BASE)
        print("  Y si no responde:  sudo systemctl restart biomas")
        sys.exit(2)


def cuadro(x, z, n, paso, y=None):
    ruta = "/cuadro?x=%d&z=%d&n=%d&paso=%d" % (x, z, n, paso)
    if y is not None:
        ruta += "&y=%d" % y
    return pedir(ruta, binario=True)


def humano(seg):
    if seg < 90:
        return "%d segundos" % round(seg)
    if seg < 5400:
        return "%d minutos" % round(seg / 60)
    return "%.1f horas" % (seg / 3600)


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--radio", type=int, default=5000)
    ap.add_argument("--centro", default="0,0")
    ap.add_argument("--paso", type=int, default=16)
    ap.add_argument("--ventana", type=int, default=384)
    ap.add_argument("--cuantos", type=int, default=10)
    ap.add_argument("--y", type=int, default=None)
    ap.add_argument("--solo-medir", action="store_true")
    ap.add_argument("--si", action="store_true", help="no preguntar, tirar p'alante")
    ap.add_argument("-h", "--help", action="store_true")
    a = ap.parse_args()
    if a.help:
        print(__doc__)
        return 0

    salud = pedir("/salud")
    leyenda = {int(k): v for k, v in pedir("/leyenda").items()}
    cx, cz = (int(v) for v in a.centro.split(","))
    print("semilla %s · %d hilos · %d biomas en la leyenda"
          % (salud.get("semilla"), salud.get("hilos", 1), len(leyenda)))
    print("zona: %d,%d ± %d   ·   paso %d   ·   ventana %d bloques"
          % (cx, cz, a.radio, a.paso, a.ventana))

    lado = 2 * a.radio // a.paso              # muestras por lado
    total = lado * lado
    print("muestras: %d × %d = %s" % (lado, lado, "{:,}".format(total).replace(",", ".")))

    # ── medir antes de prometer nada ─────────────────────────────────────
    print("\nmidiendo…", end="", flush=True)
    t0 = time.time()
    n_prueba = 128
    cuadro(cx, cz, n_prueba, a.paso, a.y)
    dt = time.time() - t0
    por_muestra = dt / (n_prueba * n_prueba)
    estimado = por_muestra * total
    print(" %.2f s para %s muestras → %.1f µs cada una"
          % (dt, "{:,}".format(n_prueba * n_prueba).replace(",", "."),
             por_muestra * 1e6))
    print("\n⏱  ESTIMADO: %s" % humano(estimado))
    if estimado > 1800:
        print("   (para bajarlo: --radio más pequeño, o --paso 32)")
    print("   El lector va con %d hilo(s) y comparte CPU con Minecraft, así que"
          % salud.get("hilos", 1))
    print("   mientras corre se puede notar. Mejor con el server vacío.")

    if a.solo_medir:
        return 0
    if not a.si:
        try:
            if input("\n¿Sigo? [s/N] ").strip().lower() not in ("s", "si", "sí", "y"):
                print("cancelado, no se ha calculado nada")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\ncancelado")
            return 0

    # ── el barrido ───────────────────────────────────────────────────────
    # Se pide en trozos de TOPE_N×TOPE_N muestras: menos peticiones que ir
    # punto a punto, y el lector paraleliza cada cuadro entre sus hilos.
    rejilla = bytearray(lado * lado)
    x0, z0 = cx - a.radio, cz - a.radio
    hecho, t0 = 0, time.time()
    for j0 in range(0, lado, TOPE_N):
        for i0 in range(0, lado, TOPE_N):
            nw = min(TOPE_N, lado - i0)
            nh = min(TOPE_N, lado - j0)
            n = max(nw, nh)                     # /cuadro es cuadrado
            datos = cuadro(x0 + i0 * a.paso, z0 + j0 * a.paso, n, a.paso, a.y)
            for j in range(nh):
                ini = j * n
                rejilla[(j0 + j) * lado + i0: (j0 + j) * lado + i0 + nw] = \
                    datos[ini:ini + nw]
            hecho += nw * nh
            pasado = time.time() - t0
            queda = pasado / max(1, hecho) * (total - hecho)
            print("\r  %5.1f%%   quedan %s        "
                  % (100 * hecho / total, humano(queda)), end="", flush=True)
    print("\r  100%%   terminado en %s            " % humano(time.time() - t0))

    # ── contar biomas por ventana ────────────────────────────────────────
    w = max(1, a.ventana // a.paso)             # muestras por lado de ventana
    if w > lado:
        print("\n✗ La ventana (%d) es más grande que la zona. Sube --radio."
              % a.ventana)
        return 1
    print("\ncontando biomas en ventanas de %d bloques (%d×%d muestras)…"
          % (a.ventana, w, w))

    # Recuento por ventana deslizante. Se hace por columnas acumuladas: mover
    # la ventana un paso solo cambia una fila y una columna, así que no hay que
    # recontar las w² muestras cada vez.
    # 🔴 Solo se guarda (cuántos, i, j) — NO el conjunto de biomas. Con medio
    # millón de ventanas, un frozenset por cada una se come cientos de MB en
    # una máquina que tiene 12 GB y está corriendo un servidor de Minecraft.
    # Los biomas concretos se recalculan al final, solo para los diez elegidos.
    # Y el listón sube solo cuando la lista crece: mantenerla entera tampoco
    # hace falta, únicamente estorba.
    sitios, umbral = [], 6
    for j in range(0, lado - w + 1):
        cuenta = Counter()
        for jj in range(j, j + w):
            cuenta.update(rejilla[jj * lado: jj * lado + w])
        for i in range(0, lado - w + 1):
            if i > 0:
                for jj in range(j, j + w):
                    fuera = rejilla[jj * lado + i - 1]
                    cuenta[fuera] -= 1
                    if cuenta[fuera] == 0:
                        del cuenta[fuera]
                    cuenta[rejilla[jj * lado + i + w - 1]] += 1
            if len(cuenta) >= umbral:
                sitios.append((len(cuenta), i, j))
                if len(sitios) > 20000:
                    sitios.sort(key=lambda s: -s[0])
                    del sitios[5000:]
                    umbral = max(umbral, sitios[-1][0])

    if not sitios:
        print("  Nada con 6 biomas o más. Prueba --ventana más grande.")
        return 0

    def biomas_de(i, j):
        vistos = set()
        for jj in range(j, j + w):
            vistos.update(rejilla[jj * lado + i: jj * lado + i + w])
        return vistos

    # ── los mejores, sin solaparse ───────────────────────────────────────
    sitios.sort(key=lambda s: -s[0])
    elegidos = []
    for cuantos, i, j in sitios:
        x = x0 + (i + w // 2) * a.paso
        z = z0 + (j + w // 2) * a.paso
        if any(abs(x - ex) < a.ventana and abs(z - ez) < a.ventana
               for _, ex, ez, _ in elegidos):
            continue                            # ya hay uno mejor ahí al lado
        elegidos.append((cuantos, x, z, biomas_de(i, j)))
        if len(elegidos) >= a.cuantos:
            break

    print("\n" + "═" * 64)
    print("  LOS %d SITIOS CON MÁS BIOMAS JUNTOS" % len(elegidos))
    print("═" * 64)
    for k, (cuantos, x, z, biomas) in enumerate(elegidos, 1):
        print("\n%2d.  %d biomas   ·   x %d   z %d" % (k, cuantos, x, z))
        print("     /tp @s %d ~ %d" % (x, z))
        nombres = sorted(leyenda.get(b, "?%d" % b).replace("minecraft:", "")
                         for b in biomas)
        for t in range(0, len(nombres), 4):
            print("     " + ", ".join(nombres[t:t + 4]))
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
