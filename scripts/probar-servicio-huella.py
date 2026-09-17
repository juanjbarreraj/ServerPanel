#!/usr/bin/env python3
"""
Que el panel se entere cuando el lector de biomas se reinicia debajo.

POR QUÉ ASÍ
-----------
El panel guarda un objeto `Servicio` vivo mientras dure el proceso, y éste
preguntaba `/salud` UNA sola vez:

    def salud(self):
        if self._salud is None:
            self._salud = json.loads(self._pedir("/salud", espera=5))
        return self._salud

Parece inofensivo —la semilla de un mundo no cambia— pero de ahí sale la
HUELLA, que es el nombre de la carpeta donde se guardan los azulejos del mapa.

El lector de biomas se reinicia solo: al actualizarse Minecraft, al cambiar de
mundo, al pulsar el botón del panel. Si vuelve con otra huella y el panel sigue
con la de antes, escribe los azulejos NUEVOS en la carpeta VIEJA. Y como los
azulejos ya cacheados no se recalculan nunca, en esa carpeta acaban conviviendo
dos épocas: unos dibujados con la paleta de antes y otros con la de ahora. El
mapa sale a parches y al cambiar de zoom cambias de carpeta y te toca otra
mezcla. Así se perdió el Bosque Moteado de la 26.3 durante un día entero, y
costó cuatro diagnósticos equivocados encontrarlo.

Lo más feo del caso es lo silencioso: no hay excepción, no hay hueco en el mapa,
no hay nada en ningún registro. Solo colores que no son.

Aquí se levanta un servicio de biomas de mentira que puede cambiar de huella y
de leyenda cuando se le diga, y se comprueba que el panel lo sigue.

Correr:  python3 scripts/probar-servicio-huella.py
"""
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import biomas                                               # noqa: E402

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


# ─────────────────────────────────── un lector de biomas de mentira, mandable
ESTADO = {"huella": "aaaa1111", "biomas": 66, "caido": False, "peticiones": 0}
LEYENDA_VIEJA = {"0": "minecraft:plains", "1": "minecraft:forest"}
LEYENDA_NUEVA = {"0": "minecraft:plains", "1": "minecraft:dappled_forest",
                 "2": "minecraft:forest"}


class Mano(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        ESTADO["peticiones"] += 1
        if ESTADO["caido"]:
            self.send_error(503)
            return
        if self.path.startswith("/salud"):
            cuerpo = json.dumps({"ok": True, "semilla": 123, "y": 128, "hilos": 2,
                                 "biomas": ESTADO["biomas"],
                                 "huella": ESTADO["huella"]})
        elif self.path.startswith("/leyenda"):
            cuerpo = json.dumps(LEYENDA_NUEVA if ESTADO["biomas"] == 67
                                else LEYENDA_VIEJA)
        else:
            self.send_error(404)
            return
        datos = cuerpo.encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


web = ThreadingHTTPServer(("127.0.0.1", 0), Mano)
threading.Thread(target=web.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % web.server_address[1]

srv = biomas.Servicio(URL)
srv.PLAZO_SALUD = 1                      # para no tener que esperar quince


def color_de(bioma):
    """El color que la paleta del PNG le da a ese bioma AHORA mismo."""
    ley = srv.leyenda()
    i = next((k for k, v in ley.items() if v == bioma), None)
    if i is None:
        return None
    p = srv.paleta_plana()
    return tuple(p[i * 3:i * 3 + 3])


titulo("1 · de entrada")
ok(srv.salud()["huella"] == "aaaa1111", "coge la huella del servicio")
ok(biomas.nombre_mundo(srv) == "123-aaaa1111",
   "y la carpeta de azulejos la lleva en el nombre (%s)" % biomas.nombre_mundo(srv))
antes = ESTADO["peticiones"]
for _ in range(20):
    biomas.nombre_mundo(srv)
ok(ESTADO["peticiones"] == antes,
   "veinte azulejos seguidos no son veinte preguntas (%d)" % (ESTADO["peticiones"] - antes))

titulo("2 · el servicio se reinicia debajo")
ESTADO["huella"] = "bbbb2222"
ok(biomas.nombre_mundo(srv) == "123-aaaa1111",
   "dentro del plazo sigue con lo que tenía, que para eso se guarda")
time.sleep(1.2)
ok(biomas.nombre_mundo(srv) == "123-bbbb2222",
   "pasado el plazo se entera solo: %s" % biomas.nombre_mundo(srv))
ok(srv.salud()["huella"] == "bbbb2222", "y la salud también está al día")

titulo("3 · si además cambian los biomas")
# Lo peligroso de verdad: la leyenda es índice→bioma. Con un bioma más, los
# índices se corren; si la paleta se quedara con la de antes, el mapa ENTERO
# saldría con los colores movidos y sin fallar ni una vez.
ok(color_de("minecraft:forest") == biomas.PALETA["forest"],
   "con la leyenda vieja, el bosque va de su color")
viejo_forest = color_de("minecraft:forest")

ESTADO["biomas"] = 67
ESTADO["huella"] = "cccc3333"
time.sleep(1.2)
biomas.nombre_mundo(srv)                 # cualquier azulejo dispara la revisión
ley = srv.leyenda()
ok(ley.get(1) == "minecraft:dappled_forest",
   "la leyenda se vuelve a pedir y trae el bioma nuevo (%s)" % ley.get(1))
ok(color_de("minecraft:dappled_forest") == biomas.PALETA["dappled_forest"],
   "el bioma nuevo sale de su color, no del de quien ocupaba su índice")
ok(color_de("minecraft:forest") == viejo_forest,
   "y el bosque sigue siendo verde aunque se haya corrido de índice")

titulo("4 · un parpadeo no tira el mapa")
ESTADO["caido"] = True
time.sleep(1.2)
try:
    quien = biomas.nombre_mundo(srv)
    ok(quien == "123-cccc3333",
       "con el servicio caído se sigue con lo último bueno (%s)" % quien)
except biomas.NoDisponible:
    ok(False, "con el servicio caído se sigue con lo último bueno")
ESTADO["caido"] = False
antes = ESTADO["peticiones"]
time.sleep(2.2)
biomas.nombre_mundo(srv)
ok(ESTADO["peticiones"] > antes, "y se reintenta enseguida, no dentro de un plazo entero")

titulo("5 · si nunca llegó a contestar")
otro = biomas.Servicio("http://127.0.0.1:1")     # ahí no hay nadie
try:
    otro.salud()
    ok(False, "sin nada que guardar, el fallo se cuenta")
except biomas.NoDisponible:
    ok(True, "sin nada que guardar, el fallo se cuenta en vez de inventarse una huella")

web.shutdown()
print()
if fallos:
    print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
    for f in fallos:
        print("   ·", f)
    sys.exit(1)
print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
