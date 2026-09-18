#!/usr/bin/env python3
"""
Que el lector de biomas NO arranque con las clases de otra versión.

POR QUÉ ASÍ
-----------
Esta prueba existe por un fallo concreto, de los caros. `biomas-servicio.sh`
comprobaba si la compilación había ido bien así:

    if [ -f "$CLASES/califree/Biomas.class" ]; then

O sea: por EXISTENCIA del fichero. Cuando Minecraft pasó a la 26.3 y `javac`
falló —la 26.3 renombró métodos del generador—, el `.class` de la 26.2 seguía
en su sitio, así que el script dio la compilación por buena, escribió el sello
diciendo «compilado contra la 26.3» y arrancó el servicio con las clases de la
26.2. Resultado:

  · el servicio moría a los 15 s con un NoSuchMethodError, 101 veces seguidas;
  · el panel leía el sello, lo daba por al día, y no avisaba de nada;
  · en la pantalla solo ponía «el explorador no está encendido».

Dos horas de terminal para llegar a una línea. La lección no es «arregla el
if»: es que **una comprobación que puede salir bien por accidente no es una
comprobación**. Aquí se fija por escrito que:

  · si javac falla, el servicio se planta, lo dice, y borra el sello;
  · nunca se arranca con clases compiladas contra otro jar;
  · sin compilador, seguir con lo de antes vale solo si el jar no ha cambiado.

Se corre el `biomas-servicio.sh` DE VERDAD, con un javac y un java de mentira
que apuntan con qué los llamaron.

Correr:  python3 scripts/probar-biomas-arranque.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
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


class Escenario:
    """Un ~/minecraft y un ~/panel de mentira, con javac y java de pega."""

    def __init__(self):
        self.base = Path(tempfile.mkdtemp(prefix="biomas-arranque-"))
        self.mc = self.base / "minecraft"
        self.panel = self.base / "panel"
        self.bin = self.base / "bin"
        self.clases = self.panel / "java-clases"
        self.sello = self.clases / ".compilado-de"
        self.diario = self.base / "llamadas.txt"
        for d in (self.mc / "libraries", self.mc / "logs", self.panel / "scripts",
                  self.clases, self.bin):
            d.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "scripts" / "biomas-servicio.sh",
                    self.panel / "scripts" / "biomas-servicio.sh")
        shutil.copy(REPO / "scripts" / "Biomas.java",
                    self.panel / "scripts" / "Biomas.java")

        # javac de pega: escribe la clase y sale 0, o falla sin escribir nada,
        # según BIEN. Igual que el de verdad, no borra lo que ya hubiera.
        # OJO con `-version`: el script elige el compilador POR NÚMERO DE
        # VERSIÓN. Un javac de mentira que no conteste a `-version` se descarta
        # sin decir nada y se acaba usando el JDK de verdad de la máquina — la
        # prueba parecería pasar y no estaría probando esto.
        (self.bin / "javac").write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-version" ]; then echo "javac 999.0.1"; exit 0; fi\n'
            'echo "javac $*" >> %s\n' % self.diario +
            'if [ "${JAVAC_BIEN:-1}" = "1" ]; then\n'
            '  mkdir -p %s/califree\n' % self.clases +
            '  echo "clase nueva" > %s/califree/Biomas.class\n' % self.clases +
            "  exit 0\n"
            "fi\n"
            'echo "error: cannot find symbol: createLookup()" >&2\n'
            "exit 1\n")
        (self.bin / "javac").chmod(0o755)
        # `javac -version` lo llama el buscador de compiladores
        (self.bin / "java").write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "-version" ]; then echo "java 25" >&2; exit 0; fi\n'
            'echo "java $*" >> %s\n' % self.diario +
            "exit 0\n")
        (self.bin / "java").chmod(0o755)

    def jar(self, version, cuando=None):
        d = self.mc / "versions" / version
        d.mkdir(parents=True, exist_ok=True)
        j = d / ("server-%s.jar" % version)
        j.write_bytes(b"jar de mentira " + version.encode())
        if cuando:
            os.utime(j, (cuando, cuando))
        return j

    def clase_vieja(self):
        (self.clases / "califree").mkdir(parents=True, exist_ok=True)
        (self.clases / "califree" / "Biomas.class").write_text("clase de la 26.2")

    def firma(self, jar):
        import hashlib
        st = jar.stat()
        fuente = self.panel / "scripts" / "Biomas.java"
        # el fuente va por CONTENIDO: el mtime lo cambia cualquier despliegue
        return "%s %d %d | %s" % (jar, int(st.st_mtime), st.st_size,
                                  hashlib.sha1(fuente.read_bytes()).hexdigest()[:16])

    def corre(self, **entorno):
        e = dict(os.environ)
        e.update(MC_DIR=str(self.mc), PANEL_DIR=str(self.panel),
                 BIOMAS_SEMILLA="1244994422874902852",     # sin consola de Minecraft
                 PATH="%s:%s" % (self.bin, os.environ["PATH"]))
        e.update(entorno)
        return subprocess.run(
            ["bash", str(self.panel / "scripts" / "biomas-servicio.sh")],
            capture_output=True, text=True, timeout=120, env=e)

    def llamadas(self):
        return self.diario.read_text() if self.diario.exists() else ""

    def arranco(self):
        return "califree.Biomas" in self.llamadas()

    def limpia(self):
        shutil.rmtree(self.base, ignore_errors=True)


def main():
    ahora = time.time()

    # ── 1 · el caso que provocó todo esto ────────────────────────────────────
    titulo("1 · javac falla y hay un .class de la versión anterior")
    e = Escenario()
    viejo = e.jar("26.2", ahora - 86400)
    e.clase_vieja()
    e.sello.write_text(e.firma(viejo) + "\n")     # todo en orden con la 26.2
    nuevo = e.jar("26.3", ahora)                  # llega la versión nueva
    r = e.corre(JAVAC_BIEN="0")

    salida = r.stdout + r.stderr
    ok(r.returncode != 0, "el servicio se planta (código %d)" % r.returncode)
    ok("no compiló" in salida, "y dice que no compiló: %r"
       % next((l for l in salida.splitlines() if "no compiló" in l), "")[:80])
    ok("server-26.3.jar" in salida, "nombrando la versión contra la que falló")
    ok(not e.arranco(), "NO arranca con las clases de la 26.2 (que es el fallo real)")
    ok(not e.sello.exists(),
       "y borra el sello, para que el panel no dé el mapa por al día")
    ok(not (e.clases / "califree" / "Biomas.class").exists(),
       "las clases viejas se quitan antes de compilar, no pueden colarse")
    e.limpia()

    # ── 2 · el camino bueno ──────────────────────────────────────────────────
    titulo("2 · javac compila bien")
    e = Escenario()
    nuevo = e.jar("26.3", ahora)
    r = e.corre()
    salida = r.stdout + r.stderr
    ok("compilando contra server-26.3.jar" in salida, "compila contra el jar de ahora")
    ok("compilado" in salida, "y lo dice")
    ok(e.sello.read_text().strip() == e.firma(nuevo),
       "el sello queda apuntando a este jar exacto")
    ok(e.arranco(), "y el servicio arranca")
    ok("-cp" in e.llamadas() and "server-26.3.jar" in e.llamadas(),
       "con el jar nuevo en el classpath")
    e.limpia()

    # ── 3 · no volver a compilar por gusto ───────────────────────────────────
    titulo("3 · si no ha cambiado nada")
    e = Escenario()
    nuevo = e.jar("26.3", ahora)
    e.corre()
    antes = e.llamadas().count("javac")
    e.corre()
    ok(e.llamadas().count("javac") == antes,
       "la segunda vez no recompila (%d llamadas a javac)" % e.llamadas().count("javac"))
    ok(e.llamadas().count("califree.Biomas") == 2, "pero sí vuelve a arrancar")
    e.limpia()

    # ── 4 · sin compilador ───────────────────────────────────────────────────
    titulo("4 · máquina sin compilador de Java")
    e = Escenario()
    nuevo = e.jar("26.3", ahora)
    e.corre()                                    # deja clases y sello buenos
    # se toca el fuente DE VERDAD (una línea nueva, no solo la fecha): cambia la
    # firma, pero el JAR es el mismo. Tocar solo el mtime ya no cuenta como
    # cambio, a propósito: eso lo hace cualquier despliegue.
    fuente = e.panel / "scripts" / "Biomas.java"
    sola_fecha = e.sello.read_text().strip()
    os.utime(fuente, (ahora + 100, ahora + 100))
    ok(e.firma(nuevo) == sola_fecha,
       "cambiar solo la FECHA del fuente no cambia la firma (Commit+Sync)")
    fuente.write_text(fuente.read_text() + "\n// un cambio de verdad\n")
    r = e.corre(BIOMAS_JAVAC="/no/existe/javac")
    salida = r.stdout + r.stderr
    ok(r.returncode == 0, "mismo jar y solo cambió el fuente: sigue adelante")
    ok("el jar no ha cambiado" in salida, "y explica por qué se fía: %r"
       % next((l for l in salida.splitlines() if "no ha cambiado" in l), "")[-70:])
    ok(e.llamadas().count("califree.Biomas") == 2, "arranca igual")

    # ahora sí cambia el jar: eso ya no se puede consentir
    e.jar("26.4", ahora + 200)
    antes = e.llamadas().count("califree.Biomas")
    r = e.corre(BIOMAS_JAVAC="/no/existe/javac")
    salida = r.stdout + r.stderr
    ok(r.returncode != 0, "jar nuevo y sin compilador: se planta (código %d)" % r.returncode)
    ok("OTRA versión" in salida, "y dice exactamente por qué")
    ok("biomas-instalar.sh" in salida, "con la orden para arreglarlo")
    ok(e.llamadas().count("califree.Biomas") == antes,
       "y NO arranca con las clases equivocadas")
    e.limpia()

    # ── 5 · el fallo de la tubería, por escrito ──────────────────────────────
    titulo("5 · el código de salida que se miraba mal")
    e = Escenario()
    e.jar("26.3", ahora)
    r = e.corre(JAVAC_BIEN="0")
    ok(r.returncode != 0,
       "un javac que falla NO puede acabar en un arranque, aunque la tubería "
       "termine en un `sed` que siempre sale 0")
    e.limpia()


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
