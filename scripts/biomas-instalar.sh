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

# QUÉ COMPILADOR, Y POR QUÉ ESTE Y NO «EL DE JAVA»
#
# Minecraft 26.2 está compilado para Java 25, y un compilador de Java 21 NO PUEDE
# NI ABRIR ese jar: no entiende el formato. En Ubuntu 24.04 `default-jdk` es el
# 21, así que instalarlo parece que funciona y luego no compila nada.
#
# Se instala el que hace juego con el `java` que ya está corriendo el servidor:
# si Minecraft arranca, ese java sabe leer su jar, y su JDK también.
# `java -version` dice  openjdk version "25.0.4"  y `javac -version` dice
# javac 25.0.4 — dos formatos distintos. Y algunos entornos cuelan encima una
# línea «Picked up JAVA_TOOL_OPTIONS». Se quita el ruido y se coge el primer
# número, que en los dos casos es la versión mayor.
version_java(){ "$1" -version 2>&1 | grep -v '^Picked up' | grep -oE '[0-9]+' | head -1; }

# El mejor javac de la máquina, POR VERSIÓN y no por nombre de carpeta:
# ordenar /usr/lib/jvm por texto pone «openjdk-21» al final y elegiría el 21.
mejor_javac(){
  local mejor="" mejorv=0 v c
  for c in $(command -v javac 2>/dev/null) /usr/lib/jvm/*/bin/javac /opt/*/bin/javac; do
    [ -x "$c" ] || continue
    v=$(version_java "$c") || continue
    [ -z "$v" ] && continue
    if [ "$v" -gt "$mejorv" ]; then mejorv=$v; mejor=$c; fi
  done
  printf '%s' "$mejor"
}

NECESARIA=$(version_java "$(command -v java || echo /usr/bin/java)" 2>/dev/null || echo 25)
[ -z "$NECESARIA" ] && NECESARIA=25
echo "  el servidor corre con Java $NECESARIA"

JAVAC=$(mejor_javac)
TENGO=0; [ -n "$JAVAC" ] && TENGO=$(version_java "$JAVAC")

if [ -n "$JAVAC" ] && [ "$TENGO" -ge "$NECESARIA" ]; then
  echo "  ya estaba: $JAVAC (Java $TENGO)"
else
  if [ -n "$JAVAC" ]; then
    echo "  hay un compilador de Java $TENGO, pero no sabe leer un jar de Java $NECESARIA"
  fi
  PAQUETE="openjdk-${NECESARIA}-jdk-headless"
  echo "  instalando $PAQUETE…"

  # Ubuntu instala solo sus actualizaciones de seguridad y mientras tanto tiene
  # apt cogido. `DPkg::Lock::Timeout` NO cubre el candado de las listas, así que
  # hay que esperar a mano. Y si `update` no llega a correr, no se instala nada:
  # con la lista vieja apt pide paquetes que ya no existen en el espejo y falla
  # con un 404 que no dice nada de lo que pasa de verdad.
  ok=1
  for intento in $(seq 1 30); do
    if apt-get -o DPkg::Lock::Timeout=120 update -qq 2>/tmp/apt-biomas.err; then ok=0; break; fi
    if ! grep -q "Could not get lock\|Unable to lock" /tmp/apt-biomas.err; then
      cat /tmp/apt-biomas.err; break
    fi
    [ "$intento" = "1" ] && echo "  apt está ocupado con las actualizaciones de Ubuntu; espero…"
    sleep 20
  done
  if [ "$ok" != "0" ]; then
    echo
    echo "  ✘ apt sigue ocupado después de 10 minutos. Mira quién lo tiene:"
    echo "        ps -eo pid,etime,args | grep -E 'apt|dpkg|unattended' | grep -v grep"
    echo "    Cuando termine, vuelve a correr este mismo comando."
    exit 1
  fi

  if ! apt-get -o DPkg::Lock::Timeout=600 install -y -qq "$PAQUETE"; then
    echo
    echo "  ✘ No se pudo instalar $PAQUETE."
    echo "    Prueba a ver si existe con otro nombre:"
    echo "        apt-cache search openjdk | grep jdk-headless"
    exit 1
  fi
  JAVAC=$(mejor_javac)
  echo "  instalado: $JAVAC ($("$JAVAC" -version 2>&1))"
fi

# La prueba de verdad no es que exista javac, es que sepa abrir ESTE jar. Si
# esto falla, mejor enterarse ahora que dentro de un rato viendo el mapa vacío.
JAR=$(ls -t "$MC"/versions/*/server-*.jar 2>/dev/null | head -1)
if [ -n "$JAR" ]; then
  if "$JAVAC" -version >/dev/null 2>&1 && \
     "$JAVAC" -nowarn -cp "$JAR" -d /tmp/biomas-prueba "$PANEL/scripts/Biomas.java" >/tmp/javac-biomas.err 2>&1; then
    echo "  ✔ compila contra $(basename "$JAR")"
  elif grep -qi "class file version\|unsupported class file" /tmp/javac-biomas.err; then
    echo "  ✘ este compilador NO sabe leer $(basename "$JAR"):"
    sed 's/^/     /' /tmp/javac-biomas.err | head -3
    exit 1
  else
    # Faltan las librerías en el classpath: normal, aquí solo se prueba el jar.
    echo "  ✔ el compilador entiende el jar"
  fi
  rm -rf /tmp/biomas-prueba
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
