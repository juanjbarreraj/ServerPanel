#!/usr/bin/env python3
"""
Prueba actualizar.py con un Mojang y un BlueMap de mentira.

Monta un ~/minecraft y un ~/panel falsos, sirve el manifiesto de versiones y la
release de BlueMap desde ficheros locales, y pone unos `screen`, `pgrep` y
`sudo` de pega que fingen un servidor que arranca —o que no—. Después corre el
actualizar.py DE VERDAD.

Lo que más importa comprobar aquí no es que actualice, es que **el mapa no se
pierda**: que se congele al actualizar, que el render se pare en seco mientras
esté congelado, y que si BlueMap todavía no puede con la versión nueva todo
quede exactamente como estaba.

Correr:  python3 scripts/probar-actualizar.py
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
ACTUALIZAR = AQUI / "actualizar.py"

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


def escribir(p: Path, texto):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(texto)


# ────────────────────────────────────────────────── el Mojang y el BlueMap falsos
def publicar_version(base: Path, version, sha1_malo=False):
    """Deja en disco un jar y el manifiesto que lo anuncia, como haría Mojang."""
    jar = base / "mojang" / ("server-%s.jar" % version)
    escribir(jar, "JAR %s" % version)
    crudo = jar.read_bytes()
    sha = hashlib.sha1(crudo).hexdigest()
    if sha1_malo:
        sha = "0" * 40
    vjson = base / "mojang" / ("%s.json" % version)
    escribir(vjson, json.dumps({"id": version, "downloads": {"server": {
        "url": jar.as_uri(), "sha1": sha, "size": len(crudo)}}}))
    manif = base / "mojang" / "manifest.json"
    escribir(manif, json.dumps({
        "latest": {"release": version, "snapshot": version + "-pre1"},
        "versions": [{"id": version, "type": "release", "url": vjson.as_uri(),
                      "releaseTime": "2026-09-20T10:00:00+00:00"}]}))
    return manif.as_uri()


def publicar_bluemap(base: Path, version):
    jar = base / "gh" / ("bluemap-%s-cli.jar" % version)
    escribir(jar, "BLUEMAP %s" % version)
    rel = base / "gh" / "latest.json"
    escribir(rel, json.dumps({"tag_name": "v" + version, "assets": [
        {"name": "bluemap-%s-cli.jar" % version, "browser_download_url": jar.as_uri()}]}))
    return rel.as_uri()


def montar(base: Path):
    mc, panel, bm, bin_ = base / "minecraft", base / "panel", base / "bluemap", base / "bin"
    estado = base / "estado"
    # el servidor que ya está instalado
    escribir(mc / "server.jar", "JAR 26.2")
    escribir(mc / "versions" / "26.2" / "server-26.2.jar", "JAR 26.2")
    escribir(mc / "logs" / "latest.log", "[10:00:00] [Server thread/INFO]: Done (9.9s)!\n")
    escribir(mc / "backup.sh", '#!/bin/bash\necho copia >> "$ESTADO/backup.log"\n')
    os.chmod(mc / "backup.sh", 0o755)

    escribir(bm / "bluemap-cli.jar", "BLUEMAP 5.23")
    escribir(bm / ".version", "5.23")

    # el render de pega: apunta que lo llamaron. Falla si se le dice que falle,
    # que es como se finge «BlueMap aún no soporta esta versión».
    escribir(panel / "scripts" / "render-mapa.sh", """#!/bin/bash
PANEL="$PANEL_DIR"
LOG="$RENDER_LOG"
CONGELADO="$PANEL/data/mapa-congelado.json"
if [ -f "$CONGELADO" ] && [ "$1" != "--descongelar" ]; then
  echo "congelado, no dibujo nada" >> "$LOG"
  exit 0
fi
echo "render $*" >> "$LOG"
[ -f "$ESTADO/render-falla" ] && exit 3
echo "azulejo nuevo" > "$ESTADO/azulejos.txt"
exit 0
""")
    escribir(panel / "get-icons.py", 'open(__import__("os").environ["ESTADO"]+"/iconos.log","a").write("x")\n')
    escribir(panel / "scripts" / "vigilancia.py",
             'open(__import__("os").environ["ESTADO"]+"/vigilancia.log","a").write("x")\n')
    for f in ("scripts/render-mapa.sh",):
        os.chmod(panel / f, 0o755)

    escribir(estado / "vivo", "1")
    escribir(estado / "azulejos.txt", "azulejos de la 26.2")
    bin_.mkdir(parents=True, exist_ok=True)
    escribir(bin_ / "pgrep", """#!/bin/bash
[ -f "$ESTADO/vivo" ] && { echo 4242; exit 0; }
exit 1
""")
    escribir(bin_ / "screen", """#!/bin/bash
echo "$*" >> "$ESTADO/consola.log"
case "$*" in *stop*) rm -f "$ESTADO/vivo" ;; esac
exit 0
""")
    # arrancar: el bundler se descomprime (crea versions/<ver>) y el log dice Done.
    # Si existe la marca «arranque-falla», finge una versión que no levanta.
    escribir(bin_ / "sudo", """#!/bin/bash
echo "$*" >> "$ESTADO/sudo.log"
case "$*" in
  *"systemctl restart"*)
    VER=$(sed -n 's/^JAR //p' "$MC_DIR/server.jar")
    mkdir -p "$MC_DIR/logs"
    if [ -f "$ESTADO/arranque-falla" ] && [ "$VER" = "$(cat "$ESTADO/arranque-falla")" ]; then
      echo "[10:00:00] [main/ERROR]: Failed to start the minecraft server" > "$MC_DIR/logs/latest.log"
      rm -f "$ESTADO/vivo"
    else
      mkdir -p "$MC_DIR/versions/$VER"
      echo "JAR $VER" > "$MC_DIR/versions/$VER/server-$VER.jar"
      echo "[10:00:00] [Server thread/INFO]: Done (9.9s)!" > "$MC_DIR/logs/latest.log"
      touch "$ESTADO/vivo"
    fi
    ;;
esac
exit 0
""")
    for n in ("pgrep", "screen", "sudo"):
        os.chmod(bin_ / n, 0o755)
    return mc, panel, bm, estado


def correr(base, *args, manifiesto=None, bmrel=None, espera_ok=True):
    env = dict(os.environ)
    env.update(MC_DIR=str(base / "minecraft"), PANEL_DIR=str(base / "panel"),
               BLUEMAP_DIR=str(base / "bluemap"), ESTADO=str(base / "estado"),
               RENDER_LOG=str(base / "render.log"),
               PATH="%s:%s" % (base / "bin", os.environ["PATH"]))
    if manifiesto:
        env["MC_MANIFIESTO"] = manifiesto
    if bmrel:
        env["BLUEMAP_RELEASES"] = bmrel
    r = subprocess.run([sys.executable, str(ACTUALIZAR)] + list(args) + ["--json"],
                       capture_output=True, text=True, env=env, timeout=300)
    try:
        d = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        d = {"ok": False, "mensaje": "(sin JSON) " + r.stdout[-300:] + r.stderr[-300:]}
    if espera_ok and not d.get("ok"):
        print("     ↳ %s" % d.get("mensaje"))
    return d


def render(base, *args):
    """Llama al render-mapa.sh de pega como lo haría el cron."""
    env = dict(os.environ)
    env.update(PANEL_DIR=str(base / "panel"), ESTADO=str(base / "estado"),
               RENDER_LOG=str(base / "render.log"), MC_DIR=str(base / "minecraft"))
    subprocess.run(["bash", str(base / "panel" / "scripts" / "render-mapa.sh")] + list(args),
                   env=env, capture_output=True, timeout=60)


def log_render(base):
    f = base / "render.log"
    return f.read_text() if f.exists() else ""


def main():
    base = Path(tempfile.mkdtemp(prefix="probar-actualizar-"))
    print("escenario en %s" % base)
    mc, panel, bm, estado = montar(base)
    manif = publicar_version(base, "26.3")
    bmrel = publicar_bluemap(base, "5.23")          # todavía no hay BlueMap nuevo
    congelado = panel / "data" / "mapa-congelado.json"

    # ── 1. comprobar ─────────────────────────────────────────────────────────
    titulo("1 · mirar si hay versión nueva")
    d = correr(base, "comprobar", manifiesto=manif)
    ok(d.get("ok"), "responde")
    ok(d.get("instalada") == "26.2", "sabe qué versión hay puesta (%s)" % d.get("instalada"))
    ok(d.get("ultima") == "26.3" and d.get("hay_nueva"), "y ve que hay una 26.3")
    ok(d.get("congelado") is None, "el mapa no está congelado de partida")

    # ── 2. la firma tiene que cuadrar ────────────────────────────────────────
    titulo("2 · un jar que no cuadra con su firma")
    malo = publicar_version(base / "malo", "26.3")
    publicar_version(base / "malo", "26.3", sha1_malo=True)
    d = correr(base, "actualizar", manifiesto=malo, espera_ok=False)
    ok(not d.get("ok") and "firma" in d.get("mensaje", ""), "se rechaza, y dice por qué")
    ok((mc / "server.jar").read_text().strip() == "JAR 26.2", "el jar de siempre sigue puesto")

    # ── 3. actualizar de verdad ──────────────────────────────────────────────
    titulo("3 · actualizar a la 26.3")
    d = correr(base, "actualizar", manifiesto=manif)
    ok(d.get("ok") and d.get("cambiado"), "actualiza (%s)" % d.get("mensaje", "")[:60])
    ok((mc / "server.jar").read_text().strip() == "JAR 26.3", "el jar nuevo está puesto")
    ok((mc / "actualizaciones" / "anterior-26.2.jar").exists(),
       "y el de antes queda guardado para poder deshacer")
    ok((estado / "backup.log").exists(), "hizo copia de seguridad ANTES de tocar nada")
    ok(d.get("instalada") == "26.3", "el servidor arrancó con la 26.3")
    ok((estado / "iconos.log").exists(), "regeneró los iconos de ítems")
    ok((estado / "vigilancia.log").exists(), "y el datapack de vigilancia")

    # ── 4. el mapa queda congelado y protegido ───────────────────────────────
    titulo("4 · el mapa se congela solo")
    ok(congelado.exists(), "queda la nota de mapa congelado")
    nota = json.loads(congelado.read_text())
    ok(nota.get("version") == "26.3", "la nota dice por qué versión (%s)" % nota.get("version"))
    ok((estado / "azulejos.txt").read_text().strip() == "azulejos de la 26.2",
       "y los azulejos de la 26.2 siguen ahí: el mapa se sigue viendo")
    (base / "render.log").unlink(missing_ok=True)
    render(base)                                     # como el cron de cada noche
    ok("congelado" in log_render(base) and "render " not in log_render(base),
       "el render de cada noche se para en seco y no dibuja nada")
    ok((estado / "azulejos.txt").read_text().strip() == "azulejos de la 26.2",
       "así que no puede pisar los azulejos buenos")

    # ── 5. BlueMap todavía no puede ──────────────────────────────────────────
    titulo("5 · se intenta descongelar y BlueMap aún no puede")
    (estado / "render-falla").write_text("1")
    (base / "render.log").unlink(missing_ok=True)
    d = correr(base, "mapa-al-dia", manifiesto=manif, bmrel=bmrel, espera_ok=False)
    ok(not d.get("ok") and "todavía no soporta" in d.get("mensaje", ""),
       "lo dice claro en vez de dejarlo a medias")
    ok(congelado.exists(), "el mapa sigue congelado")
    ok((estado / "azulejos.txt").read_text().strip() == "azulejos de la 26.2",
       "y los azulejos siguen intactos — no se ha perdido el mapa")
    ok((bm / "bluemap-cli.jar").read_text().strip() == "BLUEMAP 5.23",
       "el BlueMap de antes sigue puesto")

    # ── 6. sale BlueMap nuevo y ya sí ────────────────────────────────────────
    titulo("6 · sale BlueMap 5.24 y ahora sí")
    bmrel2 = publicar_bluemap(base / "nuevo", "5.24")
    (estado / "render-falla").unlink()
    (base / "render.log").unlink(missing_ok=True)
    d = correr(base, "mapa-al-dia", manifiesto=manif, bmrel=bmrel2)
    ok(d.get("ok"), "descongela (%s)" % d.get("mensaje", "")[:60])
    ok((bm / "bluemap-cli.jar").read_text().strip() == "BLUEMAP 5.24", "puso el BlueMap nuevo")
    ok((bm / ".version").read_text().strip() == "5.24", "y apunta qué versión tiene")
    ok(not congelado.exists(), "quitó la nota: el mapa vuelve a dibujarse solo")
    ok("render " in log_render(base), "lanzó el render")
    ok("--completo" not in log_render(base),
       "INCREMENTAL, no completo: los azulejos viejos valen y el mapa no se queda en blanco")
    ok((estado / "azulejos.txt").read_text().strip() == "azulejo nuevo", "y el mapa se actualizó")
    (base / "render.log").unlink(missing_ok=True)
    render(base)
    ok("render " in log_render(base), "el cron de cada noche vuelve a funcionar")

    # ── 7. si la versión nueva no arranca, se deshace sola ───────────────────
    titulo("7 · una versión que no arranca")
    manif4 = publicar_version(base / "v4", "26.4")
    (estado / "arranque-falla").write_text("26.4")
    d = correr(base, "actualizar", manifiesto=manif4, espera_ok=False)
    ok(not d.get("ok"), "avisa de que no salió bien")
    ok("devuelto" in d.get("mensaje", "").lower() or "he devuelto" in d.get("mensaje", ""),
       "y dice que ha vuelto atrás (%s)" % d.get("mensaje", "")[:80])
    ok((mc / "server.jar").read_text().strip() == "JAR 26.3", "el jar volvió a la 26.3")
    ok((estado / "vivo").exists(), "y el servidor está en marcha otra vez")
    ok(not congelado.exists(), "sin dejar el mapa congelado por una actualización que no fue")

    # ── 8. deshacer a mano ───────────────────────────────────────────────────
    titulo("8 · deshacer a mano")
    (estado / "arranque-falla").unlink()
    d = correr(base, "deshacer")
    ok(d.get("ok"), "vuelve a la versión anterior")
    ok((mc / "server.jar").read_text().strip() == "JAR 26.2", "el jar es el de antes")
    ok("MUNDO sigue convertido" in d.get("mensaje", ""),
       "y avisa de que el MUNDO no se deshace con esto")

    print("\n" + "─" * 60)
    if fallos:
        print("\033[31m%d fallos\033[0m de %d comprobaciones:" % (len(fallos), pasadas + len(fallos)))
        for f in fallos:
            print("   · %s" % f)
    else:
        print("\033[32m%d comprobaciones, todas bien\033[0m" % pasadas)
    print("escenario: %s" % base)
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
