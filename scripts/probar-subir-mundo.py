#!/usr/bin/env python3
"""
Subir un mundo: que el diálogo no bloquee nada y que el archivo llegue entero.

POR QUÉ ASÍ
-----------
El fallo que provocó esta prueba: el `<input type="file">` llevaba
`accept=".zip,.tar,.gz,.tgz"`, y el diálogo de macOS APAGA el botón «Abrir» para
todo lo que no case con esa lista. Bastaba que el mundo se llamara de otra forma
para que no hubiera manera de subirlo — y sin un solo mensaje que dijera por qué,
porque el navegador no avisa: simplemente no deja pulsar.

Lo peor es que el servidor nunca tuvo ese problema: abre el archivo con
`zipfile.is_zipfile` y `tarfile.open`, que miran DENTRO y no el nombre. O sea que
el panel rechazaba en la puerta cosas que el servidor habría aceptado.

Así que aquí se sube de todo: un zip llamado zip, un tar.gz llamado tar.gz, un
zip llamado `copia-del-mundo` a secas (sin extensión), y un archivo que no es un
mundo. Los tres primeros tienen que subir; el cuarto tiene que decir por qué no.

Hace falta:  pip install playwright && playwright install chromium
Correr:      python3 scripts/probar-subir-mundo.py
"""
import io
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_falso import PanelFalso, foto                     # noqa: E402

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("falta playwright:  pip install playwright && playwright install chromium")
    sys.exit(2)

FALLOS = []


def ok(cond, que):
    print(("  ✔ " if cond else "  ✘ ") + que)
    if not cond:
        FALLOS.append(que)


# ── mundos de mentira, pero archivos de verdad ───────────────────────────────
tmp = Path(tempfile.mkdtemp(prefix="mundos-"))
NIVEL = b"\x0a\x00\x00\x0a\x00\x04Data\x00"      # un level.dat mínimo, no se lee aquí


def hace_zip(ruta):
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("world/level.dat", NIVEL)
        z.writestr("world/region/r.0.0.mca", b"\x00" * 9000)
    return ruta


def hace_targz(ruta):
    with tarfile.open(ruta, "w:gz") as t:
        for nombre, datos in (("world/level.dat", NIVEL),
                              ("world/region/r.0.0.mca", b"\x00" * 9000)):
            info = tarfile.TarInfo(nombre); info.size = len(datos)
            t.addfile(info, io.BytesIO(datos))
    return ruta


ARCHIVOS = [
    ("mundo.zip", hace_zip(tmp / "mundo.zip"), True, "un .zip normal"),
    ("mundo.tar.gz", hace_targz(tmp / "mundo.tar.gz"), True, "un .tar.gz normal"),
    ("copia-del-mundo", hace_zip(tmp / "copia-del-mundo"), True,
     "un zip SIN extensión (el caso que antes no se podía ni elegir)"),
    ("mundo.tgz", hace_targz(tmp / "mundo.tgz"), True, "un .tgz"),
]
(tmp / "no-es-un-mundo.txt").write_text("esto no es un mundo" * 500)

pf = PanelFalso()
with sync_playwright() as pw:
    nav = pw.chromium.launch(args=["--no-proxy-server"])
    pag = nav.new_page(viewport={"width": 1280, "height": 900})
    errores = []
    pag.on("pageerror", lambda e: errores.append(str(e)))
    pag.on("console", lambda m: errores.append("console: " + m.text)
           if m.type == "error" and "Failed to load resource" not in m.text else None)
    pag.route("**/*", pf.enruta)

    pag.goto(pf.BASE, wait_until="domcontentloaded")
    pag.wait_for_selector("#tabs button", timeout=15000)
    pag.click("#tabbtn-mundo")
    pag.wait_for_timeout(900)

    # ── la puerta: que el diálogo del sistema no filtre ───────────────────
    print("── el diálogo de elegir archivo ──")
    acc = pag.get_attribute("#mu-file", "accept")
    ok(not acc, "el campo no filtra por extensión (accept=%r)" % acc)
    ok(pag.locator("#mu-drop").count() == 1, "está la zona de soltar")

    # ── subir de todo ─────────────────────────────────────────────────────
    print("── subiendo mundos ──")
    for nombre, ruta, debe, que in ARCHIVOS:
        pf.subidas.clear()
        pag.set_input_files("#mu-file", str(ruta))
        pag.wait_for_timeout(1800)
        subido = sum(pf.subidas.values())
        peso = ruta.stat().st_size
        bien = subido == peso                # exacto: ni un byte de más ni de menos
        ok(bien if debe else not subido,
           "%s → %s (%d bytes de %d)" % (nombre, que, subido, peso))
        if pag.locator("#mu-modal.open").count():
            pag.evaluate("() => muCancelar()")
            pag.wait_for_timeout(500)

    print("── y lo que no es un mundo ──")
    pf.subidas.clear()
    pag.set_input_files("#mu-file", str(tmp / "no-es-un-mundo.txt"))
    pag.wait_for_timeout(1200)
    ok(not sum(pf.subidas.values()), "un .txt no se sube (%d bytes)" % sum(pf.subidas.values()))
    aviso = pag.inner_text("#toast")
    ok("No reconozco" in aviso or "no es" in aviso.lower(),
       "y se dice por qué: %r" % aviso)
    foto(pag, "7-subir-mundo")

    # ── una carpeta ───────────────────────────────────────────────────────
    # Un mundo de Minecraft ES una carpeta, así que es lo primero que la gente
    # intenta soltar aquí. Se simula lo que llega al soltar una: un File vacío.
    print("── si alguien suelta la carpeta del mundo ──")
    pag.evaluate("""() => { const f=new File([], 'world');
        window._t=null; const orig=toast; window.toast=m=>{window._t=m; orig(m);};
        return muSubir(f).then(()=>{ window.toast=orig; }); }""")
    pag.wait_for_timeout(600)
    dijo = pag.evaluate("() => window._t || ''")
    ok("carpeta" in dijo.lower(), "se le dice que comprima la carpeta: %r" % dijo)

    ok(not errores, "sin errores de JavaScript" + (": %s" % errores[:2] if errores else ""))
    nav.close()

pf.para()
print()
if FALLOS:
    print("✘ %d fallo(s):" % len(FALLOS))
    for f in FALLOS:
        print("   ·", f)
    sys.exit(1)
print("✔ todo bien")
