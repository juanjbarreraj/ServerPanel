#!/usr/bin/env python3
"""
Que el panel se entere solo de que Minecraft cambió de versión.

POR QUÉ ASÍ
-----------
El panel actualiza Minecraft solo: cada 6 h mira si hay versión nueva, hace
copia, la instala y reinicia el lector de biomas. Pero las tablas de estructuras
que lee del jar se quedaban en memoria del propio panel, así que la pestaña
Explorar seguía contestando con las de la versión ANTERIOR hasta que alguien
reiniciara el panel a mano.

Un paso manual escondido dentro de algo que se vende como automático es la peor
clase de paso manual: nadie sabe que existe hasta que los datos llevan semanas
mal y nada se ha quejado.

Aquí se comprueban las dos mitades:

  · que el jar que se elige es el más nuevo POR FECHA, no por nombre (ordenando
    texto, «26.2» gana a «26.10» y el panel leería una versión vieja);
  · que cambiar el jar debajo hace que se recarguen las tablas, sin reiniciar
    nada y sin llamar a ninguna función especial.

Correr:  python3 scripts/probar-jar-nuevo.py
"""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FALLOS = []


def ok(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


tmp = Path(tempfile.mkdtemp(prefix="jar-nuevo-"))
PANEL = tmp / "panel"
MC = tmp / "minecraft"
shutil.copytree(RAIZ / "scripts", PANEL / "scripts")
shutil.copy(RAIZ / "server.py", PANEL / "server.py")
shutil.copy(RAIZ / "nbt.py", PANEL / "nbt.py")
(PANEL / "data").mkdir(exist_ok=True)
(MC / "versions").mkdir(parents=True)

os.environ["PANEL_DIR"] = str(PANEL)
os.environ["MC_DIR"] = str(MC)
sys.path.insert(0, str(PANEL))
import server                                                # noqa: E402


def pon_jar(version, cuando):
    d = MC / "versions" / version
    d.mkdir(parents=True, exist_ok=True)
    j = d / ("server-%s.jar" % version)
    j.write_bytes(b"jar de mentira " + version.encode())
    os.utime(j, (cuando, cuando))
    return j


print("── cuál es el jar de ahora ──")
ok(server._mc_jar() is None, "sin jars, no se inventa ninguno")

ahora = time.time()
viejo = pon_jar("26.2", ahora - 86400)
ok(server._mc_jar() == viejo, "con uno solo, ese (%s)" % server._mc_jar().name)

# El caso que rompía: por nombre, «26.2» va DESPUÉS de «26.10».
nuevo = pon_jar("26.10", ahora)
elegido = server._mc_jar()
ok(elegido == nuevo, "con 26.2 y 26.10 elige la 26.10, no la 26.2 (eligió %s)"
   % elegido.name)
ok(sorted([viejo.name, nuevo.name], reverse=True)[0] == viejo.name,
   "(ordenando por nombre habría salido la vieja: por eso se ordena por fecha)")

print("── recargar cuando cambia el jar ──")
# Se sustituyen los módulos pesados por unos de mentira que apuntan con qué jar
# se les llamó: lo que se prueba es CUÁNDO se recargan, no qué leen del jar.
cargas = []


class _Estructuras:
    CONJUNTOS = {}
    TIPOS = {}

    @staticmethod
    def cargar(j):
        cargas.append(("cargar", j))

    @staticmethod
    def cargar_estructuras(j):
        cargas.append(("estructuras", j))


class _Biomas:
    NIVELES = [4]
    TAM = 256

    class Servicio:
        def __init__(self, url):
            self.url = url


import types                                                 # noqa: E402
for nombre, mod in (("biomas", _Biomas), ("estructuras", _Estructuras)):
    falso = types.ModuleType(nombre)
    for k in dir(mod):
        if not k.startswith("__"):
            setattr(falso, k, getattr(mod, k))
    sys.modules[nombre] = falso

server._m2_mods()
ok(len(cargas) == 2, "la primera vez carga las tablas (%d llamadas)" % len(cargas))
ok(cargas[0][1].endswith("26.10.jar"), "y contra el jar bueno (%s)"
   % Path(cargas[0][1]).name)

cargas.clear()
for _ in range(5):
    server._m2_mods()
ok(not cargas, "las siguientes veces NO recarga nada (%d llamadas)" % len(cargas))

# alguien —el actualizador automático— pone una versión más nueva
server._m2_est[("x", 0, 0)] = ["algo calculado con las tablas viejas"]
pon_jar("26.11", time.time() + 5)
server._m2_mods()
ok(len(cargas) == 2, "al cambiar el jar, recarga (%d llamadas)" % len(cargas))
ok(cargas and cargas[0][1].endswith("26.11.jar"),
   "contra el jar NUEVO (%s)" % (Path(cargas[0][1]).name if cargas else "—"))
ok(not server._m2_est, "y tira lo que había calculado con las tablas de antes")

cargas.clear()
server._m2_mods()
ok(not cargas, "y se vuelve a quedar quieta hasta el próximo cambio")

shutil.rmtree(tmp, ignore_errors=True)
print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
