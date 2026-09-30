#!/usr/bin/env python3
"""
Buscar lobos por color, y las señas que dicen POR QUÉ falta uno.

Lo que se prueba aquí no es «lee ficheros» —eso ya lo hace el buscador de
siempre— sino las dos cosas que se pueden equivocar en silencio:

  1. **Encontrar la variante sin saber cómo se llama la clave.** Mojang la ha
     movido de sitio más de una vez (`Variant`, `variant`, un componente…), y
     una clave escrita a mano deja de encontrar nada el día que la cambien, sin
     dar ningún error: simplemente todos los lobos salen «color desconocido».
  2. **Que un lobo SENTADO se vea como tal.** Es la primera explicación de «se
     me ha perdido un perro» —un lobo sentado no se teletransporta nunca— y si
     la lista no lo dice, se da por muerto un perro que está vivo.

Correr:  python3 scripts/probar-buscar-lobos.py
"""
import importlib.util, sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent
sys.path.insert(0, str(REPO))
import nbt

spec = importlib.util.spec_from_file_location("be", AQUI / "buscar-entidad.py")
be = importlib.util.module_from_spec(spec)
spec.loader.exec_module(be)

fallos, pasadas = [], 0


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


def T(tipo, valor):
    return nbt.Tag(tipo, valor)


def lobo(variante=None, clave="variant", dueño=None, sentado=False,
         collar=None, vida=None, ruido="classic"):
    """Un lobo de mentira como payload de compound: [(nombre, Tag), …]."""
    e = [(b"id", T(nbt.TAG_STRING, b"minecraft:wolf"))]
    if variante is not None:
        e.append((clave.encode(), T(nbt.TAG_STRING, variante.encode())))
    # SIEMPRE lleva sound_variant: es la trampa. Si el buscador se queda con la
    # primera clave que diga «variant», dirá que el lobo es de color "classic".
    e.append((b"sound_variant", T(nbt.TAG_STRING, ruido.encode())))
    if dueño is not None:
        e.append((b"Owner", T(nbt.TAG_INT_ARRAY, dueño)))
    if sentado:
        e.append((b"Sitting", T(nbt.TAG_BYTE, 1)))
    if collar is not None:
        e.append((b"CollarColor", T(nbt.TAG_BYTE, collar)))
    if vida is not None:
        e.append((b"Health", T(nbt.TAG_FLOAT, vida)))
    return e


# el UUID de Juan como los 4 enteros que guarda Minecraft
UUID_JUAN = [0x11111111, 0x22223333, 0x44445555, 0x66667777]
NOMBRES = {"11111111-2222-3333-4444-555566667777": "JEYtheFlash"}


def main():
    titulo("1 · la variante se encuentra, se llame como se llame")
    for clave in ("variant", "Variant", "minecraft:wolf_variant", "WolfVariant"):
        ok(be.variante_de(lobo("black", clave=clave)) == "black",
           "con la clave «%s»" % clave)
    ok(be.variante_de(lobo("minecraft:black")) == "black",
       "y se le quita el «minecraft:» de delante")
    ok(be.variante_de(lobo(None)) is None,
       "un lobo sin variante devuelve None, no se lo inventa")
    # 🔴 la trampa: sound_variant también contiene «variant»
    ok(be.variante_de(lobo("black")) != "classic",
       "y NO confunde `sound_variant` con el color (sería «classic» en todos)")
    ok(be.variante_de(lobo(None, ruido="cute")) is None,
       "ni siquiera cuando no hay color de verdad con el que compararlo")

    titulo("2 · pedir el color como se ve, en español o en inglés")
    ok(be._variante_pedida("negro") == "black", "negro → black")
    ok(be._variante_pedida("black") == "black", "black → black")
    ok(be._variante_pedida("NEGRO") == "black", "y da igual cómo se escriba")
    ok(be._variante_pedida("castaño") == "chestnut", "castaño → chestnut")
    ok(be._variante_pedida("castano") == "chestnut",
       "y sin la ñ también, que en un terminal pasa")
    ok(be._variante_pedida("rayado") == "striped", "rayado → striped")
    ok(be._variante_pedida("morado") == "morado",
       "un color que no existe se busca tal cual, sin romperse")
    ok(be._variante_pedida(None) is None, "y sin pedir nada, no filtra")
    ok(len(be.LOBOS) == 9,
       "están las nueve variantes del juego (%d)" % len(be.LOBOS))

    titulo("3 · las señas dicen por qué falta un perro")
    s = be.señas_lobo(lobo("black", dueño=UUID_JUAN, sentado=True,
                           collar=14, vida=20.0), NOMBRES)
    ok("negro" in s, "el color, en español: %r" % s)
    ok("JEYtheFlash" in s, "de quién es")
    ok("SENTADO" in s, "🔴 y que está SENTADO, que es por lo que no te sigue")
    ok("collar rojo" in s, "el color del collar")
    ok("20♥" in s, "y la vida")

    s2 = be.señas_lobo(lobo("ashen", dueño=UUID_JUAN, collar=11), NOMBRES)
    ok("SENTADO" not in s2, "uno de pie NO sale como sentado")
    ok("ceniza" in s2 and "collar azul" in s2, "y sus señas salen bien: %r" % s2)

    s3 = be.señas_lobo(lobo("black"), NOMBRES)
    ok("salvaje" in s3, "un lobo sin dueño sale como salvaje")

    s4 = be.señas_lobo(lobo(None, dueño=UUID_JUAN), NOMBRES)
    ok("color desconocido" in s4,
       "y si un día no se encuentra el color, lo DICE en vez de callarse")

    titulo("4 · el buscador sabe que existe todo esto")
    doc = (AQUI / "buscar-entidad.py").read_text()
    ok("--lobos" in doc and "--color" in doc, "las banderas están documentadas")
    ok('tipo = "wolf"' in doc, "y --lobos busca lobos")
    ok('"variante": variante_de(ent)' in doc,
       "el escaneo guarda la variante de cada uno, para poder filtrar")
    ok("Variantes que SÍ hay" in doc,
       "y si no hay ninguno de ese color, enseña los que sí hay en vez de "
       "dejarte con un cero")

    titulo("5 · una bandera que no existe se dice, no se traga")
    # 🔴 Pasó de verdad: correr `--lobos --color negro` contra la versión
    # anterior descartó las dos banderas en silencio, dejó «negro» como NOMBRE
    # y contestó «NO EXISTE ninguno con ese nombre» con una lista de 105
    # nombres. Una respuesta segurísima a una pregunta que nadie hizo.
    import subprocess
    r = subprocess.run([sys.executable, str(AQUI / "buscar-entidad.py"),
                        "--lobos", "--colorr", "negro"],
                       capture_output=True, text=True,
                       env={**__import__("os").environ, "MC_DIR": "/tmp/no-existe"})
    ok(r.returncode == 2, "sale con error (%d), no con una respuesta cualquiera"
       % r.returncode)
    ok("--colorr" in r.stdout, "y dice EXACTAMENTE cuál no entiende")
    ok("Commit+Sync" in r.stdout,
       "y sugiere lo que casi siempre es: que el servidor tiene la versión vieja")
    ok("negro" not in r.stdout.split("Las que sí")[0].replace("--colorr", ""),
       "y sobre todo NO se pone a buscar «negro» por su cuenta")
    for b in ("--lobos", "--color", "--tipo", "--caballos"):
        ok(b in be.BANDERAS, "«%s» está en la lista de banderas buenas" % b)


    titulo("6 · la lista se lee sin scrollear, y el /tp lleva a donde dice")
    # Los dos fallos que se vieron con la salida de verdad: 23 lobos, los tres
    # de Juan al FINAL, y un /tp sin dimensión que en el Nether manda a otro
    # sitio (los mismos números en el Overworld son un lugar distinto).
    mio = {"nombre": "", "tipo": "wolf", "dim": "nether", "pos": (804, 63, 2607),
           "vida": 40.0, "señas": "negro · de JEYtheFlash", "dueño": "JEYtheFlash"}
    salvaje = {"nombre": "", "tipo": "wolf", "dim": "overworld",
               "pos": (493, 78, 2477), "vida": 8.0, "señas": "negro · salvaje",
               "dueño": None}
    con_nombre = {"nombre": "Toothless", "tipo": "wolf", "dim": "overworld",
                  "pos": (402, 116, 704), "vida": 35.0,
                  "señas": "negro · de Jakobino155", "dueño": "Jakobino155"}

    ok(be.orden_tp(mio) == "/execute in minecraft:the_nether run tp @s 804 63 2607",
       "🔴 en el Nether el /tp lleva la dimensión: %s" % be.orden_tp(mio))
    ok(be.orden_tp(salvaje) == "/tp @s 493 78 2477",
       "y en el Overworld se queda corto y claro")
    ok(be.orden_tp({**mio, "dim": "end"}).startswith("/execute in minecraft:the_end"),
       "el End también")
    ok(be.orden_tp({**mio, "pos": None}) is None,
       "y sin posición no se inventa una orden")

    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        be.pinta_lista([salvaje] * 20 + [mio, con_nombre])
    lineas = buf.getvalue().splitlines()
    primeros = "\n".join(lineas[:14])
    ok("JEYtheFlash" in primeros,
       "🔴 con 20 lobos salvajes por medio, los DOMADOS salen arriba")
    ok(primeros.index("JEYtheFlash") < primeros.index("salvaje")
       if "salvaje" in primeros else True,
       "antes que los salvajes, que es lo que uno no está buscando")


    titulo("7 · filtrar por dueño")
    # «Las mascotas de fulano» es la pregunta real casi siempre, y leerla a ojo
    # entre cien bichos no es leerla.
    import subprocess, os
    ent = {**os.environ, "MC_DIR": "/tmp/no-existe-nada"}
    r = subprocess.run([sys.executable, str(AQUI / "buscar-entidad.py"),
                        "--lobos", "--dueño", "jalrvarezzz"],
                       capture_output=True, text=True, env=ent)
    ok(r.returncode != 0 or "Ninguno" in r.stdout,
       "acepta --dueño sin quejarse de la bandera")
    ok("No conozco" not in r.stdout, "y no la toma por desconocida")
    for b in ("--dueño", "--dueno"):
        ok(b in be.BANDERAS, "«%s» está en la lista de banderas buenas" % b)
    r = subprocess.run([sys.executable, str(AQUI / "buscar-entidad.py"),
                        "--lobos", "--duenyo", "x"],
                       capture_output=True, text=True, env=ent)
    ok(r.returncode == 2 and "--duenyo" in r.stdout,
       "pero una mal escrita sigue parando el guion")


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
