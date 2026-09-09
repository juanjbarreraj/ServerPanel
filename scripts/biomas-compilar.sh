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
# Puede estar instalado y no estar en el PATH: Ubuntu deja los JDK en
# /usr/lib/jvm y a veces solo enlaza el `java`, no el `javac`.
JAVAC=$(command -v javac 2>/dev/null)
if [ -z "$JAVAC" ]; then
  JAVAC=$(ls -1 /usr/lib/jvm/*/bin/javac 2>/dev/null | tail -1)
fi
if [ -z "$JAVAC" ]; then
  JAVAC=$(ls -1 "$HOME"/*/bin/javac /opt/*/bin/javac 2>/dev/null | tail -1)
fi
if [ -z "$JAVAC" ]; then
  echo "  ✘ No hay compilador. Lo que sí hay:"
  echo "      java:  $(command -v java || echo 'ninguno')"
  echo "      JDKs:  $(ls -d /usr/lib/jvm/* 2>/dev/null | tr '\n' ' ' || echo 'ninguno')"
  echo
  echo "  Dos caminos; elige tú:"
  echo
  echo "  A) Instalar el compilador (lo más simple):"
  echo "       sudo apt install -y default-jdk-headless"
  echo "     Es una herramienta de la máquina, NO un mod: no entra en el"
  echo "     servidor, no toca el mundo, no cambia nada de Minecraft. Y como el"
  echo "     panel se actualiza solo de versión, tenerlo permite recompilar"
  echo "     el lector de biomas cada vez, sin que tengas que hacer nada."
  echo "     Si prefieres, se puede quitar después de compilar."
  echo
  echo "  B) Que lo compile Claude y tú solo copies el resultado."
  echo "     Necesitaría el jar y las librerías. Desde TU MAC:"
  echo "       scp -i ~/oracle-mc.key ubuntu@132.145.136.215:~/minecraft/versions/26.2/server-26.2.jar ~/Downloads/"
  echo "       ssh -i ~/oracle-mc.key ubuntu@132.145.136.215 'cd ~/minecraft && tar czf /tmp/libs.tgz libraries'"
  echo "       scp -i ~/oracle-mc.key ubuntu@132.145.136.215:/tmp/libs.tgz ~/Downloads/"
  echo "     y me avisas: los leo de tu carpeta de Descargas."
  exit 1
fi
echo "  javac: $JAVAC"
"$JAVAC" -version 2>&1 | sed 's/^/  /'

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
if "$JAVAC" -nowarn -cp "$CP" -d "$SALIDA" "$PANEL/scripts/Biomas.java" 2>&1 | sed 's/^/  /'; then
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
JAVA=$(dirname "$JAVAC")/java; [ -x "$JAVA" ] || JAVA=java
"$JAVA" -Xmx384M -cp "$CP:$SALIDA" califree.Biomas "$SEMILLA" 2>&1 | sed 's/^/  /'

echo
echo "─────────────────────────────────────────────────────────────"
echo "Compara esos biomas con Chunkbase en esas mismas coordenadas."
echo "Si coinciden, el mapa tipo Chunkbase está resuelto."
