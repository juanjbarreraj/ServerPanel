#!/usr/bin/env python3
"""
Lee las firmas reales de las clases del server.jar.

POR QUÉ
-------
El jar de la 26.2 no está ofuscado, así que se puede escribir un programa Java
normal contra él. Lo que no sé es cómo es la API EXACTA de esta versión: los
nombres de los métodos de generación de mundos cambian de una a otra, y
escribir a ciegas significa una vuelta entera —editar, mandarte el fichero,
compilar, pegarme el error— por cada método que no acierte.

Esto lee los `.class` directamente (formato de fichero de clase de Java, no hace
falta ni javac) y escupe los métodos públicos con su firma. Con eso el Java se
escribe una vez y bien.

Correr EN EL SERVIDOR:
    python3 ~/panel/scripts/api-jar.py
    python3 ~/panel/scripts/api-jar.py net.minecraft.world.level.biome.Climate
    python3 ~/panel/scripts/api-jar.py --paquete net.minecraft.resources
"""
import os, re, struct, sys, zipfile
from pathlib import Path

MC = Path(os.environ.get("MC_DIR", Path.home() / "minecraft"))

# lo que hace falta para montar el generador de biomas
INTERESAN = [
    "net.minecraft.SharedConstants",
    "net.minecraft.server.Bootstrap",
    "net.minecraft.data.registries.VanillaRegistries",
    "net.minecraft.core.RegistryAccess",
    "net.minecraft.core.Registry",
    "net.minecraft.core.Holder",
    "net.minecraft.core.HolderGetter",
    "net.minecraft.world.level.biome.MultiNoiseBiomeSource",
    "net.minecraft.world.level.biome.MultiNoiseBiomeSourceParameterLists",
    "net.minecraft.world.level.biome.BiomeSource",
    "net.minecraft.world.level.biome.Climate",
    "net.minecraft.world.level.levelgen.RandomState",
    "net.minecraft.world.level.levelgen.NoiseGeneratorSettings",
]

# ───────────────────────────────────────────── lector de ficheros .class
ETIQUETAS_TAM = {3: 4, 4: 4, 5: 8, 6: 8, 7: 2, 8: 2, 9: 4, 10: 4, 11: 4,
                 12: 4, 16: 2, 19: 2, 20: 2, 17: 4, 18: 4}


def leer_clase(crudo):
    """Devuelve (nombre, super, [(flags, nombre, descriptor)], [campos])."""
    if crudo[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("no es un .class")
    p = 10                                    # magic(4) + minor(2) + major(2) + count(2)
    n_const = struct.unpack_from(">H", crudo, 8)[0]
    const = {}
    i = 1
    while i < n_const:
        etiqueta = crudo[p]; p += 1
        if etiqueta == 1:
            largo = struct.unpack_from(">H", crudo, p)[0]; p += 2
            const[i] = crudo[p:p + largo].decode("utf-8", "replace"); p += largo
        elif etiqueta == 15:
            p += 3
        else:
            tam = ETIQUETAS_TAM.get(etiqueta)
            if tam is None:
                raise ValueError("etiqueta desconocida %d" % etiqueta)
            if etiqueta == 7:                 # Class → guarda a qué Utf8 apunta
                const[i] = ("clase", struct.unpack_from(">H", crudo, p)[0])
            p += tam
        # Long y Double ocupan DOS huecos de la tabla; saltárselo desalinea todo
        i += 2 if etiqueta in (5, 6) else 1

    def utf(idx):
        v = const.get(idx)
        if isinstance(v, tuple) and v[0] == "clase":
            return const.get(v[1], "?")
        return v if isinstance(v, str) else "?"

    p += 2                                     # access_flags
    yo = utf(struct.unpack_from(">H", crudo, p)[0]); p += 2
    padre = utf(struct.unpack_from(">H", crudo, p)[0]); p += 2
    n_int = struct.unpack_from(">H", crudo, p)[0]; p += 2 + 2 * n_int

    def saltar_atributos(p):
        n = struct.unpack_from(">H", crudo, p)[0]; p += 2
        for _ in range(n):
            largo = struct.unpack_from(">I", crudo, p + 2)[0]
            p += 6 + largo
        return p

    campos = []
    n = struct.unpack_from(">H", crudo, p)[0]; p += 2
    for _ in range(n):
        fl, ni, di = struct.unpack_from(">HHH", crudo, p); p += 6
        campos.append((fl, utf(ni), utf(di)))
        p = saltar_atributos(p)

    metodos = []
    n = struct.unpack_from(">H", crudo, p)[0]; p += 2
    for _ in range(n):
        fl, ni, di = struct.unpack_from(">HHH", crudo, p); p += 6
        metodos.append((fl, utf(ni), utf(di)))
        p = saltar_atributos(p)
    return yo, padre, metodos, campos


# ─────────────────────────────────────────── descriptores a algo legible
BASICOS = {"B": "byte", "C": "char", "D": "double", "F": "float", "I": "int",
           "J": "long", "S": "short", "Z": "boolean", "V": "void"}


def tipo(d, i=0):
    n = 0
    while d[i] == "[":
        n += 1; i += 1
    if d[i] == "L":
        fin = d.index(";", i)
        t = d[i + 1:fin].split("/")[-1].replace("$", ".")
        i = fin + 1
    else:
        t = BASICOS.get(d[i], d[i]); i += 1
    return t + "[]" * n, i


def firma(desc):
    args, i = [], 1
    while desc[i] != ")":
        t, i = tipo(desc, i)
        args.append(t)
    devuelve, _ = tipo(desc, i + 1)
    return "(%s) → %s" % (", ".join(args), devuelve)


def flags(fl):
    fuera = []
    if fl & 0x0008: fuera.append("static")
    if fl & 0x0001: fuera.append("public")
    elif fl & 0x0004: fuera.append("protected")
    elif fl & 0x0002: fuera.append("private")
    return " ".join(fuera)


def jar_del_servidor():
    v = sorted((MC / "versions").glob("*/server-*.jar")) if (MC / "versions").is_dir() else []
    return v[-1] if v else None


def main():
    jar = jar_del_servidor()
    if not jar:
        print("no encuentro el jar en %s/versions" % MC)
        return 2
    print("jar: %s\n" % jar)
    z = zipfile.ZipFile(jar)
    dentro = set(z.namelist())

    if "--paquete" in sys.argv:
        pq = sys.argv[sys.argv.index("--paquete") + 1].replace(".", "/")
        print("clases en %s/ :" % pq)
        for n in sorted(dentro):
            if n.startswith(pq + "/") and n.endswith(".class") and "$" not in n:
                print("   %s" % n[len(pq) + 1:-6])
        return 0

    pedidas = [a for a in sys.argv[1:] if not a.startswith("--")] or INTERESAN
    for nombre in pedidas:
        ruta = nombre.replace(".", "/") + ".class"
        print("═" * 70)
        print(nombre)
        print("═" * 70)
        if ruta not in dentro:
            hoja = nombre.rsplit(".", 1)[-1] + ".class"
            otras = [n for n in dentro if n.endswith("/" + hoja)]
            print("  ✘ no está." + ("  ¿Será %s?" % otras[0][:-6] if otras else ""))
            print()
            continue
        try:
            _, padre, metodos, campos = leer_clase(z.read(ruta))
        except Exception as e:
            print("  ⚠ no pude leerla: %s" % e)
            print()
            continue
        print("  extiende: %s" % padre)
        publicos = [m for m in metodos if m[0] & 0x0001 and m[1] != "<clinit>"]
        estaticos = [m for m in publicos if m[0] & 0x0008]
        normales = [m for m in publicos if not (m[0] & 0x0008)]
        if estaticos:
            print("  estáticos:")
            for fl, n, d in sorted(estaticos, key=lambda m: m[1]):
                print("     %-34s %s" % (n, firma(d)))
        if normales:
            print("  de instancia:")
            for fl, n, d in sorted(normales, key=lambda m: m[1])[:40]:
                print("     %-34s %s" % (n, firma(d)))
        cpub = [c for c in campos if c[0] & 0x0009 == 0x0009]     # public static
        if cpub:
            print("  constantes públicas:")
            for fl, n, d in sorted(cpub, key=lambda c: c[1])[:25]:
                t, _ = tipo(d)
                print("     %-34s %s" % (n, t))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
