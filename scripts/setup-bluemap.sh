#!/bin/bash
# ============================================================
#  BlueMap para Server of Califree — instalación completa
#  Corre EN EL SERVER:  bash ~/panel/scripts/setup-bluemap.sh
#  Idempotente: se puede correr varias veces sin dañar nada.
# ============================================================
set -e
BM_VER="5.23"
BM_DIR="$HOME/bluemap"
WORLD="$HOME/minecraft/world"

echo "== 1/6 · Carpeta y jar =="
mkdir -p "$BM_DIR"
cd "$BM_DIR"
if [ ! -f bluemap-cli.jar ]; then
  curl -fL -o bluemap-cli.jar "https://github.com/BlueMap-Minecraft/BlueMap/releases/download/v${BM_VER}/bluemap-${BM_VER}-cli.jar"
fi

echo "== 2/6 · Configuración =="
# primera corrida genera las plantillas de config y sale
if [ ! -d config ]; then
  java -jar bluemap-cli.jar >/dev/null 2>&1 || true
fi
# aceptar la descarga de texturas oficiales de Mojang
sed -i 's/accept-download: false/accept-download: true/' config/core.conf
# los tres mapas apuntando al mundo real (las 3 dimensiones)
mkdir -p config/maps
rm -f config/maps/*.conf
cat > config/maps/overworld.conf <<EOF
world: "$WORLD"
dimension: "minecraft:overworld"
name: "Overworld"
sorting: 0
EOF
cat > config/maps/nether.conf <<EOF
world: "$WORLD"
dimension: "minecraft:the_nether"
name: "Nether"
sorting: 1
sky-color: "#290000"
ambient-light: 0.6
world-sky-light: 0
remove-caves-below-y: -10000
max-y: 90
EOF
cat > config/maps/end.conf <<EOF
world: "$WORLD"
dimension: "minecraft:the_end"
name: "End"
sorting: 2
sky-color: "#080010"
ambient-light: 0.6
world-sky-light: 0
remove-caves-below-y: -10000
EOF

echo "== 3/6 · Caddy: servir /map/ con el login del panel =="
sudo tee /etc/caddy/Caddyfile >/dev/null <<'EOF'
califree.net {
    handle_path /map/* {
        forward_auth 127.0.0.1:8444 {
            uri /api/mapauth
        }
        root * /home/ubuntu/bluemap/web
        file_server
    }
    handle {
        reverse_proxy 127.0.0.1:8444
    }
}
EOF
sudo systemctl reload caddy

echo "== 4/6 · Re-render automático cada noche (09:00 UTC, tras el backup) =="
sudo tee /etc/cron.d/bluemap-render >/dev/null <<'EOF'
0 9 * * * ubuntu cd /home/ubuntu/bluemap && nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar -r >> render.log 2>&1
EOF

echo "== 5/6 · Render inicial (en segundo plano, tarda HORAS la primera vez) =="
if ! screen -list | grep -q bluemaprender; then
  screen -dmS bluemaprender bash -c "cd $BM_DIR && nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar -r >> render.log 2>&1"
  echo "   render arrancado en segundo plano (screen: bluemaprender)"
else
  echo "   ya hay un render corriendo"
fi

echo "== 6/6 · Listo =="
echo "Progreso:   tail -f ~/bluemap/render.log     (Ctrl+C para salir del tail)"
echo "El mapa va apareciendo por pedazos en https://califree.net (pestaña Mapa)"
