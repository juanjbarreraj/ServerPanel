#!/bin/bash
# ============================================================
#  Mete /static/biomas.js dentro del webapp de BlueMap
#
#  BlueMap rehace sus ficheros web cada vez que se actualiza, así que este
#  parche hay que reaplicarlo: es idempotente, se puede correr siempre.
#  El cron de render lo ejecuta después de cada renderizado.
#
#  Corre EN EL SERVER:  bash ~/panel/scripts/parche-bluemap.sh
# ============================================================
set -e
WEBDIR="/var/www/bluemap-web"
INDEX="$WEBDIR/index.html"
TAG='<script src="/static/biomas.js"></script>'

if [ ! -f "$INDEX" ]; then
  echo "✗ No encuentro $INDEX — ¿está BlueMap instalado?"
  exit 1
fi

if grep -q 'static/biomas.js' "$INDEX"; then
  echo "✔ el parche del bioma ya estaba puesto"
  exit 0
fi

# se inyecta como script CLÁSICO en el <head>: así corre antes que el bundle
# de BlueMap (que es un módulo y va diferido) y alcanza a enganchar el evento.
sudo cp "$INDEX" "$INDEX.antes-del-parche"
sudo sed -i "0,/<\/head>/s|</head>|  $TAG\n</head>|" "$INDEX"

if grep -q 'static/biomas.js' "$INDEX"; then
  echo "✔ parche del bioma aplicado en $INDEX"
else
  echo "✗ no pude inyectar el script; restaurando"
  sudo mv "$INDEX.antes-del-parche" "$INDEX"
  exit 1
fi
