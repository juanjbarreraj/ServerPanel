#!/bin/bash
# ============================================================
#  Retoques al webapp de BlueMap — Server of Califree
#
#  Hace dos cosas:
#   1. Mete /static/biomas.js en index.html (biomas al hacer clic, tarjeta por
#      encima de los marcadores, iconos solo en la vista plana).
#   2. Pone "defaultToFlatView": true en settings.json, que es la opción PROPIA
#      de BlueMap para que el mapa abra en la vista plana cenital — la segunda
#      de los tres botones, la única que enseña los iconos.
#
#  BlueMap rehace sus ficheros web cada vez que se actualiza o renderiza, así
#  que este parche hay que reaplicarlo: es idempotente, se puede correr siempre.
#  El cron de render lo ejecuta después de cada renderizado.
#
#  Si biomas.js cambia, SUBIR VER: el navegador cachea el script y sin eso
#  seguiría sirviendo el viejo. Correr este script otra vez actualiza la
#  etiqueta que ya estuviera puesta.  OJO: VER y la constante MAP_SRC de
#  static/index.html suben JUNTAS.
#
#  Corre EN EL SERVER:  bash ~/panel/scripts/parche-bluemap.sh
# ============================================================
set -e
VER=7
WEBDIR="/var/www/bluemap-web"
INDEX="$WEBDIR/index.html"
AJUSTES="$WEBDIR/settings.json"
TAG="<script src=\"/static/biomas.js?v=$VER\"></script>"

if [ ! -f "$INDEX" ]; then
  echo "✗ No encuentro $INDEX — ¿está BlueMap instalado?"
  exit 1
fi

# ---------------------------------------------------------------- 1) el script
sudo cp "$INDEX" "$INDEX.antes-del-parche"

if grep -q 'static/biomas\.js' "$INDEX"; then
  # ya estaba: solo se actualiza la versión de la etiqueta
  sudo sed -i -E "s|<script src=\"/static/biomas\.js[^\"]*\"></script>|$TAG|" "$INDEX"
  echo "✔ parche del bioma actualizado a v$VER"
else
  # se inyecta como script CLÁSICO en el <head>: así corre antes que el bundle
  # de BlueMap (que es un módulo y va diferido) y alcanza a enganchar el evento.
  sudo sed -i "0,/<\/head>/s|</head>|  $TAG\n</head>|" "$INDEX"
  if grep -q 'static/biomas\.js' "$INDEX"; then
    echo "✔ parche del bioma aplicado en $INDEX (v$VER)"
  else
    echo "✗ no pude inyectar el script; restaurando"
    sudo mv "$INDEX.antes-del-parche" "$INDEX"
    exit 1
  fi
fi

echo "   etiqueta:  $(grep -o '<script src="/static/biomas.js[^>]*></script>' "$INDEX" | head -1)"

# ------------------------------------------------- 2) abrir en la vista plana
#
# BlueMapApp.resetCamera() —que es lo que corre cuando se entra sin dirección—
# hace literalmente esto:
#
#     this.appState.controls.state = "perspective";
#     this.settings.defaultToFlatView && map.hasView("flat")
#         ? this.setFlatView() : …
#
# O sea: la vista de arranque NO se decide con JavaScript nuestro, se decide con
# esta bandera. Ponerla aquí es más fiable que pelearse con la cámara desde
# fuera, porque BlueMap la aplica en el mismo sitio donde ya estaba decidiendo.
if [ ! -f "$AJUSTES" ]; then
  echo "⚠ no encuentro $AJUSTES — me salto lo de la vista plana"
  exit 0
fi

sudo python3 - "$AJUSTES" <<'PY'
import json, shutil, sys

ruta = sys.argv[1]
try:
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
except Exception as e:
    print("⚠ settings.json ilegible (%s) — lo dejo como está" % e)
    raise SystemExit(0)

if datos.get("defaultToFlatView") is True:
    print("✔ settings.json ya abre en vista plana")
    raise SystemExit(0)

shutil.copy(ruta, ruta + ".antes-del-parche")
datos["defaultToFlatView"] = True
with open(ruta, "w", encoding="utf-8") as f:
    json.dump(datos, f, indent=2, ensure_ascii=False)
print("✔ settings.json: defaultToFlatView = true (el mapa abre en vista plana)")
PY
