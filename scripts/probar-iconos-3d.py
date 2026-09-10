#!/usr/bin/env python3
"""
Comprueba que sobre el mapa 3D (BlueMap) SOLO quedan los lugares del server.

POR QUÉ EXISTE
--------------
Los iconos de estructuras se fueron a la pestaña «Explorar», que los calcula de
la semilla. Para quitarlos del mapa 3D bastaba con dejar de escribirlos en las
configs de BlueMap… y se comprobó que el generador ya los dejaba fuera. Aun así
seguían viéndose en el mapa.

La lección: mirar el generador no vale. Lo que se ve en el navegador es UN
fichero —<mapas>/<dimensión>/live/markers.json— que BlueMap reescribe entero en
cada `--markers`. Esta prueba recorre la cadena entera con el BlueMap de verdad
y termina leyendo ESE fichero, que es el único testigo que no miente.

QUÉ PRUEBA
----------
  tubería  monta un ~/bluemap de mentira con las configs reales, le planta
           iconos de estructuras de una versión vieja, corre marcadores.sh y
           comprueba que desaparecen. Luego sabotea el generador a propósito
           para comprobar que el aviso salta cuando NO desaparecen.
  panel    abre el panel en un navegador y comprueba que la pestaña Mapa dice
           la verdad sobre lo que hay publicado, en verde y en rojo.

Uso:
    python3 scripts/probar-iconos-3d.py             # las dos, saltando lo que falte
    python3 scripts/probar-iconos-3d.py --tuberia
    python3 scripts/probar-iconos-3d.py --panel
    PANEL_URL=http://127.0.0.1:8099 PANEL_USER=juan PANEL_PASS=... …
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FALLOS = []
SALTADAS = []


def afirmar(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


def saltar(por_que):
    print("  — saltada: " + por_que)
    SALTADAS.append(por_que)


# El bloque tal y como lo escribía la versión que SÍ ponía estructuras. Es el
# punto de partida de la prueba: si marcadores.sh no lo borra, el mapa 3D se
# queda con los iconos igual que le pasó al server de verdad.
BEGIN = "# >>> CALIFREE MARKERS (autogenerado por scan-structures.py — no editar)"
END = "# <<< CALIFREE MARKERS"
BLOQUE_VIEJO = BEGIN + """
marker-sets: {
  lugares: {
    label: "Lugares del server"
    toggleable: true
    default-hidden: false
    sorting: 0
    markers: {
      m0: { type: "poi", label: "Base vieja", position: { x: 1, y: 64, z: 1 }, icon: "assets/markers/landmark.png", anchor: { x: 16, y: 16 }, min-distance: 0, max-distance: 100000, listed: true }
    }
  }
  est_village: {
    label: "Aldeas"
    toggleable: true
    sorting: 1
    markers: {
      v0: { type: "poi", label: "Aldea", position: { x: 100, y: 64, z: 100 }, icon: "assets/markers/village.png", anchor: { x: 16, y: 16 }, min-distance: 0, max-distance: 5000, listed: true }
      v1: { type: "poi", label: "Aldea", position: { x: 700, y: 64, z: 300 }, icon: "assets/markers/village.png", anchor: { x: 16, y: 16 }, min-distance: 0, max-distance: 5000, listed: true }
    }
  }
  est_mansion: {
    label: "Mansiones"
    toggleable: true
    sorting: 2
    markers: {
      w0: { type: "poi", label: "Mansión", position: { x: 256, y: 90, z: 832 }, icon: "assets/markers/mansion.png", anchor: { x: 16, y: 16 }, min-distance: 0, max-distance: 100000, listed: true }
    }
  }
}
""" + END + "\n"

LUGARES = [
    {"id": "a1", "name": "Base de Jakobino", "x": 120, "y": 70, "z": -340,
     "icon": "landmark", "dim": "overworld"},
    {"id": "a2", "name": "Granja de hierro", "x": -80, "y": 64, "z": 210,
     "icon": "farm", "dim": "overworld"},
    {"id": "a3", "name": "Puente del Nether", "x": 10, "y": 40, "z": 10,
     "icon": "danger", "dim": "nether"},
]


def conjuntos_publicados(mapas):
    """{dimensión: {conjunto: nº de marcadores}} leyendo lo que ve el navegador."""
    import gzip
    fuera = {}
    for d in sorted(p for p in mapas.iterdir() if p.is_dir()):
        crudo = None
        for cand in (d / "live/markers.json", d / "live/markers.json.gz"):
            if cand.exists():
                b = cand.read_bytes()
                crudo = gzip.decompress(b) if cand.suffix == ".gz" else b
                break
        if crudo is None:
            continue
        try:
            datos = json.loads(crudo)
        except Exception:
            continue
        fuera[d.name] = {k: len((v or {}).get("markers") or {}) for k, v in datos.items()}
    return fuera


def montar(casa, jar, configs):
    """Un ~/ de mentira con su panel y su bluemap, listo para marcadores.sh."""
    panel = casa / "panel"
    (panel / "data").mkdir(parents=True)
    # los scripts y los iconos son los DE VERDAD; solo data/ es de mentira, que
    # es lo único que la prueba tiene derecho a ensuciar
    for nombre in ("scripts", "static", "nbt.py"):
        (panel / nombre).symlink_to(RAIZ / nombre)
    (panel / "data/markers.json").write_text(json.dumps(LUGARES))
    (panel / "data/structures.json").write_text(json.dumps(
        {"overworld": [{"kind": "village", "id": "minecraft:village_plains",
                        "x": 100, "y": 64, "z": 100}], "nether": [], "end": []}))

    bm = casa / "bluemap"
    bm.mkdir()
    shutil.copytree(configs, bm / "config")
    shutil.copy2(jar, bm / "bluemap-cli.jar")
    # web como ENLACE, igual que en el server: es lo que hace que lo que escribe
    # BlueMap sea lo que sirve la web
    servido = casa / "servido"
    servido.mkdir()
    (bm / "web").symlink_to(servido)
    for conf in sorted((bm / "config/maps").glob("*.conf")):
        t = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "",
                   conf.read_text(), flags=re.S)
        conf.write_text(t.rstrip() + "\n\n" + BLOQUE_VIEJO)
    return panel, bm


def correr_marcadores(casa):
    entorno = dict(os.environ, HOME=str(casa))
    entorno.pop("BLUEMAP_DIR", None)
    entorno.pop("PANEL_DIR", None)
    r = subprocess.run(["bash", str(casa / "panel/scripts/marcadores.sh")],
                       capture_output=True, text=True, timeout=1800, env=entorno)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def prueba_tuberia():
    print("── la tubería: configs → BlueMap → lo que ve el navegador ──")
    if not shutil.which("java"):
        saltar("no hay java")
        return
    jar = Path(os.environ.get("BLUEMAP_JAR", Path.home() / "bluemap/bluemap-cli.jar"))
    if not jar.exists():
        saltar("no encuentro bluemap-cli.jar (ponlo en BLUEMAP_JAR)")
        return
    configs = Path(os.environ.get("BLUEMAP_CONFIG", Path.home() / "bluemap/config"))
    if not (configs / "maps").is_dir():
        saltar("no encuentro las configs de BlueMap en %s" % configs)
        return

    tmp = Path(tempfile.mkdtemp(prefix="iconos3d-"))
    try:
        casa = tmp / "casa"
        casa.mkdir()
        panel, bm = montar(casa, jar, configs)
        mapas = bm / "web/maps"

        # 1) el punto de partida: iconos de estructuras publicados
        subprocess.run(["java", "-jar", "bluemap-cli.jar", "--markers"],
                       cwd=bm, capture_output=True, timeout=900)
        antes = conjuntos_publicados(mapas)
        afirmar(any("est_village" in c for c in antes.values()),
                "de partida el mapa 3D tiene iconos de estructuras: %r" % antes)

        # 2) el botón del panel
        codigo, salida = correr_marcadores(casa)
        afirmar(codigo == 0, "marcadores.sh termina bien (código %s)" % codigo)
        despues = conjuntos_publicados(mapas)
        sobran = {d: [k for k in c if k != "lugares"] for d, c in despues.items()}
        sobran = {d: v for d, v in sobran.items() if v}
        afirmar(not sobran, "ya no queda ningún icono de estructura: %r" % (sobran or despues))
        afirmar(despues.get("overworld", {}).get("lugares") == 2,
                "y siguen los 2 lugares del overworld: %r" % despues.get("overworld"))
        # el nether solo si esta instalación tiene ese mapa configurado
        if "nether" in despues:
            afirmar(despues["nether"].get("lugares") == 1,
                    "y el del nether: %r" % despues["nether"])
        afirmar("solo quedan los lugares" in salida,
                "el script lo dice en voz alta")
        afirmar("NO es un enlace" not in salida,
                "no se queja de la carpeta web (es un enlace, como en el server)")

        # 3) que el aviso salte cuando NO se limpia. Sin esto la comprobación
        #    podría estar diciendo «✔» sin mirar nada.
        casa2 = tmp / "casa2"
        casa2.mkdir()
        panel2, bm2 = montar(casa2, jar, configs)
        propio = casa2 / "guion"
        shutil.copytree(RAIZ / "scripts", propio)
        (panel2 / "scripts").unlink()
        (panel2 / "scripts").symlink_to(propio)
        g = propio / "scan-structures.py"
        g.write_text(g.read_text().replace(
            '    out.append("}\\n" + END + "\\n")',
            '    out.append(\'  est_village: { label: "Aldeas" markers: { v0: '
            '{ type: "poi", label: "Aldea", position: { x: 1, y: 64, z: 1 }, '
            'icon: "assets/markers/village.png", min-distance: 0, '
            'max-distance: 5000 } } }\\n\')\n'
            '    out.append("}\\n" + END + "\\n")', 1))
        codigo2, salida2 = correr_marcadores(casa2)
        afirmar("SIGUE HABIENDO iconos" in salida2,
                "con el generador saboteado, avisa")
        afirmar("est_village" in salida2, "y dice cuál es")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def prueba_panel():
    print("── el panel: la pestaña Mapa dice la verdad ──")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        saltar("falta playwright (pip install playwright && playwright install chromium)")
        return
    base = os.environ.get("PANEL_URL", "http://127.0.0.1:8099")
    usuario = os.environ.get("PANEL_USER", "juan")
    clave = os.environ.get("PANEL_PASS", "prueba1234")

    # el fichero que lee el panel es el mismo que ve el navegador del mapa
    bm = Path(os.environ.get("BLUEMAP_DIR", Path.home() / "bluemap"))
    vivo = bm / "web/maps/overworld/live/markers.json"
    if not vivo.exists():
        saltar("no hay %s todavía (corre antes marcadores.sh)" % vivo)
        return

    with sync_playwright() as pw:
        nav = pw.chromium.launch(args=["--no-proxy-server"])
        pag = nav.new_page(viewport={"width": 1280, "height": 900})
        errores = []
        pag.on("pageerror", lambda e: errores.append(str(e)))
        # El <iframe> apunta a /map/. Si en la maqueta no está BlueMap, el panel
        # se sirve a sí mismo y su propia cabecera frame-ancestors lo rechaza:
        # es ruido del banco de pruebas, no del producto.
        ruido = ("Failed to load resource", "frame-ancestors")
        pag.on("console", lambda m: errores.append("console.error: " + m.text)
               if m.type == "error" and not any(r in m.text for r in ruido) else None)

        # Entrar con un reintento: rellenar y pulsar en el mismo suspiro pilla a
        # veces el formulario a medio montar y manda la contraseña incompleta,
        # que sale como un 401 de mentira. Se comprueba lo escrito antes de
        # pulsar y, si aun así no entra, se intenta otra vez.
        def entrar():
            pag.goto(base, wait_until="domcontentloaded")
            pag.wait_for_timeout(1200)
            pag.evaluate("() => showLogin && showLogin()")
            pag.wait_for_selector("#li-user", state="visible", timeout=8000)
            pag.wait_for_selector("#li-pass", state="visible", timeout=8000)
            pag.fill("#li-user", usuario)
            pag.fill("#li-pass", clave)
            if (pag.input_value("#li-user"), pag.input_value("#li-pass")) != (usuario, clave):
                return False
            pag.click("#li-btn")
            try:
                pag.wait_for_selector("#tabs button", timeout=15000)
                return True
            except Exception:
                return False

        dentro = entrar() or entrar()
        afirmar(dentro, "sesión iniciada")
        if not dentro:
            nav.close()
            return

        pag.click("#tabbtn-map")
        pag.wait_for_timeout(1500)
        afirmar(not pag.locator("#mk-scan-row").is_hidden(), "salen los botones de admin")

        pag.wait_for_function(
            "() => document.getElementById('mk-estado').innerText.trim().length > 0",
            timeout=10000)
        t = pag.locator("#mk-estado").inner_text()
        afirmar("solo están los lugares" in t, "en verde dice que solo quedan los lugares")
        afirmar("overworld" in t, "y lista las dimensiones")
        afirmar("⚠" not in t, "sin avisos")

        guarda = vivo.read_text()
        try:
            sucio = json.loads(guarda)
            sucio["est_village"] = {"label": "Aldeas", "markers": {"v0": {}, "v1": {}, "v2": {}}}
            vivo.write_text(json.dumps(sucio))
            pag.evaluate("() => mkEstadoIconos()")
            pag.wait_for_function(
                "() => document.getElementById('mk-estado').innerText.includes('todavía hay iconos')",
                timeout=10000)
            t = pag.locator("#mk-estado").inner_text()
            afirmar("est_village (3)" in t, "en rojo nombra y cuenta lo que sobra")
            afirmar("Actualizar los iconos" in t, "y dice qué botón pulsar")
        finally:
            vivo.write_text(guarda)

        pag.evaluate("() => mkEstadoIconos()")
        pag.wait_for_function(
            "() => document.getElementById('mk-estado').innerText.includes('solo están los lugares')",
            timeout=10000)
        afirmar(True, "vuelve a verde al arreglarlo")

        pedidas = []
        pag.on("request", lambda r: pedidas.append(r.url) if "/api/system/" in r.url else None)
        pag.evaluate("() => mkMarcadores()")
        pag.wait_for_timeout(1200)
        afirmar(any("map_markers" in u for u in pedidas),
                "el botón llama a /api/system/map_markers")
        afirmar(not errores, "consola limpia: %r" % errores[:3])
        nav.close()


def main():
    cuales = [a for a in sys.argv[1:] if a.startswith("--")]
    todo = not cuales
    if todo or "--tuberia" in cuales:
        prueba_tuberia()
    if todo or "--panel" in cuales:
        prueba_panel()
    print()
    if FALLOS:
        print("%d FALLO(S):" % len(FALLOS))
        for f in FALLOS:
            print("  · " + f)
    else:
        print("todo bien" + (" (%d saltada(s))" % len(SALTADAS) if SALTADAS else ""))
    return 1 if FALLOS else 0


if __name__ == "__main__":
    sys.exit(main())
