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

    # Los tres de abajo hacen falta para las estructuras que NO van en rejilla
    # (tesoros, minas, puestos de saqueadores): esas se deciden chunk a chunk
    # con una tirada de probabilidad, y cada una usa un dado distinto.
    def sembrar(self, s):
        self.s = (s ^ MULT) & MASCARA

    def next_float(self):
        return self.next(24) / float(1 << 24)

    def next_double(self):
        return ((self.next(26) << 27) + self.next(27)) * (2.0 ** -53)


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
# (espaciado, separación, sal, miembros, reparto)
#
# EL REPARTO NO ES IGUAL PARA TODAS y es un fallo que costó encontrar. Casi
# todas reparten «lineal»: un número al azar entre 0 y espaciado-separación.
# Pero las mansiones, los monumentos oceánicos y las ciudades del End reparten
# «triangular»: sacan DOS números y se quedan con la media, lo que las empuja
# hacia el centro de su región. Haciéndolo lineal para todas, esas tres salen
# en una casilla plausible y equivocada — que es la peor forma de fallar.
CONJUNTOS_RESPALDO = {
    "villages":          (34, 8, 10387312,  ["village_plains", "village_desert",
                                             "village_savanna", "village_snowy",
                                             "village_taiga"], "linear"),
    "pillager_outposts": (32, 8, 165745296, ["pillager_outpost"], "linear"),
    "desert_pyramids":   (32, 8, 14357617,  ["desert_pyramid"], "linear"),
    "igloos":            (32, 8, 14357618,  ["igloo"], "linear"),
    "jungle_temples":    (32, 8, 14357619,  ["jungle_pyramid"], "linear"),
    "swamp_huts":        (32, 8, 14357620,  ["swamp_hut"], "linear"),
    "ocean_ruins":       (20, 8, 14357621,  ["ocean_ruin_cold", "ocean_ruin_warm"], "linear"),
    "shipwrecks":        (24, 4, 165745295, ["shipwreck", "shipwreck_beached"], "linear"),
    "ocean_monuments":   (32, 5, 10387313,  ["monument"], "triangular"),
    "woodland_mansions": (80, 20, 10387319, ["mansion"], "triangular"),
    "ancient_cities":    (24, 8, 20083232,  ["ancient_city"], "linear"),
    "trail_ruins":       (34, 8, 83469867,  ["trail_ruins"], "linear"),
    "trial_chambers":    (34, 12, 94251327, ["trial_chambers"], "linear"),
    "ruined_portals":    (40, 15, 34222645, ["ruined_portal"], "linear"),
    "nether_complexes":  (27, 4, 30084232,  ["fortress", "bastion_remnant"], "linear"),
    "end_cities":        (20, 11, 10387313, ["end_city"], "triangular"),
}


# ───────────────────────────────────────────── cómo se enseña cada estructura
#
# Esta tabla la comparten el mapa 3D (scan-structures.py, que lee el mundo de
# verdad) y el mapa del mundo entero (el panel, que lo calcula de la semilla).
# Estaba duplicada en los dos y era cuestión de tiempo que se separaran: se
# añade una estructura nueva en un sitio y en el otro sale sin icono.
#
# Los nombres en español son los OFICIALES de la wiki en español de Minecraft
# (es.minecraft.wiki), no traducciones propias.
#
# max_dist = a qué distancia de cámara deja de dibujarse en BlueMap.
# oculto   = la capa arranca apagada (las que salen a cientos).
TIPOS = {
    #                    icono              español                 inglés                 max_dist  oculto  orden
    "ancient_city":     ("ancient_city",    "Ciudad antigua",       "Ancient City",         100000, False, 10),
    "mansion":          ("mansion",         "Mansión del bosque",   "Woodland Mansion",     100000, False, 11),
    "monument":         ("monument",        "Monumento oceánico",   "Ocean Monument",       100000, False, 12),
    "stronghold":       ("stronghold",      "Fortaleza",            "Stronghold",           100000, False, 13),
    "end_city":         ("end_city",        "Ciudad del End",       "End City",             100000, False, 14),
    "bastion_remnant":  ("bastion_remnant", "Bastión en ruinas",    "Bastion Remnant",      100000, False, 15),
    "fortress":         ("nether_fortress", "Fortaleza del Nether", "Nether Fortress",      100000, False, 16),
    "trial_chambers":   ("trial_chambers",  "Cámaras de desafío",   "Trial Chambers",        20000, False, 17),
    "village":          ("village",         "Aldea",                "Village",               20000, False, 20),
    "pillager_outpost": ("outpost",         "Puesto de saqueadores", "Pillager Outpost",     20000, False, 21),
    "desert_pyramid":   ("desert_temple",   "Pirámide del desierto", "Desert Pyramid",        8000, False, 30),
    "jungle_pyramid":   ("jungle_temple",   "Templo de jungla",     "Jungle Temple",          8000, False, 31),
    "swamp_hut":        ("witch_hut",       "Cabaña de pantano",    "Swamp Hut",              8000, False, 32),
    "igloo":            ("igloo",           "Iglú",                 "Igloo",                  8000, False, 33),
    "trail_ruins":      ("trail_ruins",     "Sendero en ruinas",    "Trail Ruins",            8000, False, 34),
    "ocean_ruin":       ("ocean_ruin",      "Ruinas oceánicas",     "Ocean Ruins",            3000, True,  39),
    "shipwreck":        ("shipwreck",       "Naufragio",            "Shipwreck",              3000, True,  40),
    "buried_treasure":  ("buried_treasure", "Tesoro enterrado",     "Buried Treasure",        3000, True,  41),
    "ruined_portal":    ("ruined_portal",   "Portal en ruinas",     "Ruined Portal",          3000, True,  42),
    "mineshaft":        ("mineshaft",       "Mina abandonada",      "Mineshaft",              3000, True,  43),
}


def tipo_de(sid):
    """minecraft:village_plains → village ; ruined_portal_desert → ruined_portal"""
    s = str(sid).split(":", 1)[-1]
    if s in TIPOS:
        return s
    # de más largo a más corto: si no, `mineshaft_mesa` podría casar antes con
    # algo más corto y acabar en el cajón equivocado
    for k in sorted(TIPOS, key=len, reverse=True):
        if s.startswith(k + "_") or s.endswith("_" + k):
            return k
    return None


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
                miembros = [e["structure"].split(":")[-1] for e in d.get("structures", [])
                            if isinstance(e, dict) and e.get("structure")]
                reparto = str(col.get("spread_type") or "linear").split(":")[-1]
                # Lo que faltaba y hacía sobrar iconos: la rejilla dice DÓNDE
                # podría ir, pero Minecraft además tira un dado en ese chunk
                # (`frequency`) y descarta si hay otra cosa cerca
                # (`exclusion_zone`). Sin esto, los puestos de saqueadores se
                # dibujaban CINCO veces de más — el dado es 1 de cada 5 — y sin
                # respetar que no pueden estar a menos de 10 chunks de una aldea.
                frec = col.get("frequency")
                metodo = str(col.get("frequency_reduction_method") or "default").split(":")[-1]
                ex = col.get("exclusion_zone") or None
                if ex:
                    ex = (str(ex.get("other_set", "")).split(":")[-1],
                          int(ex.get("chunk_count", 0)))
                # `locate_offset` es dónde cae de verdad dentro del chunk. Para
                # un cofre enterrado importa: son 9 bloques, no el centro.
                lo = col.get("locate_offset") or None
                lo = (int(lo[0]), int(lo[2])) if isinstance(lo, list) and len(lo) >= 3 else None
                fuera[n.rsplit("/", 1)[-1][:-5]] = (esp, sep, sal, miembros, reparto,
                                                    frec, metodo, ex, lo)
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


def _campo(conjunto, i, por_defecto=None):
    """Un campo de CONJUNTOS que puede no estar (el respaldo son tuplas cortas)."""
    d = CONJUNTOS.get(conjunto)
    if not d or len(d) <= i or d[i] is None:
        return por_defecto
    return d[i]


def _reparto(conjunto):
    return _campo(conjunto, 4, "linear")


def _frecuencia(conjunto):
    return _campo(conjunto, 5)


def _metodo(conjunto):
    return _campo(conjunto, 6, "default")


def _exclusion(conjunto):
    return _campo(conjunto, 7)


def _dentro_del_chunk(conjunto):
    """Dónde cae dentro de su chunk, en bloques."""
    return _campo(conjunto, 8, (8, 8))


def por_probabilidad(conjunto):
    """¿Va chunk a chunk en vez de en rejilla? (tesoros, minas)"""
    return _campo(conjunto, 0, 32) <= 1


# ══════════════════════════════════ el dado: `frequency` y su método
#
# Sacado del jar, no de la memoria: `StructurePlacement.isStructureChunk` es
#   isPlacementChunk (la rejilla)  &&  el dado  &&  la zona de exclusión
# y el dado tiene CUATRO variantes distintas, cada una sembrando el generador a
# su manera. Están en `StructurePlacement$FrequencyReductionMethod`:
#
#   default        → setLargeFeatureWithSalt(semilla, sal, cx, cz)  · nextFloat()  < f
#   legacy_type_1  → el de los puestos de saqueadores: nextInt(1/f) == 0
#   legacy_type_2  → setLargeFeatureWithSalt(semilla, cx, cz, 10387320) · nextFloat() < f
#   legacy_type_3  → setLargeFeatureSeed(semilla, cx, cz) · nextDouble() < f
#
# Comprobado contra un servidor 26.2 de verdad con la semilla de Juan: 12 de 12
# tesoros y 12 de 12 minas que dio /locate caen en un chunk que estas cuentas
# marcan. Sin esto, los puestos de saqueadores salían CINCO veces de más.
def _s64(v):
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >= (1 << 63) else v


def _s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v >= (1 << 31) else v


def _con_sal(r, semilla, a, b, sal):
    r.sembrar(_s64(a * 341873128712 + b * 132897987541 + semilla + sal))


def _grande(r, semilla, x, z):
    r.sembrar(semilla)
    l1 = r.next_long()
    l2 = r.next_long()
    r.sembrar(_s64(_s64(x * l1) ^ _s64(z * l2) ^ semilla))


def pasa_el_dado(semilla, conjunto, cx, cz):
    f = _frecuencia(conjunto)
    if f is None or f >= 1.0:
        return True
    metodo = _metodo(conjunto)
    sal = _campo(conjunto, 2, 0)
    r = JavaRandom(0)
    if metodo == "legacy_type_1":
        # el de los puestos: la semilla se hace con el chunk dividido entre 16
        i, j = cx >> 4, cz >> 4
        r.sembrar(_s64(_s32(i ^ _s32(j << 4)) ^ semilla))
        r.next_int()
        return r.next_int(int(1.0 / f)) == 0
    if metodo == "legacy_type_2":
        _con_sal(r, semilla, cx, cz, 10387320)
        return r.next_float() < f
    if metodo == "legacy_type_3":
        _grande(r, semilla, cx, cz)
        return r.next_double() < f
    _con_sal(r, semilla, sal, cx, cz)
    return r.next_float() < f


def pasa_la_exclusion(semilla, conjunto, cx, cz):
    """`exclusion_zone`: nada de puestos a menos de 10 chunks de una aldea."""
    ex = _exclusion(conjunto)
    if not ex:
        return True
    otro, radio = ex
    if otro not in CONJUNTOS or por_probabilidad(otro):
        return True
    esp = _campo(otro, 0, 32)
    for rz in range(region_de(cz - radio, esp), region_de(cz + radio, esp) + 1):
        for rx in range(region_de(cx - radio, esp), region_de(cx + radio, esp) + 1):
            ox, oz = candidata(semilla, otro, rx, rz)
            if abs(ox - cx) <= radio and abs(oz - cz) <= radio \
                    and pasa_el_dado(semilla, otro, ox, oz):
                return False
    return True


def candidata(semilla, conjunto, region_x, region_z):
    """El chunk donde Minecraft pondría ese conjunto en esa región.

    OJO con el orden y con la cantidad de números que se sacan: en «triangular»
    se sacan DOS por eje, y sacar uno de más o de menos desplaza todo lo que
    venga después. Por eso los dos ejes se calculan con la misma función y
    seguidos, igual que en el juego.
    """
    esp, sep, sal = CONJUNTOS[conjunto][:3]
    s = (semilla + region_x * 341873128712 + region_z * 132897987541 + sal)
    s &= (1 << 64) - 1
    if s >= (1 << 63):
        s -= (1 << 64)
    r = JavaRandom(s)
    n = esp - sep
    if _reparto(conjunto) == "triangular":
        # la media de dos tiradas: empuja la estructura hacia el centro de la
        # región en vez de repartirla por igual
        tirada = lambda: (r.next_int(n) + r.next_int(n)) // 2
    else:
        tirada = lambda: r.next_int(n)
    dx = tirada()
    dz = tirada()
    return (region_x * esp + dx, region_z * esp + dz)


# Cuántos chunks se aceptan mirar de uno en uno para las de probabilidad. Un
# rectángulo de 8192 bloques son 262 144 chunks y cada uno cuesta varias tiradas;
# más allá de esto no se calcula y se avisa, en vez de tardar medio minuto.
TOPE_CHUNKS = 400_000


def candidatas_en(semilla, conjunto, x0, z0, x1, z1):
    """Todas las candidatas de un conjunto dentro de un rectángulo, en bloques.

    Tres filtros, en el mismo orden que `StructurePlacement.isStructureChunk`:
    la rejilla, el dado de `frequency` y la zona de exclusión. Los dos últimos
    faltaban y por eso sobraban iconos.
    """
    esp = _campo(conjunto, 0, 32)
    dx, dz = _dentro_del_chunk(conjunto)
    c0x, c0z = x0 >> 4, z0 >> 4
    c1x, c1z = x1 >> 4, z1 >> 4
    fuera = []

    if esp <= 1:
        # Sin rejilla: cada chunk se juega su tirada. Son los tesoros enterrados
        # (1 de cada 100) y las minas (4 de cada 1000).
        if (c1x - c0x + 1) * (c1z - c0z + 1) > TOPE_CHUNKS:
            return fuera
        for cz in range(c0z, c1z + 1):
            for cx in range(c0x, c1x + 1):
                if pasa_el_dado(semilla, conjunto, cx, cz):
                    fuera.append([cx * 16 + dx, cz * 16 + dz])
        return fuera

    for rz in range(region_de(c0z, esp), region_de(c1z, esp) + 1):
        for rx in range(region_de(c0x, esp), region_de(c1x, esp) + 1):
            cx, cz = candidata(semilla, conjunto, rx, rz)
            bx, bz = cx * 16 + dx, cz * 16 + dz
            if not (x0 <= bx <= x1 and z0 <= bz <= z1):
                continue
            if not pasa_el_dado(semilla, conjunto, cx, cz):
                continue
            if not pasa_la_exclusion(semilla, conjunto, cx, cz):
                continue
            fuera.append([bx, bz])
    return fuera


# ══════════════════════════════════════════ el segundo paso: ¿le vale el bioma?
#
# La cuenta de arriba dice dónde PODRÍA ir cada conjunto. Minecraft, además,
# mira el bioma de ese sitio y solo la coloca si le vale: una pirámide necesita
# desierto, un monumento necesita océano profundo. Sin este paso, el mapa
# saldría con cinco veces más iconos de los que hay de verdad.
#
# Qué biomas le valen a cada estructura está EN EL JAR, en JSON, en
# `data/minecraft/worldgen/structure/<nombre>.json`, en el campo `biomes`. Suele
# ser una etiqueta (`#minecraft:has_structure/village_plains`) que a su vez
# vive en `data/minecraft/tags/worldgen/biome/…` y puede apuntar a otras
# etiquetas, así que se resuelve en cadena.
#
# Se lee del jar del servidor, no de una tabla mía: cuando salga la 26.3 y
# Mojang cambie dónde puede aparecer algo, esto se entera solo.

Y_SUPERFICIE = 128


def _resolver_etiqueta(z, dentro, nombre, visto=None):
    """Una etiqueta de biomas → el conjunto de biomas, siguiendo la cadena."""
    visto = visto if visto is not None else set()
    if nombre in visto:
        return set()                       # etiquetas que se citan entre sí
    visto.add(nombre)
    espacio, _, camino = nombre.partition(":")
    if not camino:
        espacio, camino = "minecraft", espacio
    ruta = "data/%s/tags/worldgen/biome/%s.json" % (espacio, camino)
    if ruta not in dentro:
        return set()
    fuera = set()
    for v in json.loads(z.read(ruta)).get("values", []):
        if isinstance(v, dict):
            v = v.get("id", "")
        if not isinstance(v, str) or not v:
            continue
        if v.startswith("#"):
            fuera |= _resolver_etiqueta(z, dentro, v[1:], visto)
        else:
            fuera.add(v if ":" in v else "minecraft:" + v)
    return fuera


def _altura_de(d):
    """A qué altura mira Minecraft el bioma de esa estructura.

    Los biomas son tridimensionales, así que esto NO es un detalle: preguntar a
    la altura equivocada descarta estructuras que sí están.

    Una ciudad antigua se coloca a y=-27 y su bioma (deep_dark) solo existe allí
    abajo; preguntando en la superficie no saldría ninguna.

    Y al revés, y es el fallo que tuvo esto durante un día: las aldeas llevan
    `start_height: 0`, pero ese 0 es un marcador de posición — llevan también
    `project_start_to_heightmap`, que significa «y ahora súbela hasta el suelo».
    El juego mira el bioma DESPUÉS de subirla. Tomándose el 0 al pie de la
    letra se preguntaba a y=0, donde muchas veces hay una cueva, y se perdían
    8 de cada 23 aldeas. Medido contra un servidor de verdad.
    """
    if d.get("project_start_to_heightmap"):
        return [Y_SUPERFICIE]
    h = d.get("start_height")
    if isinstance(h, dict):
        if "absolute" in h:
            return [int(h["absolute"])]
        lo = (h.get("min_inclusive") or {}).get("absolute")
        hi = (h.get("max_inclusive") or {}).get("absolute")
        if lo is not None and hi is not None:
            # `uniform` = el juego saca una altura AL AZAR de ese tramo y mira el
            # bioma AHÍ. Las cámaras de desafío van entre -40 y -20, y en esos
            # veinte bloques el bioma puede cambiar (a esa profundidad conviven
            # cuevas y oscuridad profunda). Preguntando solo por el medio se
            # escapaban 2 de cada 287; se pregunta por todo el tramo.
            lo, hi = int(lo), int(hi)
            paso = max(4, (hi - lo) // 4)
            alturas = list(range(lo, hi + 1, paso))
            if alturas[-1] != hi:
                alturas.append(hi)
            return alturas
    return [Y_SUPERFICIE]


# Los puntos donde se pregunta el bioma, respecto al CENTRO del chunk.
#
# El juego no mira el bioma en el chunk: lo mira en el CENTRO DE LA PRIMERA
# PIEZA de la construcción. Y esa pieza se coloca con un giro al azar, así que
# su centro cae a unos seis bloques de la esquina del chunk, hacia una de las
# cuatro diagonales — a veces incluso FUERA del chunk.
#
# Lo comprobé con una aldea concreta: en la esquina del chunk el bioma es
# `grove` (no vale para aldeas) y en el centro de la pieza, cinco bloques a la
# izquierda, es `meadow` (sí vale). El juego la coloca; yo la descartaba.
#
# Saber la pieza exacta haría falta cargar las plantillas del jar, que es otro
# mundo. En vez de eso se preguntan los cinco sitios donde puede caer y vale con
# que uno cuadre. Medido contra un servidor de verdad, sobre 27 aldeas:
#     solo la esquina       → 26 de 27, 3 fantasmas
#     esquina + 4 diagonales→ 27 de 27, 5 fantasmas   ← esto
# Dos fantasmas más a cambio de no perder ninguna.
_ESQUINA = (-8, -8)
_DIAGONALES = 6
PUNTOS_JIGSAW = [_ESQUINA,
                 (_ESQUINA[0] - _DIAGONALES, _ESQUINA[1] - _DIAGONALES),
                 (_ESQUINA[0] + _DIAGONALES, _ESQUINA[1] + _DIAGONALES),
                 (_ESQUINA[0] - _DIAGONALES, _ESQUINA[1] + _DIAGONALES),
                 (_ESQUINA[0] + _DIAGONALES, _ESQUINA[1] - _DIAGONALES)]
PUNTOS_CENTRO = [(0, 0)]


def _desplazamiento(d):
    """Dónde mira el juego el bioma, respecto al centro del chunk.

    Las de tipo `jigsaw` —aldeas, puestos, ciudades antiguas, cámaras— se
    montan a partir de una pieza girada al azar (ver arriba). Las demás usan el
    centro del chunk exacto, sin sorpresas.
    """
    return PUNTOS_JIGSAW if d.get("type") == "minecraft:jigsaw" else PUNTOS_CENTRO


ESTRUCTURAS = {}
BIOMAS_DIMENSION = {}          # 'overworld' → set de biomas de esa dimensión


def leer_estructuras(jar):
    """nombre → {'biomas': set, 'y': int}. Del jar, sin adivinar nada."""
    import zipfile
    fuera = {}
    with zipfile.ZipFile(jar) as z:
        dentro = set(z.namelist())
        for n in dentro:
            if not n.startswith("data/minecraft/worldgen/structure/") or \
               not n.endswith(".json"):
                continue
            try:
                d = json.loads(z.read(n))
            except Exception:
                continue
            b = d.get("biomes")
            if isinstance(b, str):
                biomas = _resolver_etiqueta(z, dentro, b[1:]) if b.startswith("#") \
                         else {b if ":" in b else "minecraft:" + b}
            elif isinstance(b, list):
                biomas = {x if ":" in x else "minecraft:" + x
                          for x in b if isinstance(x, str)}
            else:
                biomas = set()
            alturas = _altura_de(d)
            fuera[n.rsplit("/", 1)[-1][:-5]] = {"biomas": biomas,
                                                "y": alturas[len(alturas) // 2],
                                                "alturas": alturas,
                                                "puntos": _desplazamiento(d)}
        for dim in ("overworld", "nether", "end"):
            BIOMAS_DIMENSION[dim] = _resolver_etiqueta(z, dentro, "is_" + dim)
    return fuera


def conjuntos_de(dimension="overworld"):
    """Los conjuntos que pueden salir en esa dimensión.

    Sin esto, dibujar el mapa del overworld gastaría el 93% del trabajo
    preguntando el bioma de fósiles del Nether, que van cada 2 chunks: 131.000
    puntos para confirmar cero. Se descartan de golpe mirando si ALGÚN miembro
    del conjunto puede vivir en un bioma de esta dimensión.
    """
    suyos = BIOMAS_DIMENSION.get(dimension)
    if not suyos or not ESTRUCTURAS:
        return sorted(CONJUNTOS)
    fuera = []
    for c, datos in CONJUNTOS.items():
        for m in (datos[3] or [c]):
            info = ESTRUCTURAS.get(m)
            if info and info["biomas"] & suyos:
                fuera.append(c)
                break
    return sorted(fuera)


def cargar_estructuras(jar=None):
    global ESTRUCTURAS
    if jar and Path(jar).exists():
        leidas = leer_estructuras(jar)
        if leidas:
            ESTRUCTURAS = leidas
    return ESTRUCTURAS


def confirmar_en(srv, semilla, cajas, conjuntos=None, progreso=None):
    """Como `confirmar`, pero sobre una lista de rectángulos sueltos.

    Existe por un fallo que costó un `Killed`: para comparar contra el mundo de
    verdad se cogía la caja que abarca TODAS las regiones del disco. Si alguien
    se ha ido una vez a X = -2.500.000, esa caja mide millones de bloques por
    millones y calcular sus candidatas se come toda la memoria de la máquina.

    Con una lista de cajas se mira solo donde hay mundo guardado.
    """
    conjuntos = conjuntos or sorted(CONJUNTOS)
    vistas, fuera = set(), []
    for x0, z0, x1, z1 in cajas:
        for e in confirmar(srv, semilla, x0, z0, x1, z1, conjuntos, progreso):
            clave = (e["tipo"], e["x"], e["z"])
            if clave not in vistas:
                vistas.add(clave)
                fuera.append(e)
    return fuera


def confirmar(srv, semilla, x0, z0, x1, z1, conjuntos=None, progreso=None):
    """Candidatas → estructuras de verdad, preguntando el bioma de cada una.

    Devuelve [{'conjunto', 'tipo', 'x', 'z', 'bioma'}]. `tipo` es el miembro
    concreto: de un conjunto «villages» sale village_desert o village_snowy
    según el bioma, igual que en el juego, y así el icono es el correcto.

    Si no hay servicio de biomas devuelve las candidatas SIN confirmar, marcadas
    como tales — más vale un mapa que avisa de que está adivinando que un mapa
    vacío.
    """
    conjuntos = conjuntos or sorted(CONJUNTOS)
    if not ESTRUCTURAS:
        return [{"conjunto": c, "tipo": (CONJUNTOS[c][3] or [c])[0],
                 "x": x, "z": z, "bioma": None, "seguro": False}
                for c in conjuntos if c in CONJUNTOS
                for x, z in candidatas_en(semilla, c, x0, z0, x1, z1)]

    # Agrupadas por «dónde hay que preguntar»: altura y desplazamiento dentro
    # del chunk. Las de superficie caen todas en el mismo grupo, las
    # subterráneas en el suyo, y así son dos o tres viajes en vez de cien mil.
    def receta(m):
        i = ESTRUCTURAS.get(m) or {}
        return (tuple(i.get("alturas") or [i.get("y", Y_SUPERFICIE)]),
                tuple(i.get("puntos") or PUNTOS_CENTRO))

    grupos = {}
    candidatas = {}
    for c in conjuntos:
        if c not in CONJUNTOS:
            continue
        cs = candidatas_en(semilla, c, x0, z0, x1, z1)
        candidatas[c] = cs
        for r in {receta(m) for m in (CONJUNTOS[c][3] or [c])}:
            for x, z in cs:
                grupos.setdefault(r, []).append((x, z))

    hechas = 0
    total = sum(len(v) * len(r[0]) * len(r[1]) for r, v in grupos.items())
    biomas_de = {}                          # (receta, x, z) → conjunto de biomas
    for r, lista in grupos.items():
        alturas, puntos = r
        for y in alturas:
            for dx, dz in puntos:
                for i in range(0, len(lista), 2000):
                    trozo = lista[i:i + 2000]
                    nombres = srv.puntos([(x + dx, z + dz) for x, z in trozo], y=y)
                    for (x, z), b in zip(trozo, nombres):
                        biomas_de.setdefault((r, x, z), set()).add(b)
                    hechas += len(trozo)
                    if progreso:
                        progreso(hechas, total)

    fuera = []
    for c in conjuntos:
        if c not in CONJUNTOS:
            continue
        for x, z in candidatas[c]:
            for m in (CONJUNTOS[c][3] or [c]):
                info = ESTRUCTURAS.get(m)
                if not info:
                    continue
                vistos = biomas_de.get((receta(m), x, z)) or ()
                b = next((v for v in vistos if v in info["biomas"]), None)
                if b:
                    fuera.append({"conjunto": c, "tipo": m, "x": x, "z": z,
                                  "bioma": b, "seguro": True})
                    break
    return fuera


def main():
    if len(sys.argv) < 6:
        print(__doc__)
        print("Uso: estructuras.py <semilla> <x0> <z0> <x1> <z1> [tipo…]")
        print("     --confirmar   pregunta el bioma y descarta las que no valen")
        return 2
    semilla = int(sys.argv[1])
    x0, z0, x1, z1 = (int(a) for a in sys.argv[2:6])
    jar = os.environ.get("MC_JAR")
    jar = jar if jar and Path(jar).exists() else None
    cargar(jar)
    tipos = [a for a in sys.argv[6:] if not a.startswith("--")] or sorted(CONJUNTOS)

    if "--confirmar" in sys.argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import biomas
        cargar_estructuras(jar)
        srv = biomas.Servicio()
        if not srv.vivo():
            print("✘ el servicio de biomas no responde", file=sys.stderr)
            return 1
        if "--confirmar" in sys.argv and not [a for a in sys.argv[6:] if not a.startswith("--")]:
            tipos = conjuntos_de("overworld")
        import time
        t = time.time()
        halladas = confirmar(srv, semilla, x0, z0, x1, z1, tipos)
        cuenta = {}
        for e in halladas:
            cuenta[e["tipo"]] = cuenta.get(e["tipo"], 0) + 1
        candidatas = sum(len(candidatas_en(semilla, c, x0, z0, x1, z1))
                         for c in tipos if c in CONJUNTOS)
        print("%d candidatas → %d confirmadas en %.1f s"
              % (candidatas, len(halladas), time.time() - t), file=sys.stderr)
        for k in sorted(cuenta, key=lambda k: -cuenta[k]):
            print("  %-24s %d" % (k, cuenta[k]), file=sys.stderr)
        print(json.dumps({"semilla": semilla, "estructuras": halladas},
                         ensure_ascii=False))
        return 0

    fuera = {t: candidatas_en(semilla, t, x0, z0, x1, z1)
             for t in tipos if t in CONJUNTOS}
    print(json.dumps({"semilla": semilla, "candidatas": fuera,
                      "total": sum(len(v) for v in fuera.values())},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
