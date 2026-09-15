#!/usr/bin/env python3
"""
Repintar el mapa 3D desde cero: que borre lo que debe y solo lo que debe.

POR QUÉ ASÍ
-----------
Este es el único botón del panel que hace `rm -rf` sobre una carpeta grande
antes de trabajar. Si la ruta se calcula mal, no se pierde «el mapa»: se pierde
lo que haya en la carpeta que le toque. Así que aquí se monta un ~/bluemap de
mentira con ficheros señuelo alrededor y se comprueba, después de correr el
script de verdad, qué sobrevivió y qué no.

También se comprueba lo que se lee del registro de BlueMap para la barra de
progreso. El formato («descripción: N.NNN% (ETA: …)») no sale de la
documentación: lo saqué del propio jar, de una concatenación `\\1: \\1%\\1` en
BlueMapCLI. Si algún día cambia, que lo diga esta prueba y no la barra parada.

Correr:  python3 scripts/probar-repintar.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FALLOS = []


def ok(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


# ─────────────────────────────────── un ~/bluemap de mentira
def monta(raiz, root_conf='root: "web/maps"'):
    bm = raiz / "bluemap"
    (bm / "config/storages").mkdir(parents=True, exist_ok=True)
    (bm / "config/maps").mkdir(parents=True, exist_ok=True)
    (bm / "config/storages/file.conf").write_text(root_conf + "\n")
    # los azulejos que hay que borrar
    for mapa in ("overworld", "nether"):
        d = bm / "web/maps" / mapa / "tiles/0/x0"
        d.mkdir(parents=True, exist_ok=True)
        (d / "z0.png.gz").write_bytes(b"azulejo viejo")
        (bm / "web/maps" / mapa / "live").mkdir(parents=True, exist_ok=True)
    # señuelos que NO se pueden tocar
    (bm / "web/index.html").write_text("la webapp de bluemap")
    (bm / "web/assets").mkdir(parents=True, exist_ok=True)
    (bm / "web/assets/algo.js").write_text("assets de la webapp")
    (bm / "render.log").write_text("")
    (bm / "bluemap-cli.jar").write_bytes(b"jar de mentira")
    # un java de mentira que solo apunta con qué lo llamaron
    binp = raiz / "bin"
    binp.mkdir(exist_ok=True)
    (binp / "java").write_text(
        '#!/bin/bash\necho "[java] $*" >> "%s/java.txt"\nexit 0\n' % raiz)
    (binp / "java").chmod(0o755)
    panel = raiz / "panel/scripts"
    panel.mkdir(parents=True, exist_ok=True)
    shutil.copy(RAIZ / "scripts/render-mapa.sh", panel / "render-mapa.sh")
    return bm


def corre(raiz, *args):
    entorno = dict(os.environ)
    entorno["HOME"] = str(raiz)
    entorno["PATH"] = "%s:%s" % (raiz / "bin", entorno["PATH"])
    return subprocess.run(["bash", str(raiz / "panel/scripts/render-mapa.sh"), *args],
                          capture_output=True, text=True, timeout=120, env=entorno)


def azulejos(bm):
    return sorted(str(p.relative_to(bm)) for p in bm.glob("web/maps/**/*")
                  if p.is_file())


print("── borrar los azulejos, y nada más ──")
raiz = Path(tempfile.mkdtemp(prefix="repintar-"))
bm = monta(raiz)
ok(len(azulejos(bm)) == 2, "de partida hay azulejos dibujados (%d)" % len(azulejos(bm)))
r = corre(raiz, "--desde-cero")
ok(r.returncode == 0, "el script termina bien (código %d)" % r.returncode)
ok(not azulejos(bm), "los azulejos se borran (quedan %d)" % len(azulejos(bm)))
ok((bm / "web/index.html").exists(), "la webapp de BlueMap NO se toca")
ok((bm / "web/assets/algo.js").exists(), "ni sus assets")
ok((bm / "bluemap-cli.jar").exists(), "ni el propio jar")
java = (raiz / "java.txt").read_text() if (raiz / "java.txt").exists() else ""
ok("-r -f" in java, "y se renderiza forzando todo: %r" % java.strip().splitlines()[:1])

print("── si la carpeta configurada no es la del mapa ──")
# Un `root:` raro no puede acabar en un rm -rf de cualquier sitio.
raiz2 = Path(tempfile.mkdtemp(prefix="repintar-malo-"))
bm2 = monta(raiz2, root_conf='root: "/etc"')
r = corre(raiz2, "--desde-cero")
ok(Path("/etc/hostname").exists(), "no se ha tocado /etc (obviamente, pero por escrito)")
ok("NO borro nada" in (r.stdout + r.stderr), "el script se planta y lo dice")
ok(len(azulejos(bm2)) == 2, "y deja los azulejos como estaban")

print("── la nota para la madrugada ──")
raiz3 = Path(tempfile.mkdtemp(prefix="repintar-nota-"))
bm3 = monta(raiz3)
(bm3 / ".repintar-pendiente").write_text('{"quien":"juan"}')
r = corre(raiz3)                                   # sin argumentos: el cron normal
ok(not azulejos(bm3), "el render de la noche atiende la nota y repinta")
ok(not (bm3 / ".repintar-pendiente").exists(), "y la nota se borra al recogerla")
r = corre(raiz3)
java3 = (raiz3 / "java.txt").read_text()
ok(java3.count("-r -f") == 1, "la noche siguiente ya no repinta sola (%d veces)"
   % java3.count("-r -f"))

print("── leer el progreso del registro de BlueMap ──")
os.environ["PANEL_DIR"] = str(RAIZ)
os.environ["MC_DIR"] = str(raiz / "mc")
sys.path.insert(0, str(RAIZ))
import server                                                # noqa: E402

casa = Path(tempfile.mkdtemp(prefix="repintar-log-"))
(casa / "bluemap").mkdir(parents=True, exist_ok=True)
os.environ["HOME"] = str(casa)
server.Path = Path                                            # (por claridad)

LOG = casa / "bluemap/render.log"
import unittest.mock as mock                                  # noqa: E402
with mock.patch.object(Path, "home", staticmethod(lambda: casa)):
    LOG.write_text("")
    ok(server._render_progreso() == (None, ""), "sin registro, no se inventa un número")

    # una línea de verdad, con el formato que imprime el jar
    LOG.write_text("[INFO] Updating map 'overworld': 42.135% (ETA: 1h 12m)\n")
    pct, eta = server._render_progreso()
    ok(abs(pct - 42.135) < 0.01, "lee el porcentaje (%s)" % pct)
    ok(eta == "1h 12m", "y lo que queda (%r)" % eta)

    LOG.write_text("[INFO] Updating map 'overworld': 7.5%\n")
    pct, eta = server._render_progreso()
    ok(abs(pct - 7.5) < 0.01 and eta == "", "sin ETA también vale (%s, %r)" % (pct, eta))

    LOG.write_text("[INFO] Updating map 'x': 12%\n[INFO] Updating map 'x': 88.2% (ETA: 4m)\n")
    ok(abs(server._render_progreso()[0] - 88.2) < 0.01, "se queda con la última línea")

    LOG.write_text("[2026-09-15 04:00:00] borrando los azulejos de /x/maps…\n")
    ok(server._render_progreso() == (0.0, ""), "mientras borra, el progreso es 0")

for d in (raiz, raiz2, raiz3, casa):
    shutil.rmtree(d, ignore_errors=True)

print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
