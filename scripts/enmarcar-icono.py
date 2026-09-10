#!/usr/bin/env python3
"""
Le pone a un icono suelto el MISMO marco que llevan todos los demás.

EL PROBLEMA
-----------
Los iconos de `static/markers/` van sobre una placa oscura con borde gris claro
y esquinas redondeadas. Esa placa no la dibuja el panel ni BlueMap: viene
pintada dentro del PNG. Un icono generado aparte sale sin ella y canta a
distancia — es lo que pasó con `slime.png` y `ocean_ruin.png`.

Pedirle la placa a un generador de imágenes no funciona bien: el borde son dos
píxeles con un bisel concreto, el fondo es un color concreto (#10141C) y el
radio de la esquina es el que es. A ojo nunca sale igual.

LA IDEA
-------
La placa ya existe 27 veces en la carpeta. Se saca de ahí:

  · el ANILLO exterior (4 px) es idéntico en todos → mediana de los 27
  · el INTERIOR es un color plano → se rellena con él

Así el marco no es una copia aproximada: es el mismo. Y si algún día se
rediseñan los iconos, este programa se entera solo, porque lo vuelve a sacar
de la carpeta en cada pasada.

Del dibujo solo se recorta lo que tiene alfa y se centra. No se reescala salvo
que no quepa: reescalar pixel-art lo emborrona, y casi siempre cabe.

Uso:
    python3 scripts/enmarcar-icono.py static/markers/slime.png
    python3 scripts/enmarcar-icono.py dibujo.png -o static/markers/slime.png
    python3 scripts/enmarcar-icono.py static/markers/*.png --ver   # solo mirar
"""
import argparse
import sys
from pathlib import Path

try:
    import numpy as np
    from PIL import Image
except ImportError:
    print("falta Pillow/numpy:  pip3 install --break-system-packages pillow numpy")
    sys.exit(2)

RAIZ = Path(__file__).resolve().parent.parent
MARCADORES = RAIZ / "static/markers"
LADO = 64          # los iconos son 64x64 nativos (ver copy_icons en scan-structures.py)
ANILLO = 4         # grosor del borde que se copia tal cual
HUECO = 60         # lo más grande que puede ser el dibujo dentro de la placa


def tiene_marco(ruta):
    """¿Este PNG ya lleva la placa? Se mira si el borde está pintado.

    Se mira la banda a 2 px del filo, no el filo mismo: hay dos variantes de
    placa en la carpeta —la de las estructuras llega al borde, la de los
    lugares (danger, shop, spawn) deja 1 px de aire— y mirando la fila 0 la
    segunda daba «sin marco» siendo mentira.
    """
    a = np.array(Image.open(ruta).convert("RGBA"))
    if a.shape[:2] != (LADO, LADO):
        return False
    k = 2
    borde = np.concatenate([a[k, :, 3], a[-1 - k, :, 3], a[:, k, 3], a[:, -1 - k, 3]])
    return float((borde > 8).mean()) > 0.6


def placa(carpeta=MARCADORES):
    """La placa vacía, sacada de los iconos que ya la tienen."""
    con_marco = [p for p in sorted(carpeta.glob("*.png")) if tiene_marco(p)]
    if len(con_marco) < 5:
        raise SystemExit("necesito al menos 5 iconos con marco en %s (encontré %d)"
                         % (carpeta, len(con_marco)))
    pila = np.stack([np.array(Image.open(p).convert("RGBA"), dtype=np.float32)
                     for p in con_marco])
    m = np.median(pila, axis=0)

    # el interior de la mediana sale con el fantasma de los dibujos; se sustituye
    # por el color plano del fondo, que se mide en la banda justo por dentro del
    # anillo — ahí casi ningún dibujo llega
    banda = np.zeros((LADO, LADO), bool)
    banda[ANILLO:ANILLO + 2, ANILLO:-ANILLO] = True
    banda[-ANILLO - 2:-ANILLO, ANILLO:-ANILLO] = True
    banda[ANILLO:-ANILLO, ANILLO:ANILLO + 2] = True
    banda[ANILLO:-ANILLO, -ANILLO - 2:-ANILLO] = True
    fondo = np.median(m[banda], axis=0)

    dentro = np.zeros((LADO, LADO), bool)
    dentro[ANILLO:-ANILLO, ANILLO:-ANILLO] = True
    m[dentro] = fondo
    return Image.fromarray(m.astype(np.uint8), "RGBA"), len(con_marco), tuple(int(v) for v in fondo)


def recortar(im):
    """El dibujo sin el aire de alrededor."""
    a = np.array(im)
    ys, xs = np.where(a[..., 3] > 24)
    if not len(xs):
        raise SystemExit("la imagen está vacía")
    return im.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))


def enmarcar(ruta_dibujo, base):
    dibujo = recortar(Image.open(ruta_dibujo).convert("RGBA"))
    w, h = dibujo.size
    if max(w, h) > HUECO:
        # solo si de verdad no cabe: reescalar pixel-art lo emborrona
        k = HUECO / max(w, h)
        dibujo = dibujo.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
        w, h = dibujo.size
    fuera = base.copy()
    fuera.alpha_composite(dibujo, ((LADO - w) // 2, (LADO - h) // 2))
    return fuera, (w, h)


def main():
    ap = argparse.ArgumentParser(description="Pone el marco de la casa a un icono.")
    ap.add_argument("iconos", nargs="+", help="PNG(s) a enmarcar")
    ap.add_argument("-o", "--salida", help="dónde escribir (solo con un icono)")
    ap.add_argument("--ver", action="store_true", help="no escribe; solo dice quién tiene marco")
    ap.add_argument("--hoja", help="guarda una tira comparativa aquí")
    args = ap.parse_args()

    if args.ver:
        for r in args.iconos:
            print("  %-24s %s" % (Path(r).name, "con marco" if tiene_marco(r) else "SIN marco"))
        return 0

    base, cuantos, fondo = placa()
    print("marco sacado de %d iconos · fondo #%02X%02X%02X" % (cuantos, *fondo[:3]))

    hechos = []
    for r in args.iconos:
        r = Path(r)
        if tiene_marco(r):
            print("  %-24s ya tenía marco, no lo toco" % r.name)
            continue
        fuera, tam = enmarcar(r, base)
        destino = Path(args.salida) if (args.salida and len(args.iconos) == 1) else r
        fuera.save(destino)
        print("  %-24s enmarcado (dibujo %dx%d) → %s" % (r.name, tam[0], tam[1], destino))
        hechos.append(destino)

    if args.hoja and hechos:
        muestra = [p for p in sorted(MARCADORES.glob("*.png"))][:4] + hechos
        Z = 4
        tira = Image.new("RGBA", (len(muestra) * (LADO * Z + 10) + 10, LADO * Z + 20), (24, 28, 34, 255))
        for i, p in enumerate(muestra):
            tira.alpha_composite(Image.open(p).convert("RGBA").resize((LADO * Z, LADO * Z), Image.NEAREST),
                                 (10 + i * (LADO * Z + 10), 10))
        tira.save(args.hoja)
        print("  tira comparativa → %s" % args.hoja)
    return 0


if __name__ == "__main__":
    sys.exit(main())
