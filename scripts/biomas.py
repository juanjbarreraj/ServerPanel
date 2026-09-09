#!/usr/bin/env python3
"""
Los colores del mapa de biomas y el dibujo de los azulejos.

QUIÉN HACE QUÉ
--------------
El programa de Java (scripts/Biomas.java) pregunta al generador de Minecraft y
devuelve UN NÚMERO por punto. Esto de aquí le pone el color y lo convierte en
una imagen. La frontera está ahí a propósito: en el servidor no hay compilador
de Java, así que cambiar un color no puede costar una recompilación.

CÓMO SE DIBUJA
--------------
El servicio devuelve un byte por píxel y la imagen es de paleta (modo "P" de
Pillow), así que los bytes SON la imagen: no hay ni un bucle en Python por
píxel. Un azulejo de 256×256 se arma en microsegundos; lo que cuesta es que
Minecraft calcule los biomas.

EL CACHÉ
--------
Un azulejo depende solo de la semilla y de cómo reparte los biomas el
generador, y ninguna de las dos cambia sola. Se guardan en
~/minecraft/mapa-biomas/<semilla>-<huella>/ y no caducan.

La huella es un resumen del propio generador (ver Biomas.java): al cambiar de
mundo cambia la semilla, y si una versión nueva de Minecraft cambiase el reparto
de biomas cambiaría la huella — en los dos casos se dibuja de nuevo solo, y
volver atrás reencuentra el mapa ya hecho en vez de rehacerlo.
"""
import io
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

try:
    from PIL import Image
except ImportError:                                   # pragma: no cover
    Image = None

SERVICIO = os.environ.get("BIOMAS_URL", "http://127.0.0.1:25580")
RAIZ_CACHE = Path(os.environ.get("MC_DIR", Path.home() / "minecraft")) / "mapa-biomas"

TAM = 256          # lado del azulejo, en píxeles
NIVELES = [4, 8, 16, 32, 64, 128, 256, 512]   # bloques por píxel
# 4 es el tope útil: el mundo guarda un bioma por celda de 4×4 bloques, así que
# pedir más detalle solo repetiría el mismo valor cuatro veces.

# ───────────────────────────────────────────────────────────────── la paleta
# Los colores de AMIDST, que son los que usa Chunkbase. Cada bioma tiene que
# distinguirse del de al lado de un vistazo; por eso los océanos van del azul
# oscuro al claro por temperatura y las montañas nevadas casi al blanco.
PALETA = {
    # agua
    "ocean":                     (0, 0, 112),
    "deep_ocean":                (0, 0, 48),
    "cold_ocean":                (32, 32, 112),
    "deep_cold_ocean":           (32, 32, 56),
    "frozen_ocean":              (112, 112, 214),
    "deep_frozen_ocean":         (64, 64, 144),
    "lukewarm_ocean":            (0, 0, 144),
    "deep_lukewarm_ocean":       (0, 0, 64),
    "warm_ocean":                (0, 0, 172),
    "river":                     (0, 0, 255),
    "frozen_river":              (160, 160, 255),
    # orilla
    "beach":                     (250, 222, 85),
    "snowy_beach":               (250, 240, 192),
    "stony_shore":               (162, 162, 132),
    # llano
    "plains":                    (141, 179, 96),
    "sunflower_plains":          (181, 219, 136),
    "snowy_plains":              (255, 255, 255),
    "ice_spikes":                (180, 220, 220),
    "desert":                    (250, 148, 24),
    "savanna":                   (189, 178, 95),
    "savanna_plateau":           (167, 157, 100),
    "windswept_savanna":         (229, 218, 135),
    "meadow":                    (96, 164, 69),
    "mushroom_fields":           (255, 0, 255),
    # bosque
    "forest":                    (5, 102, 33),
    "flower_forest":             (45, 142, 73),
    "birch_forest":              (48, 116, 68),
    "old_growth_birch_forest":   (88, 156, 108),
    "dark_forest":               (64, 81, 26),
    "pale_garden":               (117, 133, 122),
    "taiga":                     (11, 102, 89),
    "snowy_taiga":               (49, 85, 74),
    "old_growth_pine_taiga":     (89, 102, 81),
    "old_growth_spruce_taiga":   (129, 142, 121),
    "cherry_grove":              (255, 183, 197),
    "windswept_forest":          (88, 118, 85),
    # selva y pantano
    "jungle":                    (83, 123, 9),
    "sparse_jungle":             (98, 139, 23),
    "bamboo_jungle":             (118, 142, 20),
    "swamp":                     (7, 249, 178),
    "mangrove_swamp":            (68, 121, 105),
    # tierras rojas
    "badlands":                  (217, 69, 21),
    "eroded_badlands":           (255, 109, 61),
    "wooded_badlands":           (176, 151, 101),
    # montaña
    "grove":                     (71, 114, 108),
    "snowy_slopes":              (196, 216, 216),
    "jagged_peaks":              (220, 220, 220),
    "frozen_peaks":              (176, 179, 206),
    "stony_peaks":               (123, 143, 116),
    "windswept_hills":           (96, 96, 96),
    "windswept_gravelly_hills":  (136, 136, 136),
    # cueva (no salen a y=128, pero por si se baja la altura)
    "dripstone_caves":           (110, 90, 70),
    "lush_caves":                (66, 130, 40),
    "sulfur_caves":              (198, 186, 86),
    "deep_dark":                 (20, 20, 30),
    # nether
    "nether_wastes":             (191, 59, 59),
    "soul_sand_valley":          (94, 56, 48),
    "crimson_forest":            (221, 8, 8),
    "warped_forest":             (73, 144, 123),
    "basalt_deltas":             (100, 95, 100),
    # end
    "the_end":                   (128, 128, 160),
    "end_highlands":             (200, 200, 160),
    "end_midlands":              (176, 176, 128),
    "small_end_islands":         (75, 75, 110),
    "end_barrens":               (149, 149, 189),
    "the_void":                  (0, 0, 0),
}

# Rosa chillón. Si aparece en el mapa es que salió un bioma nuevo que no está
# en la tabla de arriba — se ve al instante en vez de disimularse en un gris.
FALTA = (255, 0, 128)

# Nombres bonitos para la leyenda de la pantalla.
BONITO = {
    "badlands": "Tierras rojas", "bamboo_jungle": "Selva de bambú",
    "basalt_deltas": "Deltas de basalto", "beach": "Playa",
    "birch_forest": "Bosque de abedules", "cherry_grove": "Arboleda de cerezos",
    "cold_ocean": "Océano frío", "crimson_forest": "Bosque carmesí",
    "dark_forest": "Bosque oscuro", "deep_cold_ocean": "Océano frío profundo",
    "deep_dark": "Oscuridad profunda", "deep_frozen_ocean": "Océano helado profundo",
    "deep_lukewarm_ocean": "Océano templado profundo", "deep_ocean": "Océano profundo",
    "desert": "Desierto", "dripstone_caves": "Cuevas de estalactitas",
    "end_barrens": "End yermo", "end_highlands": "End alto",
    "end_midlands": "End medio", "eroded_badlands": "Tierras rojas erosionadas",
    "flower_forest": "Bosque florido", "forest": "Bosque",
    "frozen_ocean": "Océano helado", "frozen_peaks": "Picos helados",
    "frozen_river": "Río helado", "grove": "Arboleda",
    "ice_spikes": "Picos de hielo", "jagged_peaks": "Picos escarpados",
    "jungle": "Selva", "lukewarm_ocean": "Océano templado",
    "lush_caves": "Cuevas frondosas", "mangrove_swamp": "Manglar",
    "meadow": "Pradera", "mushroom_fields": "Campos de setas",
    "nether_wastes": "Yermo del Nether", "ocean": "Océano",
    "old_growth_birch_forest": "Abedular viejo",
    "old_growth_pine_taiga": "Taiga de pinos vieja",
    "old_growth_spruce_taiga": "Taiga de abetos vieja",
    "pale_garden": "Jardín pálido", "plains": "Llanura", "river": "Río",
    "savanna": "Sabana", "savanna_plateau": "Meseta de sabana",
    "small_end_islands": "Islas del End", "snowy_beach": "Playa nevada",
    "snowy_plains": "Llanura nevada", "snowy_slopes": "Laderas nevadas",
    "snowy_taiga": "Taiga nevada", "soul_sand_valley": "Valle de arena de almas",
    "sparse_jungle": "Selva rala", "stony_peaks": "Picos pedregosos",
    "stony_shore": "Costa pedregosa", "sulfur_caves": "Cuevas de azufre",
    "sunflower_plains": "Llanura de girasoles", "swamp": "Pantano",
    "taiga": "Taiga", "the_end": "El End", "the_void": "El vacío",
    "warm_ocean": "Océano cálido", "warped_forest": "Bosque deformado",
    "windswept_forest": "Bosque azotado por el viento",
    "windswept_gravelly_hills": "Colinas de grava azotadas",
    "windswept_hills": "Colinas azotadas por el viento",
    "windswept_savanna": "Sabana azotada por el viento",
    "wooded_badlands": "Tierras rojas boscosas",
}


def corto(nombre):
    return nombre.split(":")[-1]


def bonito(nombre):
    c = corto(nombre)
    return BONITO.get(c, c.replace("_", " ").capitalize())


def color(nombre):
    return PALETA.get(corto(nombre), FALTA)


# ─────────────────────────────────────────────────────────── el servicio Java
class NoDisponible(RuntimeError):
    """El servicio de biomas no está levantado."""


class Servicio:
    def __init__(self, url=SERVICIO, espera=60):
        self.url = url.rstrip("/")
        self.espera = espera
        self._leyenda = None
        self._paleta = None
        self._salud = None

    def _pedir(self, ruta, espera=None):
        try:
            with urllib.request.urlopen(self.url + ruta, timeout=espera or self.espera) as r:
                return r.read()
        except (urllib.error.URLError, OSError) as e:
            raise NoDisponible("no responde el servicio de biomas: %s" % e)

    def salud(self):
        if self._salud is None:
            self._salud = json.loads(self._pedir("/salud", espera=5))
        return self._salud

    def vivo(self):
        try:
            return bool(self.salud().get("ok"))
        except NoDisponible:
            return False

    def leyenda(self):
        """{numero: 'minecraft:plains'} — fija, se pide una vez."""
        if self._leyenda is None:
            cruda = json.loads(self._pedir("/leyenda", espera=10))
            self._leyenda = {int(k): v for k, v in cruda.items()}
        return self._leyenda

    def paleta_plana(self):
        """Los 256 colores seguidos, como los quiere Pillow."""
        if self._paleta is None:
            ley = self.leyenda()
            fuera = bytearray(768)
            for i in range(256):
                r, g, b = color(ley[i]) if i in ley else FALTA
                fuera[i * 3:i * 3 + 3] = bytes((r, g, b))
            self._paleta = bytes(fuera)
        return self._paleta

    @staticmethod
    def _y(y):
        """`suelo` = a ras de suelo, la altura del terreno en ese punto.

        Es lo que hace falta para las estructuras de superficie: el juego las
        sube hasta el suelo y mira el bioma AHÍ, no a una altura fija.
        """
        return "" if y is None else ("&y=suelo" if y == "suelo" else "&y=%d" % int(y))

    def bioma(self, x, z, y=None):
        return json.loads(self._pedir("/bioma?x=%d&z=%d%s"
                                      % (int(x), int(z), self._y(y)), espera=10))

    def cuadro(self, x, z, n, paso, y=None):
        return self._pedir("/cuadro?x=%d&z=%d&n=%d&paso=%d%s"
                           % (int(x), int(z), int(n), int(paso), self._y(y)))

    def puntos(self, pares, y=None):
        """Muchos sitios de golpe → una lista de nombres de bioma."""
        ley = self.leyenda()
        fuera = []
        # La consulta va en la URL, así que se trocea para no pasarse de largo.
        for i in range(0, len(pares), 400):
            trozo = pares[i:i + 400]
            r = ("/puntos?p=" + ";".join("%d,%d" % (int(a), int(b)) for a, b in trozo)
                 + self._y(y))
            fuera.extend(ley.get(b, "?") for b in self._pedir(r))
        return fuera


    def alturas(self, pares):
        """La altura del suelo en muchos sitios de golpe."""
        import struct
        fuera = []
        for i in range(0, len(pares), 400):
            trozo = pares[i:i + 400]
            crudo = self._pedir("/alturas?p="
                                + ";".join("%d,%d" % (int(a), int(b)) for a, b in trozo))
            fuera.extend(struct.unpack(">%di" % len(trozo), crudo))
        return fuera


# ────────────────────────────────────────────────────────────── los azulejos
def carpeta_cache(mundo, bpp):
    return RAIZ_CACHE / str(mundo) / str(bpp)


def nombre_mundo(srv):
    """La carpeta donde van los azulejos de ESTE mundo con ESTE generador.

    Lleva la semilla y la huella del generador. Si Minecraft cambiase de versión
    y con ella el reparto de biomas, la huella cambia y los azulejos se dibujan
    de nuevo — en vez de seguir enseñando, tan tranquilos, el mundo de antes.
    """
    s = srv.salud()
    h = s.get("huella")
    return "%s-%s" % (s["semilla"], h) if h else str(s["semilla"])


def azulejo(srv, bpp, tx, tz, y=None, cachear=True):
    """PNG del azulejo (tx, tz) al detalle `bpp` bloques por píxel."""
    if Image is None:
        raise RuntimeError("falta Pillow")
    destino = carpeta_cache(nombre_mundo(srv), bpp) / ("%d_%d.png" % (tx, tz))
    if cachear and destino.is_file():
        return destino.read_bytes()

    datos = srv.cuadro(tx * TAM * bpp, tz * TAM * bpp, TAM, bpp, y)
    img = Image.frombytes("P", (TAM, TAM), bytes(datos))
    img.putpalette(srv.paleta_plana())
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    crudo = buf.getvalue()
    if cachear:
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(".part")
        tmp.write_bytes(crudo)
        tmp.replace(destino)                 # que nadie lea un PNG a medias
    return crudo


def region(srv, x0, z0, x1, z1, bpp, y=None):
    """Un rectángulo de mundo en una sola imagen. Para pruebas y capturas."""
    if Image is None:
        raise RuntimeError("falta Pillow")
    an = max(1, (x1 - x0) // bpp)
    al = max(1, (z1 - z0) // bpp)
    lado = max(an, al)
    datos = srv.cuadro(x0, z0, lado, bpp, y)
    img = Image.frombytes("P", (lado, lado), bytes(datos))
    img.putpalette(srv.paleta_plana())
    return img.crop((0, 0, an, al))


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="dibuja un trozo del mapa de biomas")
    p.add_argument("--x0", type=int, default=-4096)
    p.add_argument("--z0", type=int, default=-4096)
    p.add_argument("--x1", type=int, default=4096)
    p.add_argument("--z1", type=int, default=4096)
    p.add_argument("--bpp", type=int, default=16, help="bloques por píxel")
    p.add_argument("--y", type=int, default=None)
    p.add_argument("--salida", default="biomas.png")
    p.add_argument("--faltantes", action="store_true",
                   help="lista los biomas sin color en la tabla")
    a = p.parse_args()

    srv = Servicio()
    if not srv.vivo():
        print("✘ el servicio de biomas no responde en", srv.url)
        return 1
    print("semilla %s · y=%s · %d biomas"
          % (srv.salud()["semilla"], srv.salud()["y"], len(srv.leyenda())))

    sin = [v for v in srv.leyenda().values() if corto(v) not in PALETA]
    if sin:
        print("⚠ sin color:", ", ".join(sin))
    elif a.faltantes:
        print("✔ todos los biomas tienen color")

    import time
    t = time.time()
    img = region(srv, a.x0, a.z0, a.x1, a.z1, a.bpp, a.y)
    img.save(a.salida)
    n = img.width * img.height
    print("%dx%d px (%d bloques por píxel) en %.1f s · %d puntos/s"
          % (img.width, img.height, a.bpp, time.time() - t, n / max(0.001, time.time() - t)))
    print("→", a.salida)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
