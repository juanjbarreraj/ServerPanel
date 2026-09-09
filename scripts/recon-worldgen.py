#!/usr/bin/env python3
"""
Reconocimiento: ¿se puede preguntarle al server.jar por los biomas?

POR QUÉ ESTE PASO EXISTE
------------------------
El plan para el mapa tipo Chunkbase es no reescribir el generador de mundos de
Minecraft, sino **usar el que ya tienes dentro de server.jar**. El problema es
que el jar de Mojang viene con los nombres internos revueltos: la clase que
decide los biomas no se llama `MultiNoiseBiomeSource`, se llama algo como `dqz`.

Mojang publica, para cada versión, el fichero de equivalencias. Esto lo baja, lo
lee y contesta a tres preguntas antes de escribir una sola línea de Java:

  1. ¿Está el jar donde creo, y descomprimido con sus librerías?
  2. ¿Se puede leer la semilla del mundo?
  3. ¿Existen en ESTA versión las clases y métodos que el programa va a
     necesitar, y cómo se llaman de verdad?

Si algo falla, falla aquí —en un script de Python que tarda segundos— y no en
un programa Java escrito a ciegas.

No toca nada: solo lee. Ni siquiera necesita el servidor parado.

Correr EN EL SERVIDOR:
    python3 ~/panel/scripts/recon-worldgen.py
"""
import json, os, re, sys, urllib.request, zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    import nbt
except Exception:
    nbt = None

MC = Path(os.environ.get("MC_DIR", Path.home() / "minecraft"))
MANIFIESTO = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"

# Lo que el programa de biomas va a necesitar. Los nombres son los OFICIALES de
# Mojang; lo que buscamos es a qué se llaman de verdad en el jar.
CLASES = [
    "net.minecraft.SharedConstants",
    "net.minecraft.server.Bootstrap",
    "net.minecraft.core.RegistryAccess",
    "net.minecraft.core.registries.Registries",
    "net.minecraft.data.registries.VanillaRegistries",
    "net.minecraft.world.level.biome.BiomeSource",
    "net.minecraft.world.level.biome.MultiNoiseBiomeSource",
    "net.minecraft.world.level.biome.MultiNoiseBiomeSourceParameterLists",
    "net.minecraft.world.level.biome.Climate",
    "net.minecraft.world.level.biome.Biome",
    "net.minecraft.world.level.biome.Biomes",
    "net.minecraft.world.level.levelgen.NoiseGeneratorSettings",
    "net.minecraft.world.level.levelgen.RandomState",
    "net.minecraft.world.level.levelgen.NoiseBasedChunkGenerator",
    "net.minecraft.world.level.levelgen.synth.NormalNoise",
    "net.minecraft.core.Holder",
    "net.minecraft.core.Registry",
    "net.minecraft.resources.ResourceKey",
    "net.minecraft.resources.ResourceLocation",
    # para la capa de estructuras, más adelante
    "net.minecraft.world.level.levelgen.structure.Structure",
    "net.minecraft.world.level.levelgen.structure.placement.RandomSpreadStructurePlacement",
    "net.minecraft.world.level.levelgen.structure.placement.StructurePlacement",
]

# métodos concretos que hacen falta, por clase oficial
METODOS = {
    "net.minecraft.SharedConstants": ["tryDetectVersion"],
    "net.minecraft.server.Bootstrap": ["bootStrap"],
    "net.minecraft.data.registries.VanillaRegistries": ["createLookup"],
    "net.minecraft.world.level.biome.BiomeSource": ["getNoiseBiome"],
    "net.minecraft.world.level.biome.MultiNoiseBiomeSource": ["createFromPreset",
                                                              "getNoiseBiome"],
    "net.minecraft.world.level.levelgen.RandomState": ["create", "sampler"],
    "net.minecraft.core.Holder": ["value", "unwrapKey"],
    "net.minecraft.core.Registry": ["get", "getHolderOrThrow", "getOrThrow"],
}


def titulo(t):
    print("\n" + "─" * 66)
    print(t)
    print("─" * 66)


def bajar(url, timeout=90):
    pet = urllib.request.Request(url, headers={"User-Agent": "panel-califree"})
    with urllib.request.urlopen(pet, timeout=timeout) as r:
        return r.read()


# ─────────────────────────────────────────────────── 1. qué hay en el disco
def mirar_disco():
    titulo("1 · lo que hay en el disco")
    jar = MC / "server.jar"
    print("  MC_DIR:                %s" % MC)
    print("  server.jar:            %s" % ("sí, %.0f MB" % (jar.stat().st_size / 2**20)
                                           if jar.exists() else "NO ESTÁ"))
    vers = sorted((MC / "versions").glob("*/server-*.jar")) if (MC / "versions").is_dir() else []
    for v in vers:
        print("  versions/:             %s  (%.0f MB)" % (v.name, v.stat().st_size / 2**20))
    libs = list((MC / "libraries").rglob("*.jar")) if (MC / "libraries").is_dir() else []
    print("  libraries/:            %d jar%s" % (len(libs), "" if len(libs) == 1 else "s"))
    if not vers:
        print("  ⚠ sin versions/ no hay classes que cargar: el jar no se ha descomprimido")
    if not libs:
        print("  ⚠ sin libraries/ el programa no podrá arrancar Minecraft")
    version = None
    if vers:
        version = re.search(r"server-(.+)\.jar$", vers[-1].name).group(1)
    print("  versión detectada:     %s" % (version or "?"))

    # ¿Sigue ofuscado? Si Mojang dejó de publicar equivalencias porque ya no
    # ofusca, el plan de los biomas se vuelve MUCHO más fácil, no imposible.
    if vers:
        try:
            with zipfile.ZipFile(vers[-1]) as z:
                clases = [n for n in z.namelist() if n.endswith(".class")]
            legibles = [c for c in clases if c.startswith("net/minecraft/")]
            print("  clases en el jar:      %d  (%d con nombre legible net/minecraft/…)"
                  % (len(clases), len(legibles)))
            print("  muestra:")
            for c in sorted(clases)[:6]:
                print("      %s" % c)
            if legibles:
                print("  muestra de net/minecraft/:")
                for c in sorted(legibles)[:6]:
                    print("      %s" % c)
            pct = 100.0 * len(legibles) / max(1, len(clases))
            if pct > 50:
                print("  ✅ EL JAR NO ESTÁ OFUSCADO (%.0f%% con nombre real): no hacen falta"
                      % pct)
                print("     equivalencias. El plan de los biomas se simplifica muchísimo.")
            else:
                print("  ⚠ el jar SÍ está ofuscado (solo %.0f%% legible)" % pct)
        except Exception as e:
            print("  ⚠ no pude mirar dentro del jar: %s" % e)
    return version, (vers[-1] if vers else None), len(libs)


# ─────────────────────────────────────────────────────── 2. la semilla
def buscar_clave(items, nombre, ruta="Data", profundidad=0):
    """Busca una clave por nombre por todo el árbol NBT."""
    if profundidad > 4:
        return []
    fuera = []
    for k, t in items:
        kk = k.decode("utf-8", "replace")
        aqui = ruta + "." + kk
        if kk.lower() == nombre:
            fuera.append((aqui, t.v))
        if t.t == nbt.TAG_COMPOUND and isinstance(t.v, list):
            fuera += buscar_clave(t.v, nombre, aqui, profundidad + 1)
    return fuera


def leer_semilla():
    titulo("2 · la semilla del mundo")
    f = MC / "world" / "level.dat"
    if nbt is None:
        print("  ⚠ no encuentro nbt.py junto al panel — no puedo leer level.dat")
        return None
    if not f.exists():
        print("  ⚠ no existe %s" % f)
        return None
    try:
        _, raiz, _ = nbt.load(str(f))
        datos = nbt.cget(raiz.v, "Data")
        if datos is None:
            print("  ⚠ no hay compuesto «Data». Raíz: %s" % nbt.ckeys(raiz.v)[:30])
            return None

        ver = nbt.cget(datos.v, "Version")
        vnom = nbt.cget(ver.v, "Name") if ver is not None else None
        print("  versión del mundo:     %s" % (vnom.v.decode() if vnom is not None else "?"))

        # buscar «seed» esté donde esté: en 1.16 se movió a WorldGenSettings y
        # puede haberse vuelto a mover
        hallazgos = buscar_clave(datos.v, "seed")
        for r in ("randomseed", "worldseed"):
            hallazgos += buscar_clave(datos.v, r)
        if hallazgos:
            for ruta, val in hallazgos:
                print("  semilla:               %s   (en %s)" % (val, ruta))
            print("  (compárala con la que tienes puesta en Chunkbase)")
            return hallazgos[0][1]

        # No está. Enseñar el mapa del terreno para saber dónde mirar.
        print("  ✘ no encuentro ninguna clave «seed». Esto es lo que SÍ hay:")
        print()
        print("    Data: %s" % ", ".join(nbt.ckeys(datos.v)))
        ws = nbt.cget(datos.v, "WorldGenSettings")
        if ws is not None and isinstance(ws.v, list):
            print()
            print("    Data.WorldGenSettings: %s" % ", ".join(nbt.ckeys(ws.v)))
            for k, t in ws.v:
                kk = k.decode("utf-8", "replace")
                if t.t == nbt.TAG_COMPOUND and isinstance(t.v, list):
                    print("      .%s: %s" % (kk, ", ".join(nbt.ckeys(t.v))[:200]))
                elif t.t in (nbt.TAG_LONG, nbt.TAG_INT, nbt.TAG_STRING):
                    v = t.v.decode("utf-8", "replace") if isinstance(t.v, bytes) else t.v
                    print("      .%s = %r" % (kk, v))
        else:
            print("    (no hay Data.WorldGenSettings)")
        print()
        print("  Mientras tanto la semilla se puede sacar con el comando /seed en la")
        print("  consola del servidor — es vanilla y no hace falta parar nada.")
        return None
    except Exception as e:
        print("  ⚠ no pude leerlo: %s" % e)
        return None


# ────────────────────────────────────────── 3. las equivalencias de nombres
def parsear_mappings(texto):
    """El fichero de Mojang va en formato ProGuard, con el nombre OFICIAL a la
    izquierda y el revuelto a la derecha:

        net.minecraft.SharedConstants -> net:
            void tryDetectVersion() -> a

    Se devuelve {clase oficial: (revuelta, {método: [(firma, revuelto), …]})}.
    """
    clases, actual = {}, None
    for linea in texto.splitlines():
        if not linea or linea.startswith("#"):
            continue
        if not linea.startswith(" ") and linea.rstrip().endswith(":"):
            izq, _, der = linea.rstrip()[:-1].partition(" -> ")
            actual = izq.strip()
            clases[actual] = [der.strip(), {}]
            continue
        if actual and linea.startswith(" ") and " -> " in linea:
            cuerpo, _, obf = linea.strip().partition(" -> ")
            cuerpo = re.sub(r"^\d+:\d+:", "", cuerpo)       # números de línea
            m = re.match(r"(\S+)\s+([^(\s]+)\((.*)\)$", cuerpo)
            if m:
                clases[actual][1].setdefault(m.group(2), []).append(
                    ("(%s) → %s" % (m.group(3), m.group(1)), obf.strip()))
    return clases


def mirar_nombres(version):
    titulo("3 · ¿existen las clases que hacen falta, y cómo se llaman?")
    if not version:
        print("  ⚠ sin versión no puedo pedir las equivalencias")
        return
    try:
        man = json.loads(bajar(MANIFIESTO))
        url = next((v["url"] for v in man["versions"] if v["id"] == version), None)
        if not url:
            print("  ⚠ Mojang no lista la versión %s" % version)
            return
        vjson = json.loads(bajar(url))
        desc = vjson.get("downloads", {})
        print("  descargas que publica Mojang para la %s:" % version)
        for k in sorted(desc):
            print("      %-22s %s" % (k, (desc[k].get("url") or "")[:96]))
        print("  (claves del JSON de versión: %s)" % ", ".join(sorted(vjson.keys()))[:200])
        # la clave se ha llamado de varias formas; se prueban todas
        m = None
        for clave in ("server_mappings", "server_mappings_official", "mappings_server",
                      "server_map", "serverMappings"):
            if desc.get(clave):
                m = desc[clave]
                print("  ✔ encontradas en «%s»" % clave)
                break
        if not m:
            print()
            print("  ✘ Ninguna de esas claves está. Si arriba no aparece nada que suene a")
            print("    «mappings», Mojang ha dejado de publicarlas para esta versión y el")
            print("    plan de los biomas hay que replantearlo.")
            return
        print("  equivalencias:         %.1f MB, bajando…" % (m.get("size", 0) / 2**20))
        texto = bajar(m["url"], timeout=300).decode("utf-8", "replace")
    except Exception as e:
        print("  ⚠ no pude bajarlas: %s" % e)
        return

    clases = parsear_mappings(texto)
    print("  clases en el fichero:  %d" % len(clases))
    faltan = []
    print()
    for c in CLASES:
        if c in clases:
            print("  ✔ %-64s → %s" % (c.split("net.minecraft.")[-1], clases[c][0]))
        else:
            faltan.append(c)
            print("  ✘ %-64s   NO EXISTE" % c.split("net.minecraft.")[-1])

    print()
    print("  Métodos que hacen falta (nombre oficial → revuelto):")
    for c, ms in METODOS.items():
        if c not in clases:
            continue
        corto = c.split("net.minecraft.")[-1]
        for nombre in ms:
            firmas = clases[c][1].get(nombre)
            if not firmas:
                print("    ✘ %s.%s  NO ESTÁ" % (corto, nombre))
                faltan.append("%s.%s" % (c, nombre))
                continue
            for firma, obf in firmas:
                print("    ✔ %s.%s%s  →  %s" % (corto, nombre, firma, obf))

    print()
    if faltan:
        print("  ⚠ FALTAN %d cosas. Pégame esta salida entera: con los nombres que sí" % len(faltan))
        print("    están puedo buscar cómo se llaman ahora las que no.")
    else:
        print("  ✅ Está todo. Con estos nombres puedo escribir el programa de biomas")
        print("     sabiendo lo que hago, en vez de adivinando.")


def mirar_conjuntos(jar):
    """Los datos de colocación de estructuras están en el jar EN JSON Y SIN
    OFUSCAR. O sea que esta parte del mapa no necesita nada de lo demás."""
    titulo("4 · dónde coloca Minecraft cada estructura (esto sale del jar, en claro)")
    if not jar:
        print("  ⚠ sin jar no puedo leerlo")
        return
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import importlib.util as iu
        sp = iu.spec_from_file_location("est", Path(__file__).resolve().parent / "estructuras.py")
        est = iu.module_from_spec(sp); sp.loader.exec_module(est)
        conj = est.leer_del_jar(jar)
    except Exception as e:
        print("  ⚠ no pude leerlos: %s" % e)
        return
    if not conj:
        print("  ✘ no encontré data/minecraft/worldgen/structure_set/ dentro del jar")
        print("    (esto no hunde el plan, pero tendría que usar la tabla de respaldo)")
        return
    print("  %d conjuntos leídos del jar de TU versión:" % len(conj))
    print()
    print("  %-24s %8s %8s %14s  %s" % ("conjunto", "espaciado", "separac.", "sal", "estructuras"))
    for n in sorted(conj):
        esp, sep, sal, miembros = conj[n]
        print("  %-24s %8d %8d %14d  %s" % (n, esp, sep, sal, ", ".join(miembros[:4]) +
                                            ("…" if len(miembros) > 4 else "")))
    print()
    print("  ✅ Con esto la capa de estructuras ya es exacta y para el mundo infinito,")
    print("     sin depender de nada de lo de arriba.")


def mirar_mundo():
    """La 26.x reorganizó la carpeta del mundo: las dimensiones se fueron a
    `dimensions/`, los jugadores a `players/`, y los ajustes de generación —con
    la semilla— a algún sitio que hay que encontrar."""
    titulo("5 · cómo está montada la carpeta del mundo")
    w = MC / "world"
    if not w.is_dir():
        print("  ⚠ no existe %s" % w)
        return
    for sub in ("dimensions", "players", "data", "datapacks"):
        d = w / sub
        if not d.is_dir():
            print("  %-12s NO EXISTE" % (sub + "/"))
            continue
        hijos = sorted(d.iterdir())
        print("  %-12s %d entradas" % (sub + "/", len(hijos)))
        for h in hijos[:12]:
            marca = "/" if h.is_dir() else ""
            n = len(list(h.iterdir())) if h.is_dir() else h.stat().st_size
            print("      %-40s %s" % (h.name + marca,
                                      ("%d dentro" % n) if h.is_dir() else ("%d bytes" % n)))
            if h.is_dir():
                for n2 in sorted(h.iterdir())[:8]:
                    print("          %s%s" % (n2.name, "/" if n2.is_dir() else ""))
        if len(hijos) > 12:
            print("      … y %d más" % (len(hijos) - 12))

    # cualquier fichero suelto (no carpeta) por si la semilla vive ahí
    print()
    print("  Ficheros sueltos en world/ y en world/dimensions/**:")
    for f in sorted(w.glob("*")):
        if f.is_file():
            print("      world/%-30s %d bytes" % (f.name, f.stat().st_size))
    for f in sorted((w / "dimensions").rglob("*")) if (w / "dimensions").is_dir() else []:
        if f.is_file() and f.suffix in (".dat", ".json", ".nbt", ".txt"):
            print("      %-36s %d bytes" % (str(f.relative_to(w)), f.stat().st_size))


def main():
    print("Reconocimiento del generador de mundos — no toca nada, solo lee.")
    version, jar, libs = mirar_disco()
    leer_semilla()
    mirar_nombres(version)
    mirar_conjuntos(jar)
    mirar_mundo()
    titulo("listo")
    print("Pega TODA esta salida en el chat.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
