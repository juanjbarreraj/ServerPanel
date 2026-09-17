#!/usr/bin/env python3
"""
Que no falte ningún bioma en la paleta, y que dos no compartan color.

POR QUÉ ASÍ
-----------
Esta prueba nace del Bosque Moteado. La 26.3 lo añadió, el lector de biomas lo
devolvía sin problema —está en el registro, se enumera solo— pero la paleta del
panel no tenía color para él, así que salía en el rosa de «falta» y en el mapa
parecía una mancha más. Nadie se enteró hasta que Juan lo echó de menos mirando
sitios donde tenía que estar.

Dos invariantes que el mapa da por supuestos y nadie comprobaba:

  · PALETA y BONITO tienen que cubrir EXACTAMENTE los mismos biomas. Uno con
    color y sin nombre sale como «Dappled forest»; uno con nombre y sin color
    sale en rosa. Las dos mitades se editan a mano y se desincronizan solas.

  · NINGÚN bioma puede repetir color. El panel monta un mapa color→bioma
    (`M2.porColor`) para decir qué hay bajo el ratón sin preguntarle al
    servidor; con dos biomas del mismo RGB, uno de los dos deja de existir para
    esa función y el mapa miente sin fallar.

Lo que NO se comprueba aquí es una distancia mínima entre todos los colores: la
paleta es la de AMIDST —la que usa Chunkbase— y trae pares muy juntos de fábrica
(`windswept_hills` y `basalt_deltas` están a ΔE 3,7), pero viven en dimensiones
distintas y nunca coinciden en el mismo mapa. Reescribir la paleta de AMIDST no
toca. Lo que sí se exige es que los colores que añadamos NOSOTROS estén bien
separados, porque esos sí los elegimos.

Correr:  python3 scripts/probar-paleta-biomas.py
"""
import itertools
import math
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import biomas as b                                          # noqa: E402

fallos, pasadas = [], 0

# Los que no vienen de AMIDST: los elegimos nosotros cuando Minecraft añadió el
# bioma y la tabla de AMIDST se quedó sin entrada. A estos sí se les exige
# separación, y la cifra es la que se midió al elegirlos.
NUESTROS = {"dappled_forest": 25.0}


def ok(cond, que):
    global pasadas
    if cond:
        pasadas += 1
        print("  ✔ %s" % que)
    else:
        fallos.append(que)
        print("  ✘ %s" % que)


def titulo(t):
    print("\n\033[1m%s\033[0m" % t)


def lab(c):
    """sRGB → CIE Lab. En Lab, restar dos colores se parece a lo que ve el ojo;
    en RGB no: (0,0,255) y (0,0,200) están «a 55» igual que (0,0,0) y (0,0,55),
    y uno de los dos pares se distingue y el otro no."""
    def f(u):
        u = u / 255
        return u / 12.92 if u <= .04045 else ((u + .055) / 1.055) ** 2.4
    r, g, bl = map(f, c)
    x = (.4124 * r + .3576 * g + .1805 * bl) / .95047
    y = (.2126 * r + .7152 * g + .0722 * bl)
    z = (.0193 * r + .1192 * g + .9505 * bl) / 1.08883
    def h(u): return u ** (1 / 3) if u > .008856 else 7.787 * u + 16 / 116
    X, Y, Z = h(x), h(y), h(z)
    return (116 * Y - 16, 500 * (X - Y), 200 * (Y - Z))


def dE(a, c):
    return math.dist(lab(a), lab(c))


titulo("1 · las dos tablas cuentan lo mismo")
solo_color = sorted(set(b.PALETA) - set(b.BONITO))
solo_nombre = sorted(set(b.BONITO) - set(b.PALETA))
ok(not solo_color, "no hay biomas con color y sin nombre%s"
   % ("" if not solo_color else ": %s" % solo_color))
ok(not solo_nombre, "ni con nombre y sin color%s"
   % ("" if not solo_nombre else ": %s" % solo_nombre))
ok(len(b.PALETA) == len(b.BONITO) and len(b.PALETA) > 60,
   "y las dos tienen los mismos %d biomas" % len(b.PALETA))

titulo("2 · ningún color repetido")
repes = {c: [k for k in b.PALETA if b.PALETA[k] == c]
         for c, n in Counter(b.PALETA.values()).items() if n > 1}
ok(not repes, "cada bioma tiene el suyo%s"
   % ("" if not repes else ": %s" % repes))
ok(b.FALTA not in b.PALETA.values(),
   "y el rosa de «falta» %s no es el de ningún bioma de verdad" % (b.FALTA,))

titulo("3 · lo que no se conoce, se nota")
ok(b.color("minecraft:bioma_que_no_existe") == b.FALTA,
   "un bioma desconocido sale en el color de «falta», no en uno plausible")
ok(b.bonito("minecraft:bioma_que_no_existe") == "Bioma que no existe",
   "y con un nombre legible, no con el id crudo: %r"
   % b.bonito("minecraft:bioma_que_no_existe"))
ok(b.color("minecraft:plains") == b.color("plains"),
   "da igual pedirlo con `minecraft:` delante o sin él")

titulo("4 · los colores que elegimos nosotros")
for nombre, minimo in NUESTROS.items():
    ok(nombre in b.PALETA, "%s tiene color (%s)" % (nombre, b.PALETA.get(nombre)))
    ok(nombre in b.BONITO, "y nombre en español (%r)" % b.BONITO.get(nombre, ""))
    if nombre in b.PALETA:
        cerca = min((dE(b.PALETA[nombre], v), k)
                    for k, v in b.PALETA.items() if k != nombre)
        ok(cerca[0] >= minimo,
           "se distingue de todos los demás (ΔE %.1f del más parecido, %s; "
           "el mínimo que nos exigimos es %.0f)" % (cerca[0], cerca[1], minimo))

titulo("5 · la paleta de AMIDST se respeta")
# Cuatro valores sacados de la tabla de AMIDST. Si alguien los «mejora» sin
# querer, el mapa deja de parecerse a Chunkbase y las comparaciones que hacemos
# con él para comprobar que el generador va bien dejan de valer.
AMIDST = {"forest": (5, 102, 33), "birch_forest": (48, 116, 68),
          "dark_forest": (64, 81, 26), "flower_forest": (45, 142, 73),
          "ocean": (0, 0, 112), "river": (0, 0, 255)}
for k, v in AMIDST.items():
    ok(b.PALETA.get(k) == v, "%s sigue siendo %s" % (k, v))

print()
if fallos:
    print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
    for f in fallos:
        print("   ·", f)
    sys.exit(1)
print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
