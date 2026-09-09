#!/bin/bash
# ============================================================
#  EL ÚNICO COMANDO CON sudo DE TODO ESTO. Se corre una vez.
#
#      sudo bash ~/panel/scripts/biomas-instalar.sh
#
#  Deja el lector de biomas arrancando solo con la máquina y le da permiso al
#  panel para reiniciarlo cuando cambies de mundo.
#
#  QUÉ TOCA, EXACTAMENTE:
#    · instala el compilador de Java (apt) — herramienta de la máquina, NO un
#      mod: no entra en Minecraft, no toca el mundo, no cambia el juego
#    · crea /etc/systemd/system/biomas.service
#    · añade una línea a /etc/sudoers.d/ para que el panel pueda hacer
#      `systemctl restart biomas` y nada más
#
#  QUÉ NO TOCA: el mundo, el servidor de Minecraft, el panel, BlueMap, la red.
#  El servicio escucha solo en 127.0.0.1, no abre ningún puerto al exterior.
#
#  Para deshacerlo:
#      sudo systemctl disable --now biomas
#      sudo rm /etc/systemd/system/biomas.service /etc/sudoers.d/panel-biomas
# ============================================================
set -eu

if [ "$(id -u)" != "0" ]; then
  echo "Esto necesita sudo:  sudo bash $0"
  exit 1
fi

USUARIO="${SUDO_USER:-ubuntu}"
CASA=$(getent passwd "$USUARIO" | cut -d: -f6)
PANEL="${PANEL_DIR:-$CASA/panel}"
MC="${MC_DIR:-$CASA/minecraft}"

echo "── usuario: $USUARIO   panel: $PANEL   minecraft: $MC"
[ -f "$PANEL/scripts/biomas-servicio.sh" ] || {
  echo "✘ no encuentro $PANEL/scripts/biomas-servicio.sh"
  echo "  Haz primero commit+sync en VS Code para que llegue al servidor."
  exit 1
}

echo
echo "── 1 · el compilador de Java ────────────────────────────────"
if command -v javac >/dev/null 2>&1 || ls /usr/lib/jvm/*/bin/javac >/dev/null 2>&1; then
  echo "  ya estaba"
else
  apt-get update -qq
  apt-get install -y -qq default-jdk-headless
  echo "  instalado: $(javac -version 2>&1)"
fi

echo
echo "── 2 · el servicio ──────────────────────────────────────────"
cat > /etc/systemd/system/biomas.service <<UNIDAD
[Unit]
Description=Lector de biomas del panel (mapa tipo Chunkbase)
After=network.target minecraft.service
Wants=minecraft.service

[Service]
Type=simple
User=$USUARIO
WorkingDirectory=$MC
Environment=MC_DIR=$MC
Environment=PANEL_DIR=$PANEL
ExecStart=/bin/bash $PANEL/scripts/biomas-servicio.sh
Restart=always
RestartSec=30

# Minecraft manda. Esto es un invitado en una máquina de 2 núcleos: cuando el
# servidor necesite la CPU, este se aparta. Sin esto, dibujar el mapa podría
# notarse como tirones dentro del juego.
Nice=10
CPUWeight=20
MemoryMax=1G

[Install]
WantedBy=multi-user.target
UNIDAD
echo "  escrito /etc/systemd/system/biomas.service"

echo
echo "── 3 · permiso para que el panel lo reinicie ────────────────"
echo "$USUARIO ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart biomas, /bin/systemctl restart biomas" \
     > /etc/sudoers.d/panel-biomas
chmod 440 /etc/sudoers.d/panel-biomas
visudo -c -f /etc/sudoers.d/panel-biomas >/dev/null && echo "  permiso puesto (solo reiniciar ese servicio)"

echo
echo "── 4 · arrancar ─────────────────────────────────────────────"
systemctl daemon-reload
systemctl enable --now biomas >/dev/null 2>&1 || systemctl enable biomas
systemctl restart biomas
echo "  arrancando… (la primera vez compila, tarda ~1 minuto)"

for i in $(seq 1 40); do
  sleep 3
  if curl -fsS --max-time 2 "http://127.0.0.1:${BIOMAS_PUERTO:-25580}/salud" 2>/dev/null; then
    echo
    echo
    echo "✔ Listo. Ya puedes abrir la pestaña del mapa en el panel."
    exit 0
  fi
done

echo
echo "⚠ Aún no responde. Mira qué dice:"
echo "     sudo journalctl -u biomas -n 40 --no-pager"
echo "  Si pone que no puede sacar la semilla, es que Minecraft todavía no había"
echo "  terminado de arrancar: se reintenta solo cada 30 segundos."
exit 0
