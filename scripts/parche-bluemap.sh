#!/bin/bash
# ============================================================
#  Mete /static/biomas.js dentro del webapp de BlueMap
#
#  BlueMap rehace sus ficheros web cada vez que se actualiza, así que este
#  parche hay que reaplicarlo: es idempotente, se puede correr siempre.
#  El cron de render lo ejecuta después de cada renderizado.
#
#  Si biomas.js cambia, SUBIR VER: el navegador cachea el script y sin eso
#  seguiría sirviendo el viejo. Correr este script otra vez actualiza la
#  etiqueta que ya estuviera puesta.
#
#  Corre EN EL SERVER:  bash ~/panel/scripts/parche-bluemap.sh
# ============================================================
set -e
VER=3
WEBDIR="/var/www/bluemap-web"
INDEX="$WEBDIR/index.html"
TAG="<script src=\"/static/biomas.js?v=$VER\"></script>"

if [ ! -f "$INDEX" ]; then
  echo "✗ No encuentro $INDEX — ¿está BlueMap instalado?"
  exit 1
fi

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
