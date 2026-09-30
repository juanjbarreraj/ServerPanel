#!/usr/bin/env python3
"""
El buscador de sitios con muchos biomas, contra un mundo de mentira.

Se levanta un lector de biomas falso que habla el mismo protocolo que el de
verdad (`/salud`, `/leyenda`, `/cuadro`) y devuelve un mundo inventado **donde
ya sabemos la respuesta**: casi todo océano, un rincón con seis biomas y otro
con doce. Si el guion no señala el de doce, está mal.

Un mundo de mentira con la respuesta escondida dentro es la única forma de
comprobar esto: contra el mundo real no hay contra qué comparar — cualquier
resultado «parece» plausible, que es justo lo que no sirve.

Correr:  python3 scripts/probar-biomas-concentracion.py
"""
import http.server, importlib.util, json, socket, subprocess, sys, threading
from pathlib import Path

AQUI = Path(__file__).resolve().parent
REPO = AQUI.parent
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


# ── el mundo de mentira ──────────────────────────────────────────────────
# Todo océano (bioma 0), salvo dos rincones:
#   · EL BUENO: 12 biomas distintos en 200×200 bloques, centrado en 1600,-1200
#   · el señuelo: 6 biomas en la misma superficie, centrado en -2400,800
# El señuelo está para que no valga con «devolver el primero que encuentre».
BUENO = (1600, -1200)
SENUELO = (-2400, 800)
LADO_RINCON = 200


def bioma_en(x, z):
    """Cada rincón es una cuadrícula de biomas, no franjas.

    🔴 Con franjas (que varían solo en x) cualquier ventana que roce el borde
    del rincón por arriba o por abajo ya ve casi todos los biomas, y entonces
    hay empates por todas partes: la prueba pasaba señalando un sitio pegado al
    rincón en vez del rincón. Con una cuadrícula, solo la ventana bien centrada
    los ve todos — que es lo que hay que comprobar.
    """
    for centro, (nx, nz), base in ((BUENO, (4, 3), 1), (SENUELO, (3, 2), 40)):
        dx, dz = x - centro[0] + LADO_RINCON // 2, z - centro[1] + LADO_RINCON // 2
        if 0 <= dx < LADO_RINCON and 0 <= dz < LADO_RINCON:
            i = min(nx - 1, dx * nx // LADO_RINCON)
            j = min(nz - 1, dz * nz // LADO_RINCON)
            return base + j * nx + i
    return 0


LEYENDA = {0: "minecraft:ocean"}
for i in range(1, 13):
    LEYENDA[i] = "minecraft:bueno_%02d" % i
for i in range(40, 46):
    LEYENDA[i] = "minecraft:senuelo_%02d" % (i - 39)


class Falso(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        if u.path == "/salud":
            self._json({"ok": True, "semilla": 1244994422874902852,
                        "hilos": 2, "biomas": len(LEYENDA), "huella": "prueba"})
        elif u.path == "/leyenda":
            self._json({str(k): v for k, v in LEYENDA.items()})
        elif u.path == "/cuadro":
            x0, z0 = int(q.get("x", 0)), int(q.get("z", 0))
            n, paso = int(q.get("n", 256)), int(q.get("paso", 4))
            fuera = bytearray(n * n)
            for j in range(n):
                z = z0 + j * paso
                for i in range(n):
                    fuera[j * n + i] = bioma_en(x0 + i * paso, z)
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(fuera)))
            self.end_headers()
            self.wfile.write(bytes(fuera))
        else:
            self.send_error(404)

    def _json(self, d):
        b = json.dumps(d).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def puerto_libre():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def main():
    puerto = puerto_libre()
    srv = http.server.HTTPServer(("127.0.0.1", puerto), Falso)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("lector de mentira en el puerto %d" % puerto)

    guion = REPO / "scripts" / "biomas-concentracion.py"
    texto = guion.read_text().replace("http://127.0.0.1:25580",
                                      "http://127.0.0.1:%d" % puerto)
    copia = REPO / "scripts" / ".conc-prueba.py"
    copia.write_text(texto)

    def corre(*args):
        return subprocess.run([sys.executable, str(copia)] + list(args),
                              capture_output=True, text=True, timeout=600)

    try:
        titulo("1 · mide antes de prometer, y no calcula nada")
        r = corre("--radio", "3000", "--paso", "20", "--solo-medir")
        ok(r.returncode == 0, "sale bien (%d) %s" % (r.returncode, r.stderr[-200:]))
        ok("ESTIMADO" in r.stdout, "dice cuánto va a tardar antes de empezar")
        ok("SITIOS CON MÁS BIOMAS" not in r.stdout,
           "y con --solo-medir no llega a calcular nada")

        titulo("2 · encuentra el sitio bueno, no el señuelo")
        r = corre("--radio", "3000", "--paso", "20", "--ventana", "200",
                  "--cuantos", "3", "--si")
        ok(r.returncode == 0, "termina bien (%d) %s" % (r.returncode, r.stderr[-300:]))
        salida = r.stdout
        lineas = [l for l in salida.splitlines() if "biomas   ·   x " in l]
        ok(bool(lineas), "saca una lista de sitios (%d)" % len(lineas))
        if lineas:
            primero = lineas[0]
            cuantos = int(primero.split()[1])
            x = int(primero.split("x ")[1].split()[0])
            z = int(primero.split("z ")[1].split()[0])
            ok(cuantos >= 12,
               "el primero ve los 12 biomas del rincón bueno (%d)" % cuantos)
            ok(abs(x - BUENO[0]) <= 60 and abs(z - BUENO[1]) <= 60,
               "y está centrado donde lo escondimos: x %d z %d (esperado %d,%d)"
               % (x, z, BUENO[0], BUENO[1]))
            ok("bueno_01" in salida and "bueno_12" in salida,
               "y enseña los nombres de los biomas que hay dentro")
            ok(not (abs(x - SENUELO[0]) <= 300 and abs(z - SENUELO[1]) <= 300),
               "🔴 no se queda con el señuelo de 6, que también supera el listón")

        titulo("3 · los sitios que salen no se pisan entre ellos")
        # Sin esto, los diez primeros serían el mismo rincón movido un paso.
        sitios = []
        for l in lineas:
            sitios.append((int(l.split("x ")[1].split()[0]),
                           int(l.split("z ")[1].split()[0])))
        juntos = [(a, b) for k, a in enumerate(sitios) for b in sitios[k + 1:]
                  if abs(a[0] - b[0]) < 200 and abs(a[1] - b[1]) < 200]
        ok(not juntos, "ninguno a menos de una ventana de otro (%d solapados)"
           % len(juntos))

        titulo("4 · si no hay lector, lo dice claro")
        texto2 = guion.read_text().replace("http://127.0.0.1:25580",
                                           "http://127.0.0.1:1")
        copia.write_text(texto2)
        r = corre("--solo-medir")
        ok(r.returncode == 2, "sale con código 2 (%d)" % r.returncode)
        ok("systemctl restart biomas" in r.stdout,
           "y dice cómo arreglarlo, en vez de soltar una traza de Python")

        titulo("5 · una ventana más grande que la zona no revienta")
        copia.write_text(texto)
        r = corre("--radio", "300", "--paso", "20", "--ventana", "5000", "--si")
        ok(r.returncode == 1 and "más grande que la zona" in r.stdout,
           "avisa y sale, sin traza")
    finally:
        copia.unlink(missing_ok=True)
        srv.shutdown()


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
