#!/bin/bash
# ============================================================
#  Compila y prueba el lector de biomas contra el server.jar.
#
#  El jar de la 26.2 no está ofuscado, así que esto es compilar contra una
#  librería normal: se le pasa el jar del servidor y sus 39 librerías en el
#  classpath y ya.
#
#  NO toca el servidor, ni el mundo, ni genera chunks. Solo hace cuentas.
#
#  Biomas.java vive en scripts/ y no en una carpeta java/ aparte porque el
#  despliegue solo sincroniza `server.py nbt.py get-icons.py static/ scripts/`:
#  en cualquier otro sitio no llegaría nunca al servidor.
#
#  Correr EN EL SERVIDOR:  bash ~/panel/scripts/biomas-compilar.sh
# ============================================================
MC="${MC_DIR:-$HOME/minecraft}"
PANEL="${PANEL_DIR:-$HOME/panel}"
SALIDA="$PANEL/java-clases"

echo "── 1 · ¿hay compilador de Java? ─────────────────────────────"
if ! command -v javac >/dev/null 2>&1; then
  echo "  ✘ No hay javac (solo está el Java para EJECUTAR, no para compilar)."
  echo
  echo "  Hace falta el compilador. Es una herramienta del sistema, no un mod:"
  echo "  no toca Minecraft ni el mundo ni entra en el servidor."
  echo
  echo "      sudo apt install -y default-jdk-headless"
  echo
  echo "  Y vuelve a correr esto. Si prefieres no instalarlo, dímelo y lo"
  echo "  compilo yo por otro lado — solo necesitaría el server-26.2.jar."
  exit 1
fi
javac -version 2>&1 | sed 's/^/  /'

echo
echo "── 2 · el classpath ─────────────────────────────────────────"
JAR=$(ls "$MC"/versions/*/server-*.jar 2>/dev/null | tail -1)
if [ -z "$JAR" ]; then
  echo "  ✘ no encuentro el jar en $MC/versions"
  exit 1
fi
LIBS=$(find "$MC/libraries" -name '*.jar' 2>/dev/null | tr '\n' ':')
CP="$JAR:$LIBS"
echo "  jar:       $JAR"
echo "  librerías: $(find "$MC/libraries" -name '*.jar' 2>/dev/null | wc -l)"

echo
echo "── 3 · compilar ─────────────────────────────────────────────"
mkdir -p "$SALIDA"
if javac -nowarn -cp "$CP" -d "$SALIDA" "$PANEL/scripts/Biomas.java" 2>&1 | sed 's/^/  /'; then
  echo "  ✔ compilado en $SALIDA"
else
  echo
  echo "  ✘ no compiló. Pégame los errores de arriba: dicen exactamente qué"
  echo "    método cambió de nombre en la 26.2, que es justo lo que necesito."
  exit 1
fi

echo
echo "── 4 · la semilla ───────────────────────────────────────────"
SEMILLA="$1"
if [ -z "$SEMILLA" ]; then
  ANTES=$(stat -c%s "$MC/logs/latest.log" 2>/dev/null || echo 0)
  screen -p 0 -S "${MC_SCREEN:-mc}" -X eval 'stuff "seed\015"' 2>/dev/null
  sleep 2
  SEMILLA=$(tail -c +$((ANTES + 1)) "$MC/logs/latest.log" 2>/dev/null |
            grep -o 'Seed: \[-\?[0-9]*\]' | head -1 | grep -o '\-\?[0-9]*')
fi
if [ -z "$SEMILLA" ]; then
  echo "  ✘ no pude sacar la semilla. Pásala a mano:"
  echo "      bash ~/panel/scripts/biomas-compilar.sh 1244994422874902852"
  exit 1
fi
echo "  semilla: $SEMILLA"

echo
echo "── 5 · probar ───────────────────────────────────────────────"
# -Xmx384M: el servidor tiene 8 GB tomados de los 12 de la máquina. Esto es un
# invitado, no puede ponerse a competir por la memoria con Minecraft.
java -Xmx384M -cp "$CP:$SALIDA" califree.Biomas "$SEMILLA" 2>&1 | sed 's/^/  /'

echo
echo "─────────────────────────────────────────────────────────────"
echo "Compara esos biomas con Chunkbase en esas mismas coordenadas."
echo "Si coinciden, el mapa tipo Chunkbase está resuelto."
