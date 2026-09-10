#!/usr/bin/env python3
"""
Convierte un icono generado (1024×1024, fondo blanco) en el PNG de 64×64 con
transparencia que usa el panel.

Se guarda EN EL REPO a propósito: la versión anterior de esta herramienta vivía
solo dentro del contenedor y se perdió al reciclarse, así que hubo que
reescribirla desde las notas.

Tres pasos:

 1. El fondo se quita con una INUNDACIÓN DESDE LOS BORDES, no borrando todo lo
    blanco. Así se conservan los blancos que son dibujo (brillos, espuma,
    nieve). El precio es que un blanco *encerrado* por el contorno se queda
    opaco; para eso está `--huecos`.
 2. Se busca la rejilla nativa midiendo los tramos de color constante: en un
    dibujo de pixel art escalado, el tramo corto más repetido ES el lado de la
    celda. (Comparar rejillas candidatas por error NO vale: cuanto más fina,
    menos error, siempre, así que elegía la más fina y se perdía el pixelado.)
 3. Se baja a esa rejilla (color mayoritario por celda) y se sube a 64×64 con
    vecino más cercano, que es el formato que sirve static/ui/.

Uso:
    python3 scripts/recortar-icono.py entrada.png=static/ui/s-mundo.png
    ... --umbral 8    si el DIBUJO es casi blanco y toca el borde (papel, nieve)
    ... --huecos      borra además las manchas de blanco encerrado grandes
    ... --rejilla 14   fuerza la rejilla nativa (la de static/ui/ es 14x14)
    ... --colores 10   reduce la paleta: static/ui/ va de 5 a 12 colores planos
"""
import sys
from collections import Counter, deque
from pathlib import Path

from PIL import Image

SALIDA = 64


def mascara_alfa(im):
    """El fondo ya viene marcado: los píxeles transparentes.

    Los generadores de imágenes de ahora devuelven PNG con transparencia, y
    entonces adivinar el fondo por el blanco sobra — y encima estorba, porque un
    icono con nieve o brillos blancos pegados al borde se comería medio dibujo.
    Si la imagen trae alfa de verdad, esa ES la respuesta.
    """
    an, al = im.size
    px = im.load()
    fondo = bytearray(an * al)
    hay = 0
    for y in range(al):
        fila = y * an
        for x in range(an):
            if px[x, y][3] < 128:
                fondo[fila + x] = 1
                hay += 1
    return (fondo, hay)


def mascara_tablero(im):
    """El fondo es el DAMERO gris de «esto es transparente», pintado de verdad.

    Muchos generadores devuelven el PNG con el damero horneado dentro: no hay
    alfa y no es blanco liso, así que ni `mascara_alfa` ni la inundación por
    blanco valen — la segunda deja el damero entero porque los cuadros grises
    no son blancos.

    El primer intento fue buscar los DOS colores exactos del damero en el
    borde. No sirve: la imagen viene recomprimida y el borde tenía 483 colores
    distintos, ninguno con más del 11%. Lo que sí aguanta la compresión es la
    propiedad: el damero es **claro y neutro** (los tres canales casi iguales).
    Con eso más la inundación desde el borde, un blanco o un gris que sea
    DIBUJO se salva igual, porque está rodeado por el contorno oscuro.

    Devuelve (mascara, cuantos) o None si el borde no parece un fondo así.
    """
    an, al = im.size
    px = im.load()

    def neutro(c):
        return (max(c) - min(c)) <= 24        # gris de verdad, no un color

    # El umbral de claridad NO puede ser fijo: el mismo damero llega a veces a
    # 254 y a veces a 131, según cómo se haya recomprimido la imagen. Se aprende
    # del propio borde: se miran sus grises y se toma el más oscuro con margen.
    borde = []
    for x in range(0, an, 5):
        borde.append(px[x, 0][:3]); borde.append(px[x, al - 1][:3])
    for y in range(0, al, 5):
        borde.append(px[0, y][:3]); borde.append(px[an - 1, y][:3])
    grises = sorted(sum(c) / 3 for c in borde if neutro(c))
    if not borde or len(grises) / len(borde) < 0.55:
        return None                            # el borde no es un fondo gris
    suelo = max(100.0, grises[len(grises) // 20] - 12)

    def es_fondo(c):
        return neutro(c) and (sum(c) / 3) >= suelo

    fondo = bytearray(an * al)
    cola = deque()

    def mirar(x, y):
        if not fondo[y * an + x] and es_fondo(px[x, y][:3]):
            fondo[y * an + x] = 1
            cola.append((x, y))

    for x in range(an):
        mirar(x, 0); mirar(x, al - 1)
    for y in range(al):
        mirar(0, y); mirar(an - 1, y)
    while cola:
        x, y = cola.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < an and 0 <= ny < al:
                mirar(nx, ny)
    hay = sum(fondo)
    return (fondo, hay) if hay > an * al * 0.08 else None


def mascara_fondo(im, umbral):
    """True donde hay fondo: blanco conectado con el borde."""
    an, al = im.size
    px = im.load()
    limite = 255 - umbral
    fondo = bytearray(an * al)
    cola = deque()

    def blanco(x, y):
        r, g, b = px[x, y][:3]
        return r >= limite and g >= limite and b >= limite

    for x in range(an):
        for y in (0, al - 1):
            if not fondo[y * an + x] and blanco(x, y):
                fondo[y * an + x] = 1
                cola.append((x, y))
    for y in range(al):
        for x in (0, an - 1):
            if not fondo[y * an + x] and blanco(x, y):
                fondo[y * an + x] = 1
                cola.append((x, y))

    while cola:
        x, y = cola.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < an and 0 <= ny < al and not fondo[ny * an + nx] and blanco(nx, ny):
                fondo[ny * an + nx] = 1
                cola.append((nx, ny))
    return fondo


def rejilla_nativa(im):
    """Deduce el tamaño de la rejilla midiendo los TRAMOS de color constante.

    El primer intento fue comparar cada rejilla candidata con la imagen y
    quedarse con la que menos se desviaba. No funciona: cuanto más fina la
    rejilla, menos error, siempre — así que elegía 64 y perdía el aspecto de
    pixel art. Esto mide otra cosa: en un dibujo escalado, el tramo de color
    constante más frecuente ES el lado de la celda.
    """
    px = im.load()
    an, al = im.size
    tramos = Counter()
    for y in range(0, al, 3):
        ant, ini = None, 0
        for x in range(an):
            c = px[x, y][:3]
            if ant is None:
                ant, ini = c, x
                continue
            if max(abs(a - b) for a, b in zip(c, ant)) > 40:
                if x - ini >= 20:
                    tramos[x - ini] += 1
                ini = x
            ant = c
    if not tramos:
        return 16
    # el lado de la celda es el tramo corto que más se repite; los largos son
    # zonas de un solo color (el cielo de un bloque grande, el relleno)
    corriente = [t for t, c in tramos.most_common(20) if c >= max(tramos.values()) * 0.2]
    lado = min(corriente)
    return max(8, min(64, round(an / lado)))


def celdas(im, fondo, n):
    """Color mayoritario de cada celda; None si la celda es fondo."""
    an, al = im.size
    px = im.load()
    cx, cy = an / n, al / n
    salida = []
    for j in range(n):
        fila = []
        for i in range(n):
            x0, x1 = int(i * cx), int((i + 1) * cx)
            y0, y1 = int(j * cy), int((j + 1) * cy)
            cuenta, vacios, tot = Counter(), 0, 0
            for y in range(y0, y1, max(1, (y1 - y0) // 12)):
                for x in range(x0, x1, max(1, (x1 - x0) // 12)):
                    tot += 1
                    if fondo[y * an + x]:
                        vacios += 1
                    else:
                        cuenta[px[x, y][:3]] += 1
            fila.append(None if (not cuenta or vacios > tot * 0.6)
                        else cuenta.most_common(1)[0][0])
        salida.append(fila)
    return salida


def quitar_huecos(rej, minimo=6):
    """Borra manchas GRANDES de blanco encerrado (el vano de una vagoneta, el
    hueco entre peldaños). Las pequeñas se respetan: suelen ser dibujo."""
    n = len(rej)
    visto = [[False] * n for _ in range(n)]
    for j in range(n):
        for i in range(n):
            c = rej[j][i]
            if visto[j][i] or c is None or min(c) < 231:
                continue
            grupo, cola = [], deque([(i, j)])
            visto[j][i] = True
            while cola:
                x, y = cola.popleft()
                grupo.append((x, y))
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < n and 0 <= ny < n and not visto[ny][nx]:
                        v = rej[ny][nx]
                        if v is not None and min(v) >= 231:
                            visto[ny][nx] = True
                            cola.append((nx, ny))
            if len(grupo) >= minimo:
                for x, y in grupo:
                    rej[y][x] = None
    return rej


def recortar(entrada, salida, umbral=24, huecos=False, forzar=None, colores=None):
    original = Image.open(entrada).convert("RGBA")
    fondo, transparentes = mascara_alfa(original)
    im = original.convert("RGB")
    # Menos de un 2% transparente = la imagen no trae fondo recortado y hay que
    # deducirlo por el blanco, como siempre.
    if transparentes < original.size[0] * original.size[1] * 0.02:
        # Antes de rendirse al blanco: ¿es el damero gris de «transparente»
        # pintado dentro del PNG? Pasa con casi todo lo que sale de un banco de
        # imágenes, y tratarlo como blanco se come el dibujo o no quita nada.
        tablero = mascara_tablero(im)
        if tablero:
            fondo = tablero[0]
            print("  (fondo de damero gris: lo quito por su patrón)")
        else:
            fondo = mascara_fondo(im, umbral)
    else:
        print("  (fondo transparente: uso el alfa, no el blanco)")
    n = forzar or rejilla_nativa(im)
    rej = celdas(im, fondo, n)
    if huecos:
        rej = quitar_huecos(rej)

    chico = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    p = chico.load()
    opacos = 0
    for j in range(n):
        for i in range(n):
            c = rej[j][i]
            if c is not None:
                p[i, j] = (c[0], c[1], c[2], 255)
                opacos += 1
    # Los iconos de las pestañas son de 5 a 12 colores planos. Una imagen
    # recomprimida trae decenas de tonos casi iguales que no se ven pero que
    # rompen ese aire: reduciendo la paleta el icono entra en la familia.
    if colores:
        recorte = chico.convert("RGB").quantize(colors=colores, method=Image.MEDIANCUT)
        recorte = recorte.convert("RGBA")
        alfa = chico.getchannel("A")
        recorte.putalpha(alfa)
        chico = recorte
    grande = chico.resize((SALIDA, SALIDA), Image.NEAREST)
    Path(salida).parent.mkdir(parents=True, exist_ok=True)
    grande.save(salida)
    blancos = sum(1 for j in range(n) for i in range(n)
                  if rej[j][i] and min(rej[j][i]) >= 231)
    print("%s → %s · rejilla %dx%d · %d celdas con dibujo · %d blancas"
          % (Path(entrada).name, salida, n, n, opacos, blancos))
    return grande


def main():
    pares, umbral, huecos, forzar, colores = [], 24, False, None, None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--umbral":
            umbral = int(args[i + 1]); i += 2
        elif a == "--rejilla":
            forzar = int(args[i + 1]); i += 2
        elif a == "--colores":
            colores = int(args[i + 1]); i += 2
        elif a == "--huecos":
            huecos = True; i += 1
        elif "=" in a:
            pares.append(a.split("=", 1)); i += 1
        else:
            i += 1
    if not pares:
        print(__doc__)
        return 2
    for entrada, salida in pares:
        recortar(entrada, salida, umbral, huecos, forzar, colores)
    return 0


if __name__ == "__main__":
    sys.exit(main())
