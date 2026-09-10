#!/usr/bin/env python3
"""
El botón «buscar generadores en esta zona», de punta a punta.

POR QUÉ ASÍ
-----------
El botón le encarga trabajo al Minecraft de verdad: lo fuerza a generar un
cuadro del mundo, espera a que lo escriba en disco y luego lo relee. Lo que
puede salir mal no es una cuenta, son las costuras:

  · pedir tandas más grandes de las que acepta /forceload
  · dejar chunks forzados puestos si algo se tuerce por el medio
  · dar por terminado un lote que el servidor todavía no había escrito
  · quedarse colgado para siempre si el servidor deja de avanzar
  · contar mal los chunks de la cabecera de las regiones (negativos, bordes)

Nada de eso se ve leyendo el código, y montar un Minecraft de verdad aquí para
comprobarlo cuesta media hora. Así que se monta un Minecraft DE MENTIRA que
habla RCON como el bueno y, cuando le mandas `forceload`, escribe ficheros de
región auténticos a un ritmo que se elige. El panel corre sin tocar ni una
línea: no sabe que al otro lado no hay un juego.

Con eso se puede provocar a voluntad lo que en el server de verdad pasaría una
vez al año: un servidor lento, uno que se atasca a medias, uno que no responde.

Correr:  python3 scripts/probar-zona.py
"""
import json
import os
import shutil
import socket
import socketserver
import struct
import sys
import tempfile
import threading
import time
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import nbt                                                   # noqa: E402

FALLOS = []


def ok(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


# ─────────────────────────────────────────── escribir regiones de verdad
def chunk_nbt(cx, cz, gens):
    """Un chunk con sus generadores, tal y como los guarda Minecraft."""
    bes = []
    for (x, y, z, mob) in gens:
        bes.append([
            (b"id", nbt.Tag(nbt.TAG_STRING, b"minecraft:mob_spawner")),
            (b"x", nbt.Tag(nbt.TAG_INT, x)),
            (b"y", nbt.Tag(nbt.TAG_INT, y)),
            (b"z", nbt.Tag(nbt.TAG_INT, z)),
            (b"SpawnData", nbt.Tag(nbt.TAG_COMPOUND, [
                (b"entity", nbt.Tag(nbt.TAG_COMPOUND, [
                    (b"id", nbt.Tag(nbt.TAG_STRING, b"minecraft:" + mob.encode())),
                ])),
            ])),
        ])
    return nbt.Tag(nbt.TAG_COMPOUND, [
        (b"xPos", nbt.Tag(nbt.TAG_INT, cx)),
        (b"zPos", nbt.Tag(nbt.TAG_INT, cz)),
        (b"Status", nbt.Tag(nbt.TAG_STRING, b"minecraft:full")),
        (b"block_entities", nbt.Tag(nbt.TAG_LIST, nbt.NList(nbt.TAG_COMPOUND, bes))),
    ])


def escribe_regiones(carpeta, chunks):
    """chunks: {(cx,cz): [(x,y,z,mob)]} → ficheros r.X.Z.mca reales."""
    carpeta.mkdir(parents=True, exist_ok=True)
    por_region = {}
    for (cx, cz), gens in chunks.items():
        por_region.setdefault((cx >> 5, cz >> 5), []).append((cx, cz, gens))
    for (rx, rz), lista in por_region.items():
        cabecera = bytearray(8192)
        cuerpo = b""
        sector = 2
        for cx, cz, gens in lista:
            crudo = zlib.compress(nbt.serialize(b"", chunk_nbt(cx, cz, gens)))
            trozo = struct.pack(">I", len(crudo) + 1) + b"\x02" + crudo
            trozo += b"\x00" * (-len(trozo) % 4096)
            n = len(trozo) // 4096
            i = ((cz & 31) * 32 + (cx & 31)) * 4
            cabecera[i:i + 3] = struct.pack(">I", sector)[1:]
            cabecera[i + 3] = n
            cuerpo += trozo
            sector += n
        (carpeta / ("r.%d.%d.mca" % (rx, rz))).write_bytes(bytes(cabecera) + cuerpo)


# ─────────────────────────────────────────── el Minecraft de mentira
class MundoFalso:
    """Habla como el juego y escribe regiones como el juego, pero a mi ritmo.

    `ritmo` son chunks por segundo. `tope` es cuántos chunks generará como mucho
    antes de plantarse: así se puede provocar un servidor que se atasca sin tener
    uno de verdad atascándose.
    """

    def __init__(self, carpeta, ritmo=400.0, tope=None, semilla_gens=97, sin_disco=False):
        self.carpeta = carpeta
        self.ritmo = ritmo
        self.tope = tope
        self.sin_disco = sin_disco
        self.semilla_gens = semilla_gens
        self.hechos = {}                 # (cx,cz) → [(x,y,z,mob)]
        self.cola = {}                   # (cx,cz) → cuándo estará
        self.forzados = set()
        self.ordenes = []
        self.pico = 0                    # lo más forzado que llegó a estar a la vez
        self.generados = 0
        self.lock = threading.Lock()

    # los generadores del mundo: pocos y repartidos, como en uno de verdad
    def gens_de(self, cx, cz):
        if (cx * 7919 + cz * 104729) % self.semilla_gens:
            return []
        mob = ("zombie", "skeleton", "spider", "cave_spider")[(cx + cz) % 4]
        return [(cx * 16 + 8, 30, cz * 16 + 8, mob)]

    def manda(self, cmd):
        with self.lock:
            self.ordenes.append(cmd)
            p = cmd.split()
            if p[:1] == ["list"]:
                return "There are 0 of a max of 5 players online:"
            if p[:1] == ["save-all"]:
                # las frases son las del jar de 26.2, comprobadas en
                # assets/minecraft/lang/en_us.json
                if self.sin_disco:
                    return "Unable to save the game (is there enough disk space?)"
                self._guardar()
                return "Saved the game"
            if p[:2] == ["forceload", "add"] or p[:2] == ["forceload", "remove"]:
                x0, z0, x1, z1 = (int(v) for v in p[2:6])
                cx0, cz0, cx1, cz1 = x0 >> 4, z0 >> 4, x1 >> 4, z1 >> 4
                cuantos = (cx1 - cx0 + 1) * (cz1 - cz0 + 1)
                trozos = {(x, z) for x in range(cx0, cx1 + 1) for z in range(cz0, cz1 + 1)}
                if p[1] == "add":
                    # el tope de verdad del juego: 256 chunks por orden
                    if cuantos > 256:
                        return ("Too many chunks in the specified area "
                                "(maximum 256, but specified %d)" % cuantos)
                    self.forzados |= trozos
                    self.pico = max(self.pico, len(self.forzados))
                    ahora = time.time()
                    for i, c in enumerate(sorted(trozos)):
                        if c not in self.hechos and c not in self.cola:
                            self.cola[c] = ahora + (i + 1) / self.ritmo
                    return ("Marked %d chunks in minecraft:overworld from %d, %d "
                            "to %d, %d to be force loaded" % (cuantos, cx0, cz0, cx1, cz1))
                self.forzados -= trozos
                return ("Unmarked %d chunks in minecraft:overworld from %d, %d "
                        "to %d, %d for force loading" % (cuantos, cx0, cz0, cx1, cz1))
            return "Unknown or incomplete command"

    def _guardar(self):
        ahora = time.time()
        listos = [c for c, cuando in self.cola.items() if cuando <= ahora]
        for c in listos:
            del self.cola[c]
            if self.tope is not None and self.generados >= self.tope:
                continue                 # el servidor se plantó
            self.hechos[c] = self.gens_de(*c)
            self.generados += 1
        if listos:
            escribe_regiones(self.carpeta, self.hechos)


class _Manejador(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        while True:
            cab = self._exacto(s, 4)
            if not cab:
                return
            (n,) = struct.unpack("<i", cab)
            cuerpo = self._exacto(s, n)
            if not cuerpo:
                return
            rid, tipo = struct.unpack("<ii", cuerpo[:8])
            texto = cuerpo[8:-2].decode("utf-8", "replace")
            salida = "" if tipo == 3 else self.server.mundo.manda(texto)
            datos = struct.pack("<ii", rid, 2 if tipo == 3 else 0) + salida.encode() + b"\x00\x00"
            s.sendall(struct.pack("<i", len(datos)) + datos)

    @staticmethod
    def _exacto(s, n):
        b = b""
        while len(b) < n:
            t = s.recv(n - len(b))
            if not t:
                return None
            b += t
        return b


class RconFalso(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def arranca_rcon(mundo):
    srv = RconFalso(("127.0.0.1", 0), _Manejador)
    srv.mundo = mundo
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


# ─────────────────────────────────────────── el escenario
tmp = Path(tempfile.mkdtemp(prefix="zona-"))
PANEL = tmp / "panel"
MC = tmp / "minecraft"
REGION = MC / "world/dimensions/minecraft/overworld/region"
REGION.mkdir(parents=True)
shutil.copytree(RAIZ / "scripts", PANEL / "scripts")
shutil.copytree(RAIZ / "static", PANEL / "static")
shutil.copy(RAIZ / "server.py", PANEL / "server.py")
shutil.copy(RAIZ / "nbt.py", PANEL / "nbt.py")
(PANEL / "data").mkdir(exist_ok=True)

mundo = MundoFalso(REGION)
srv, puerto = arranca_rcon(mundo)
(MC / "server.properties").write_text(
    "level-seed=1244994422874902852\nrcon.port=%d\nrcon.password=prueba\n" % puerto)

os.environ["PANEL_DIR"] = str(PANEL)
os.environ["MC_DIR"] = str(MC)
os.environ["BLUEMAP_DIR"] = str(tmp / "bluemap")
sys.path.insert(0, str(PANEL))
import server                                                # noqa: E402

print("── hablar con el servidor ──")
ok(server.rcon_try("list")[0], "el RCON de mentira contesta")
ok("Too many" in server.rcon_try("forceload add 0 0 1000 1000")[1],
   "y se queja si le piden más de 256 chunks de una vez")
mundo.forzados.clear()
mundo.cola.clear()
mundo.ordenes.clear()

# ─────────────────────────────────────────── 1. contar chunks en las cabeceras
print("── contar lo que hay escrito (cabeceras de región) ──")
# a caballo entre cuatro regiones y pisando el cero: donde se rompen los bits
sueltos = {(-33, -33), (-32, -1), (-1, -32), (-1, -1), (0, 0), (1, 1), (31, 31), (32, 32)}
escribe_regiones(REGION, {c: [] for c in sueltos})
for caja in ((-40, -40, 40, 40), (0, 0, 40, 40), (-33, -33, -33, -33),
             (-32, -32, -1, -1), (-1, -1, 0, 0), (100, 100, 130, 130)):
    cx0, cz0, cx1, cz1 = caja
    # la cuenta buena se saca contando a mano, no escribiéndola aquí: una
    # expectativa a mano se equivoca igual que el código que quiere vigilar
    espera = sum(1 for (x, z) in sueltos if cx0 <= x <= cx1 and cz0 <= z <= cz1)
    n = server._chunks_hechos(*caja)
    ok(n == espera, "cuadro %s → %d chunks (a mano salen %d)" % (str(caja), n, espera))
for f in REGION.glob("*.mca"):
    f.unlink()
mundo.hechos.clear()

# ─────────────────────────────────────────── 2. el trabajo entero
print("── el trabajo, de principio a fin ──")
server._ZONA_LATIDO = 0.4          # el de verdad son 6 s: aquí no hay nada que esperar
CAJA = (0, 0, 31, 31)              # 32x32 chunks = 1024, lo máximo que acepta el botón
t0 = time.time()
server._zona_trabajo(*CAJA, 30)
tardo = time.time() - t0
est = dict(server._zona)
print("    %s en %.1f s" % ({k: est[k] for k in ("estado", "hechos", "total", "hallados")}, tardo))

ok(est["estado"] == "listo", "termina en «listo» (%s)" % est["estado"])
ok(est["total"] == 1024, "cuenta los 1024 chunks del cuadro")
ok(est["hechos"] == 1024, "y dice que hizo los 1024 (%s)" % est["hechos"])
ok(len(mundo.hechos) == 1024, "el mundo tiene los 1024 escritos (%d)" % len(mundo.hechos))

adds = [o for o in mundo.ordenes if o.startswith("forceload add")]
quita = [o for o in mundo.ordenes if o.startswith("forceload remove")]
ok(len(adds) == 4, "cuatro tandas de 256 chunks (%d)" % len(adds))
ok(len(quita) == len(adds), "una retirada por cada tanda (%d/%d)" % (len(quita), len(adds)))
ok(not mundo.forzados, "no queda ni un chunk forzado al terminar (%d)" % len(mundo.forzados))
ok(mundo.pico <= 256, "nunca hubo más de 256 chunks forzados a la vez (%d)" % mundo.pico)
ok(any(o.startswith("save-all") for o in mundo.ordenes), "se guarda el mundo por el camino")

# los generadores que el escaneo encontró: la prueba de que la cadena llega hasta el final
esperados = sum(1 for c in mundo.hechos if mundo.gens_de(*c))
gens = json.loads((PANEL / "data/spawners.json").read_text())
ok(len(gens.get("overworld", [])) == esperados,
   "el escaneo guardó los %d generadores del cuadro (%d)"
   % (esperados, len(gens.get("overworld", []))))
ok(est["hallados"] == esperados,
   "y el botón informa de esos mismos %d (%s)" % (esperados, est["hallados"]))
mobs = {g["mob"] for g in gens.get("overworld", [])}
ok(mobs and mobs <= {"zombie", "skeleton", "spider", "cave_spider"},
   "con el bicho de cada uno: %s" % ", ".join(sorted(mobs)))

# y que la pestaña Explorar los vería
enmapa = server._m2_generadores(0, 0, 511, 511)
ok(len(enmapa) == esperados, "el mapa los pide y salen los %d" % esperados)
ok(all(g["k"] == "spawner" and g["tipo"].startswith("spawner_") for g in enmapa),
   "cada uno con su icono de variante (%s)" % (enmapa[0]["tipo"] if enmapa else "—"))

# ─────────────────────────────────────────── 3. un servidor que se atasca
print("── un servidor que deja de avanzar ──")
for f in REGION.glob("*.mca"):
    f.unlink()
atascado = MundoFalso(REGION, ritmo=400.0, tope=300)     # se planta a los 300 chunks
srv.mundo = atascado
t0 = time.time()
server._zona_trabajo(0, 0, 31, 31, 30)
tardo = time.time() - t0
est = dict(server._zona)
print("    %s en %.1f s" % ({k: est[k] for k in ("estado", "hechos", "total")}, tardo))
ok(est["estado"] in ("listo", "error"), "no se queda colgado (%s)" % est["estado"])
ok(tardo < 60, "y termina en un tiempo razonable (%.0f s)" % tardo)
ok(est["hechos"] < est["total"], "dice la verdad: hizo %d de %d" % (est["hechos"], est["total"]))
ok(not atascado.forzados, "tampoco deja chunks forzados (%d)" % len(atascado.forzados))

# ─────────────────────────────────────────── 3b. un disco lleno
print("── un disco sin sitio ──")
for f in REGION.glob("*.mca"):
    f.unlink()
lleno = MundoFalso(REGION, sin_disco=True)
srv.mundo = lleno
server._zona_trabajo(0, 0, 15, 15, 30)
est = dict(server._zona)
ok(est["estado"] == "error", "no se calla: %s" % est["estado"])
ok("disk space" in (est["mensaje"] or ""),
   "y repite lo que dijo el juego: %r" % (est["mensaje"] or "")[:70])
ok(not lleno.forzados, "y suelta los chunks igualmente (%d)" % len(lleno.forzados))


# ─────────────────────────────────────────── 4. un servidor apagado
print("── un servidor apagado ──")
srv.shutdown()
srv.server_close()
server._zona_trabajo(0, 0, 15, 15, 30)
est = dict(server._zona)
ok(est["estado"] == "error", "lo dice en vez de fingir que trabaja (%s)" % est["estado"])
ok("Minecraft" in (est["mensaje"] or ""), "y con un mensaje que se entiende: %r" % est["mensaje"][:60])

# ─────────────────────────────────────────── 5. la puerta de entrada
print("── la API ──")
server._zona.update({"estado": "quieto", "t": 0})
cli = server.app.test_client()
QUIEN = {"name": "juan", "role": "admin"}
server.require = lambda *a, **k: QUIEN                 # la sesión ya está probada aparte

QUIEN = {"name": "curioso", "role": "viewer"}
r = cli.post("/api/mapa2/zona", json={"x0": 0, "z0": 0, "x1": 100, "z1": 100},
             headers={"X-Panel": "1"})
ok(r.status_code == 403, "un observador no puede encargar trabajo (%d)" % r.status_code)

QUIEN = {"name": "juan", "role": "admin"}
r = cli.post("/api/mapa2/zona", json={"x0": 0, "z0": 0, "x1": 100, "z1": 100})
ok(r.status_code == 403, "ni nadie sin la cabecera del panel (%d)" % r.status_code)

r = cli.post("/api/mapa2/zona", json={"x0": 0}, headers={"X-Panel": "1"})
ok(r.status_code == 400, "faltando coordenadas, 400 (%d)" % r.status_code)
ok(server._zona["estado"] == "quieto", "y no se queda marcado como ocupado")

r = cli.post("/api/mapa2/zona", json={"x0": 0, "z0": 0, "x1": 100, "z1": 100},
             headers={"X-Panel": "1"})
ok(r.status_code == 503, "con el Minecraft apagado, 503 (%d)" % r.status_code)
ok("enciéndelo" in r.get_json().get("error", ""), "diciendo qué hacer: %r" % r.get_json().get("error"))
ok(server._zona["estado"] == "quieto", "y tampoco se queda ocupado")

# el recorte: pedir medio mundo no puede generar medio mundo
mundo2 = MundoFalso(REGION)
srv2, puerto2 = arranca_rcon(mundo2)
(MC / "server.properties").write_text(
    "level-seed=1244994422874902852\nrcon.port=%d\nrcon.password=prueba\n" % puerto2)
lanzados = []
_trabajo = server._zona_trabajo
server._zona_trabajo = lambda *a: lanzados.append(a)     # el trabajo ya está probado arriba
r = cli.post("/api/mapa2/zona", json={"x0": -100000, "z0": -100000, "x1": 100000, "z1": 100000},
             headers={"X-Panel": "1"})
d = r.get_json() or {}
ok(r.status_code == 200 and d.get("ok"), "acepta un cuadro enorme (%d)" % r.status_code)
ok(d.get("chunks") == 1024, "pero recorta a 1024 chunks (%s)" % d.get("chunks"))
time.sleep(0.3)
if lanzados:
    cx0, cz0, cx1, cz1, _espera = lanzados[0]
    ok(cx1 - cx0 + 1 == 32 and cz1 - cz0 + 1 == 32, "32x32 chunks de verdad")
    ok(abs((cx0 + cx1) // 2) <= 1 and abs((cz0 + cz1) // 2) <= 1,
       "centrado donde estaba mirando (%d,%d)" % ((cx0 + cx1) // 2, (cz0 + cz1) // 2))
else:
    ok(False, "el trabajo se lanza en su propio hilo")
server._zona.update({"estado": "generando", "t": time.time()})
r = cli.post("/api/mapa2/zona", json={"x0": 0, "z0": 0, "x1": 100, "z1": 100},
             headers={"X-Panel": "1"})
ok(r.status_code == 409, "con una búsqueda en marcha, no arranca otra (%d)" % r.status_code)
server._zona_trabajo = _trabajo

r = cli.get("/api/mapa2/zona")
d = r.get_json() or {}
ok(d.get("ok") and d.get("estado") == "generando", "el estado se puede consultar (%s)" % d.get("estado"))
ok(d.get("max_chunks") == 1024, "y dice cuánto abarca como mucho (%s)" % d.get("max_chunks"))

srv2.shutdown()
srv2.server_close()
shutil.rmtree(tmp, ignore_errors=True)

print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
