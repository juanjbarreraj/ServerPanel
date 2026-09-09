#!/usr/bin/env python3
"""
Dónde va cada estructura, calculado igual que lo hace Minecraft.

LA IDEA
-------
Minecraft no guarda una lista de aldeas: las coloca con una cuenta. Divide el
mundo en regiones de N×N chunks y, para cada región, saca un número al azar
—pero al azar de forma reproducible, a partir de la semilla del mundo y de las
coordenadas de esa región— que dice en qué chunk de dentro va la estructura.

O sea que con la semilla se pueden calcular TODAS las posiciones candidatas del
mundo infinito sin generar nada y sin preguntarle a nadie. Es lo que hace
Chunkbase, y es una cuenta de microsegundos.

    semilla_region = semilla + regionX*341873128712 + regionZ*132897987541 + sal
    rnd = Random(semilla_region)
    chunk = region*espaciado + rnd.nextInt(espaciado - separacion)

CANDIDATA, NO SEGURA
--------------------
La cuenta dice dónde PODRÍA ir. Minecraft además comprueba que el bioma de ese
sitio le valga a esa estructura (una aldea del desierto necesita desierto). Ese
segundo paso es el único que necesita el generador de biomas; sin él, estas
posiciones son candidatas y hay que marcarlas como tales.

El `sal` de cada estructura es una constante que Mojang fija en el datapack de
vanilla, y el espaciado también. Se leen del jar cuando se puede; la tabla de
abajo es el respaldo con los valores conocidos.
"""
import json, os, sys
from pathlib import Path

MASCARA = (1 << 48) - 1
MULT = 0x5DEECE66D
SUMA = 0xB


class JavaRandom:
    """El generador de números de Java, tal cual.

    Hace falta clavarlo bit a bit: si el `nextInt` no da exactamente lo mismo
    que el de Java, las estructuras salen en sitios plausibles pero falsos, que
    es la peor forma de fallar — parece que funciona.
    """

    def __init__(self, semilla):
        self.s = (semilla ^ MULT) & MASCARA

    def next(self, bits):
        self.s = (self.s * MULT + SUMA) & MASCARA
        r = self.s >> (48 - bits)
        # Java devuelve int con signo de 32 bits
        if bits == 32 and r >= (1 << 31):
            r -= (1 << 32)
        return r

    def next_int(self, cota=None):
        if cota is None:
            return self.next(32)
        if cota <= 0:
            raise ValueError("cota debe ser positiva")
        if (cota & -cota) == cota:                 # potencia de dos
            return (cota * self.next(31)) >> 31
        while True:
            bits = self.next(31)
            val = bits % cota
            # el descarte de Java: sin esto los valores salen sesgados
            if bits - val + (cota - 1) < (1 << 31):
                return val

    def next_long(self):
        alto = self.next(32)
        bajo = self.next(32)
        v = (alto << 32) + bajo
        v &= (1 << 64) - 1
        return v - (1 << 64) if v >= (1 << 63) else v


# ─────────────────────────────────────────────────── de dónde salen los datos
#
# Minecraft NO coloca cada estructura por separado: las agrupa en «conjuntos»
# (structure sets) y coloca el conjunto entero con una sola cuenta. Todas las
# aldeas —llanura, desierto, sabana, nieve, taiga— comparten conjunto: hay UNA
# candidata por región y el bioma decide cuál de las cinco sale, o ninguna.
#
# Al principio tenía una fila por estructura con la misma sal, lo que habría
# pintado cinco iconos superpuestos en cada sitio.
#
# Y lo mejor: estos datos están en el jar EN JSON Y SIN OFUSCAR, en
# `data/minecraft/worldgen/structure_set/*.json`. O sea que no hay que
# adivinarlos ni mantenerlos: se leen del jar que tenga el servidor y siempre
# son los de esa versión. La tabla de abajo es solo el respaldo para cuando no
# se puede abrir el jar.
CONJUNTOS_RESPALDO = {
    "villages":          (34, 8, 10387312,  ["village_plains", "village_desert",
                                             "village_savanna", "village_snowy",
                                             "village_taiga"]),
    "pillager_outposts": (32, 8, 165745296, ["pillager_outpost"]),
    "desert_pyramids":   (32, 8, 14357617,  ["desert_pyramid"]),
    "igloos":            (32, 8, 14357618,  ["igloo"]),
    "jungle_temples":    (32, 8, 14357619,  ["jungle_pyramid"]),
    "swamp_huts":        (32, 8, 14357620,  ["swamp_hut"]),
    "ocean_ruins":       (20, 8, 14357621,  ["ocean_ruin_cold", "ocean_ruin_warm"]),
    "shipwrecks":        (24, 4, 165745295, ["shipwreck", "shipwreck_beached"]),
    "ocean_monuments":   (32, 5, 10387313,  ["monument"]),
    "woodland_mansions": (80, 20, 10387319, ["mansion"]),
    "ancient_cities":    (24, 8, 20083232,  ["ancient_city"]),
    "trail_ruins":       (34, 8, 83469867,  ["trail_ruins"]),
    "trial_chambers":    (34, 12, 94251327, ["trial_chambers"]),
    "ruined_portals":    (40, 15, 34222645, ["ruined_portal"]),
    "nether_complexes":  (27, 4, 30084232,  ["fortress", "bastion_remnant"]),
    "end_cities":        (20, 11, 10387313, ["end_city"]),
}


def leer_del_jar(jar):
    """Saca los conjuntos del server.jar. Es JSON plano: nada de ofuscación."""
    import zipfile
    fuera = {}
    with zipfile.ZipFile(jar) as z:
        for n in z.namelist():
            if not n.startswith("data/minecraft/worldgen/structure_set/") or \
               not n.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(n))
                col = d.get("placement") or {}
                if col.get("type") not in ("minecraft:random_spread", None):
                    continue        # los fortines y las fortalezas usan otra cuenta
                esp, sep = col.get("spacing"), col.get("separation")
                sal = col.get("salt")
                if esp is None or sep is None or sal is None:
                    continue
                # Los tesoros enterrados y las minas salieron del jar con
                # espaciado 1 y sal 0: no van en rejilla, van por probabilidad en
                # CADA chunk. La fórmula de aquí no los describe, y pintarlos
                # daría un icono por chunk — millones de puntos falsos.
                if esp <= 1 or col.get("probability") is not None and esp <= 2:
                    continue
                miembros = [e["structure"].split(":")[-1] for e in d.get("structures", [])
                            if isinstance(e, dict) and e.get("structure")]
                fuera[n.rsplit("/", 1)[-1][:-5]] = (esp, sep, sal, miembros)
            except Exception:
                continue
    return fuera


CONJUNTOS = dict(CONJUNTOS_RESPALDO)


def cargar(jar=None):
    """Si se le da el jar, manda el jar. Si no, el respaldo."""
    global CONJUNTOS
    if jar:
        leidos = leer_del_jar(jar)
        if leidos:
            CONJUNTOS = leidos
    return CONJUNTOS


def region_de(chunk, espaciado):
    return chunk // espaciado if chunk >= 0 else -((-chunk + espaciado - 1) // espaciado)


def candidata(semilla, conjunto, region_x, region_z):
    """El chunk donde Minecraft pondría ese conjunto en esa región."""
    esp, sep, sal = CONJUNTOS[conjunto][:3]
    s = (semilla + region_x * 341873128712 + region_z * 132897987541 + sal)
    s &= (1 << 64) - 1
    if s >= (1 << 63):
        s -= (1 << 64)
    r = JavaRandom(s)
    dx = r.next_int(esp - sep)
    dz = r.next_int(esp - sep)
    return (region_x * esp + dx, region_z * esp + dz)


def candidatas_en(semilla, conjunto, x0, z0, x1, z1):
    """Todas las candidatas de un conjunto dentro de un rectángulo, en bloques."""
    esp = CONJUNTOS[conjunto][0]
    c0x, c0z = x0 >> 4, z0 >> 4
    c1x, c1z = x1 >> 4, z1 >> 4
    fuera = []
    for rz in range(region_de(c0z, esp), region_de(c1z, esp) + 1):
        for rx in range(region_de(c0x, esp), region_de(c1x, esp) + 1):
            cx, cz = candidata(semilla, conjunto, rx, rz)
            bx, bz = cx * 16 + 8, cz * 16 + 8
            if x0 <= bx <= x1 and z0 <= bz <= z1:
                fuera.append([bx, bz])
    return fuera


def main():
    if len(sys.argv) < 6:
        print(__doc__)
        print("Uso: estructuras.py <semilla> <x0> <z0> <x1> <z1> [tipo…]")
        return 2
    semilla = int(sys.argv[1])
    x0, z0, x1, z1 = (int(a) for a in sys.argv[2:6])
    jar = os.environ.get("MC_JAR")
    cargar(jar if jar and Path(jar).exists() else None)
    tipos = sys.argv[6:] or sorted(CONJUNTOS)
    fuera = {t: candidatas_en(semilla, t, x0, z0, x1, z1)
             for t in tipos if t in CONJUNTOS}
    print(json.dumps({"semilla": semilla, "candidatas": fuera,
                      "total": sum(len(v) for v in fuera.values())},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
