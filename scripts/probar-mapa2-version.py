#!/usr/bin/env python3
"""
Que el mapa Explorar se dé cuenta —y lo diga— cuando se queda en otra versión.

POR QUÉ ASÍ
-----------
Minecraft se actualiza solo. De la pestaña Explorar, las tablas de estructuras
se recargan solas con el jar nuevo; los biomas no, porque salen de un proceso
Java COMPILADO contra el jar. Ese proceso se recompila al reiniciarse... salvo
si en la máquina no hay compilador: entonces `biomas-servicio.sh` arranca con
las clases de antes, lo escribe en su registro y sigue contestando igual de
rápido, con los biomas de la versión anterior.

Ese es el peor fallo posible: no peta, no tarda más, no deja un error donde
alguien mire. Solo está mal. Es el mismo que ya cazamos con las tablas, y la
lección de entonces fue que no basta con arreglarlo una vez: hay que hacer que
se vea la próxima.

Así que aquí se prueban las tres partes:

  · que se DETECTA (comparando la huella que deja el compilador con el jar);
  · que no se cría el lobo: sin huella todavía, la respuesta es «no lo sé», no
    «está viejo» — un aviso que sale cuando no toca deja de leerse a la tercera;
  · que el botón de arreglarlo hace lo que dice y lo cuenta, incluido el caso
    de que no haya compilador y no pueda.

El lector de biomas de verdad no se levanta aquí: haría falta un Minecraft y un
JDK. Lo que se suplanta es SOLO «¿contesta el servicio?»; la huella, el jar, el
reinicio y lo que se escribe en el registro son los de verdad.

Correr:  python3 scripts/probar-mapa2-version.py
"""
import importlib.util
import os
import shutil
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


def main():
    base = Path(tempfile.mkdtemp(prefix="mapa2-version-"))
    mc, panel, binp = base / "minecraft", base / "panel", base / "bin"
    (mc / "versions").mkdir(parents=True)
    (panel / "data").mkdir(parents=True)
    (panel / "java-clases").mkdir(parents=True)
    shutil.copytree(REPO / "scripts", panel / "scripts")
    shutil.copy(REPO / "nbt.py", panel / "nbt.py")
    binp.mkdir()
    sello = panel / "java-clases" / ".compilado-de"
    llamadas = base / "sudo.txt"

    def pon_jar(version, cuando):
        d = mc / "versions" / version
        d.mkdir(parents=True, exist_ok=True)
        j = d / ("server-%s.jar" % version)
        j.write_bytes(b"jar de mentira " + version.encode())
        os.utime(j, (cuando, cuando))
        os.utime(d, (cuando, cuando))
        return j

    def firma_de(jar):
        """La misma huella que escribe biomas-servicio.sh al compilar."""
        import hashlib
        st = jar.stat()
        fuente = panel / "scripts" / "Biomas.java"
        # el fuente va por CONTENIDO: su mtime lo cambia cualquier despliegue
        return "%s %d %d | %s" % (jar, int(st.st_mtime), st.st_size,
                                  hashlib.sha1(fuente.read_bytes()).hexdigest()[:16])

    # Un `sudo` de pega que apunta con qué lo llamaron y, además, hace lo que
    # haría el servicio de verdad al arrancar: recompilar contra el jar de ahora
    # y dejar su huella. Así el camino completo del botón se prueba de verdad.
    (binp / "sudo").write_text(
        "#!/bin/bash\n"
        'echo "$*" >> %s\n' % llamadas +
        '[ -n "$FALLA_SUDO" ] && exit 1\n'
        'JAR=$(ls -t %s/versions/*/server-*.jar | head -1)\n' % mc +
        "printf '%%s | %%s\\n' \"$(stat -c '%%n %%Y %%s' \"$JAR\")\" "
        "\"$(sha1sum %s/scripts/Biomas.java | cut -c1-16)\" > %s\n" % (panel, sello) +
        "exit 0\n")
    (binp / "sudo").chmod(0o755)

    ahora = time.time()
    viejo = pon_jar("26.2", ahora - 86400)
    nuevo = pon_jar("26.10", ahora)

    os.environ.update(MC_DIR=str(mc), PANEL_DIR=str(panel),
                      PANEL_SECRET="secreto-de-prueba-0123456789",
                      PATH="%s:%s" % (binp, os.environ["PATH"]))
    spec = importlib.util.spec_from_file_location("srv", REPO / "server.py")
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)               # ← el server.py DE VERDAD

    # ── 1 · qué versión hay puesta ───────────────────────────────────────────
    titulo("1 · cuál es la versión de ahora")
    ok(srv._mc_jar() == nuevo, "el jar es el más nuevo POR FECHA (%s)" % srv._mc_jar().name)
    ok(sorted([viejo.name, nuevo.name])[-1] == viejo.name,
       "(por nombre habría salido la 26.2: «26.2» va detrás de «26.10»)")
    srv._ver_cache.update(t=0, val=None)
    ok(srv.mc_version() == "26.10",
       "y mc_version() dice 26.10, no 26.2 (dijo %s)" % srv.mc_version())

    # ── 2 · detectar el desfase ──────────────────────────────────────────────
    titulo("2 · ¿están los biomas al día?")
    d = srv._m2_desfase()
    ok(d["al_dia"] is None, "sin huella todavía, no se inventa un veredicto (%r)" % d["al_dia"])
    ok("todavía no ha compilado" in d["motivo"], "y dice por qué: %r" % d["motivo"])

    sello.write_text(firma_de(viejo) + "\n")
    d = srv._m2_desfase()
    ok(d["al_dia"] is False, "compilado contra la 26.2 con la 26.10 puesta: viejo")
    ok("server-26.2.jar" in d["motivo"] and "server-26.10.jar" in d["motivo"],
       "y el motivo nombra las DOS versiones: %r" % d["motivo"])
    ok(d["biomas_jar"] == "server-26.2.jar" and d["jar"] == "server-26.10.jar",
       "los dos jars salen por separado, para poder enseñarlos")

    sello.write_text(firma_de(nuevo) + "\n")
    ok(srv._m2_desfase()["al_dia"] is True, "compilado contra la 26.10: al día")

    # tocar el jar (una reinstalación de la misma versión) también cuenta
    os.utime(nuevo, (ahora + 60, ahora + 60))
    d = srv._m2_desfase()
    ok(d["al_dia"] is False, "si el jar cambia debajo, deja de estar al día")
    ok("versión anterior del código" in d["motivo"] or "server-" in d["motivo"],
       "y no dice que sea otra versión si el nombre no cambió: %r" % d["motivo"])
    sello.write_text(firma_de(nuevo) + "\n")

    titulo("3 · ¿puede la máquina arreglarlo sola?")
    (binp / "javac").write_text("#!/bin/bash\necho 'javac 25'\n")
    (binp / "javac").chmod(0o755)
    ok(srv._hay_javac() is True, "con javac en el PATH, lo encuentra")
    # El «no» no se puede provocar quitando el de mentira: la máquina que corre
    # esta prueba suele tener su propio JDK, y `_hay_javac` mira también
    # /usr/lib/jvm. Para lo que sigue se suplanta la respuesta, que es lo único
    # que interesa: qué HACE el panel cuando no hay compilador.
    real_javac = srv._hay_javac
    sin_javac = lambda: False

    # ── 4 · el semáforo de Sistema ───────────────────────────────────────────
    titulo("4 · lo que se ve en Sistema")
    filas = {a["id"]: a for a in srv._automatismos()}
    ok("mapa2" in filas, "el mapa Explorar sale en «lo que se actualiza solo»")
    ok(filas["mapa2"]["ok"] is True, "en verde cuando está al día")
    ok("al día con server-26.10.jar" in filas["mapa2"]["estado"],
       "y dice contra qué versión: %r" % filas["mapa2"]["estado"])

    sello.write_text(firma_de(viejo) + "\n")
    filas = {a["id"]: a for a in srv._automatismos()}
    ok(filas["mapa2"]["ok"] is False, "en rojo cuando se ha quedado atrás")
    ok(filas["mapa2"]["nota"], "con una nota que explica qué pasa")

    srv._hay_javac = sin_javac
    filas = {a["id"]: a for a in srv._automatismos()}
    ok("biomas-instalar.sh" in filas["mapa2"]["nota"],
       "y si encima no hay compilador, dice cómo instalarlo")
    srv._hay_javac = real_javac
    ok("biomas-instalar.sh" not in
       {a["id"]: a for a in srv._automatismos()}["mapa2"]["nota"],
       "y no lo dice cuando sí lo hay (no se avisa de lo que no pasa)")

    # ── 5 · permisos del botón ───────────────────────────────────────────────
    titulo("5 · quién puede pulsarlo")
    srv.save_users({
        "juan":  {"hash": srv.hash_pw("contraseñalarga"), "role": "admin",
                  "perms": {}, "must_change": False},
        "bicho": {"hash": srv.hash_pw("contraseñalarga"), "role": "mod",
                  "perms": {}, "must_change": False}})
    srv.app.config["TESTING"] = True

    def cliente(quien, csrf=True):
        c = srv.app.test_client()
        if quien:
            c.set_cookie("panel_session",
                         srv.sign("%s|%d" % (quien, time.time() + 3600)),
                         domain="localhost")
        if csrf:
            c.environ_base["HTTP_X_PANEL"] = "1"
        return c

    admin, mod, fuera = cliente("juan"), cliente("bicho"), cliente(None)
    ok(fuera.post("/api/mapa2/actualizar").status_code == 401, "sin sesión: 401")
    ok(mod.post("/api/mapa2/actualizar").status_code == 403, "un mod no: 403")
    ok(cliente("juan", csrf=False).post("/api/mapa2/actualizar").status_code == 403,
       "sin la cabecera X-Panel tampoco (escudo anti-CSRF)")
    ok(fuera.get("/api/mapa2/version").status_code == 401,
       "y el estado de versión tampoco se cuenta a quien no ha entrado")

    # ── 6 · el botón ─────────────────────────────────────────────────────────
    titulo("6 · pulsar el botón")
    # Lo único suplantado: «¿contesta el lector?». Levantarlo de verdad pediría
    # un Minecraft y un JDK; lo que se prueba aquí es el camino del panel.
    class _Srv:
        def salud(self):
            return {"semilla": 1, "y": 64}
    srv._m2_srv = lambda: _Srv()

    srv._m2_est[("x", 0, 0)] = ["calculado con las tablas viejas"]
    srv._M2["jar"] = ("otro", 0, 0)
    r = admin.post("/api/mapa2/actualizar").get_json()
    ok(r["ok"], "el admin lo lanza: %r" % r["output"][:60])
    ok("mapa2" in srv._sys_busy, "queda marcado como trabajo en marcha")
    ok(admin.post("/api/mapa2/actualizar").get_json()["ok"] is False,
       "y pulsarlo otra vez no lanza una segunda recompilación")
    ok(admin.get("/api/mapa2/version").get_json()["trabajando"] is True,
       "el panel lo cuenta mientras dura")

    t0 = time.time()
    while "mapa2" in srv._sys_busy and time.time() - t0 < 90:
        time.sleep(0.5)
    ok("mapa2" not in srv._sys_busy, "termina y suelta la marca (%.0fs)" % (time.time() - t0))

    dicho = llamadas.read_text() if llamadas.exists() else ""
    ok("systemctl restart biomas" in dicho,
       "se reinicia el lector de biomas, que es lo que dispara la recompilación")
    ok(not srv._m2_est, "se tira lo calculado con las tablas de la versión vieja")
    ok(srv._M2["jar"] is None, "y las tablas se releerán del jar nuevo")
    ok(srv._m2_desfase()["al_dia"] is True, "al terminar, el mapa está al día")

    registro = srv.SYS_LOG.read_text() if srv.SYS_LOG.exists() else ""
    ok("[mapa2]" in registro, "queda escrito en el registro del sistema")
    ok("al día" in registro and "server-26.10.jar" in registro,
       "y el registro dice contra qué versión quedó")

    # ── 7 · cuando no puede ──────────────────────────────────────────────────
    titulo("7 · cuando no se puede arreglar solo")
    sello.write_text(firma_de(viejo) + "\n")
    os.environ["FALLA_SUDO"] = "1"
    antes = len(registro)
    ok(admin.post("/api/mapa2/actualizar").get_json()["ok"], "se acepta igual")
    t0 = time.time()
    while "mapa2" in srv._sys_busy and time.time() - t0 < 60:
        time.sleep(0.3)
    nuevo_log = srv.SYS_LOG.read_text()[antes:]
    ok("no pude reiniciar" in nuevo_log,
       "si el reinicio falla, se dice en vez de quedarse esperando: %r"
       % nuevo_log.strip().splitlines()[-1][-90:] if nuevo_log.strip() else "(vacío)")
    ok("mapa2" not in srv._sys_busy, "y la marca se suelta igual, no se queda pillada")
    del os.environ["FALLA_SUDO"]

    srv._hay_javac = sin_javac
    d = srv._m2_desfase()
    ok(d["al_dia"] is False and d["javac"] is False,
       "sin compilador y con el jar cambiado: viejo, y se sabe por qué")
    r = admin.post("/api/mapa2/actualizar").get_json()
    ok("compilador de Java" in r["output"],
       "y al pulsar el botón se avisa ANTES de esperar dos minutos en balde")

    t0 = time.time()
    while "mapa2" in srv._sys_busy and time.time() - t0 < 60:
        time.sleep(0.3)
    srv._hay_javac = real_javac

    # ── 8 · la cabecera de caché del azulejo ─────────────────────────────────
    titulo("8 · cuánto puede durar un azulejo en el navegador")
    # `immutable` no es un consejo: le dice al navegador que NO revalide ese
    # fichero nunca, ni con una recarga dura. Eso es verdad para el dibujo de un
    # generador concreto y mentira para «el azulejo 3,-2» a secas. Con la URL de
    # siempre, un bioma nuevo tarda un AÑO en verse: el servidor sirve lo bueno
    # y el navegador enseña lo de antes. Pasó de verdad con la 26.3, y costó dos
    # días y cuatro diagnósticos equivocados dar con esta línea.
    class _SrvFalso:
        def salud(self):
            return {"semilla": 1, "y": 64, "huella": "abc123"}

    class _BioFalso:
        NIVELES = [4, 8, 16]
        NoDisponible = RuntimeError

        @staticmethod
        def azulejo(*a, **k):
            return b"un png de mentira"

    srv._m2_srv = lambda: _SrvFalso()
    srv._m2_mods = lambda: (_BioFalso, None)

    r = admin.get("/api/mapa2/azulejo/16/3/-2.png?g=abc123")
    cc = r.headers.get("Cache-Control", "")
    ok(r.status_code == 200, "el azulejo se sirve (%d)" % r.status_code)
    ok("immutable" in cc and "max-age=31536000" in cc,
       "con la huella de ahora se puede guardar para siempre: %r" % cc)

    r = admin.get("/api/mapa2/azulejo/16/3/-2.png?g=huella-vieja")
    ok(r.headers.get("Cache-Control") == "no-store",
       "con una huella que ya no es, NO se marca eterno: %r"
       % r.headers.get("Cache-Control"))

    r = admin.get("/api/mapa2/azulejo/16/3/-2.png")
    ok(r.headers.get("Cache-Control") == "no-store",
       "y sin huella tampoco: %r" % r.headers.get("Cache-Control"))

    shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
    print()
    if fallos:
        print("\033[31m✘ %d fallo(s) de %d:\033[0m" % (len(fallos), len(fallos) + pasadas))
        for f in fallos:
            print("   ·", f)
        sys.exit(1)
    print("\033[32m✔ %d comprobaciones, todas bien\033[0m" % pasadas)
