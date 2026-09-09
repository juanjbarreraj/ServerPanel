#!/bin/bash
# ============================================================
#  Arranca el lector de biomas y lo mantiene al día.
#
#  Lo lanza systemd (ver biomas-instalar.sh). Cada vez que arranca:
#
#    1. busca el jar del servidor y lo compara con lo que compiló la última vez
#    2. si el jar cambió (o sea: Minecraft se actualizó solo a la 26.3), vuelve
#       a compilar Biomas.java contra el jar nuevo
#    3. averigua la semilla del mundo que está cargado ahora mismo
#    4. deja el servicio escuchando en 127.0.0.1
#
#  El paso 2 es el que hace que esto no haya que tocarlo nunca más: el panel se
#  actualiza de versión solo, y este se entera por el jar.
#
#  Si algo falta (el servidor aún no ha arrancado, no se puede sacar la
#  semilla), sale con error y systemd lo reintenta en 30 segundos. No hay
#  nada que vigilar.
# ============================================================
set -u

MC="${MC_DIR:-$HOME/minecraft}"
PANEL="${PANEL_DIR:-$HOME/panel}"
CLASES="$PANEL/java-clases"
SELLO="$CLASES/.compilado-de"
PUERTO="${BIOMAS_PUERTO:-25580}"
HILOS="${BIOMAS_HILOS:-2}"
MEMORIA="${BIOMAS_MEM:-512M}"
PANTALLA="${MC_SCREEN:-mc}"
CACHE_SEMILLA="$PANEL/data/semilla.txt"

decir(){ echo "[biomas] $*" >&2; }

# ── 1 · el jar ───────────────────────────────────────────────────────────
JAR=$(ls -t "$MC"/versions/*/server-*.jar 2>/dev/null | head -1)
[ -z "$JAR" ] && JAR=$(ls -t "$MC"/server.jar 2>/dev/null | head -1)
if [ -z "$JAR" ] || [ ! -f "$JAR" ]; then
  decir "no encuentro el jar del servidor en $MC"; exit 1
fi
LIBS=$(find "$MC/libraries" -name '*.jar' 2>/dev/null | sort | tr '\n' ':')
CP="$JAR:$LIBS"

# ── 2 · ¿hace falta compilar? ────────────────────────────────────────────
FIRMA="$(stat -c '%n %Y %s' "$JAR" 2>/dev/null) | $(stat -c '%Y' "$PANEL/scripts/Biomas.java" 2>/dev/null)"
if [ ! -f "$CLASES/califree/Biomas.class" ] || [ "$(cat "$SELLO" 2>/dev/null)" != "$FIRMA" ]; then
  JAVAC=$(command -v javac 2>/dev/null)
  [ -z "$JAVAC" ] && JAVAC=$(ls -1 /usr/lib/jvm/*/bin/javac 2>/dev/null | tail -1)
  if [ -z "$JAVAC" ]; then
    if [ -f "$CLASES/califree/Biomas.class" ]; then
      decir "no hay compilador; sigo con lo compilado de antes (puede no valer para $JAR)"
    else
      decir "no hay compilador de Java y no hay nada compilado."
      decir "instálalo con:  sudo apt install -y default-jdk-headless"
      exit 1
    fi
  else
    decir "compilando contra $(basename "$JAR")…"
    mkdir -p "$CLASES"
    if "$JAVAC" -nowarn -cp "$CP" -d "$CLASES" "$PANEL/scripts/Biomas.java" 2>&1 | sed 's/^/[biomas] /' >&2; then
      : # javac escribe los errores por la salida de error, el código va abajo
    fi
    if [ -f "$CLASES/califree/Biomas.class" ]; then
      echo "$FIRMA" > "$SELLO"
      decir "compilado"
    else
      decir "no compiló contra $(basename "$JAR")."
      decir "Esto pasa si Minecraft cambió el nombre de algún método en la versión nueva."
      exit 1
    fi
  fi
fi

# ── 3 · la semilla ───────────────────────────────────────────────────────
# En la 26.x la semilla ya no está en level.dat, así que hay que preguntársela
# al servidor. Si aún no ha arrancado, sale y systemd reintenta: es lo normal
# al encender la máquina, porque Minecraft tarda un minuto largo en estar listo.
semilla_de_consola(){
  local antes nuevo s
  [ -f "$MC/logs/latest.log" ] || return 1
  antes=$(stat -c%s "$MC/logs/latest.log")
  screen -p 0 -S "$PANTALLA" -X eval 'stuff "seed\015"' 2>/dev/null || return 1
  for _ in 1 2 3 4 5 6; do
    sleep 1
    nuevo=$(tail -c +$((antes + 1)) "$MC/logs/latest.log" 2>/dev/null)
    s=$(printf '%s' "$nuevo" | grep -o 'Seed: \[\?-\?[0-9]\+\]\?' | head -1 | grep -o '\-\?[0-9]\+')
    [ -n "$s" ] && { printf '%s' "$s"; return 0; }
  done
  return 1
}

SEMILLA="${BIOMAS_SEMILLA:-}"
if [ -z "$SEMILLA" ]; then
  SEMILLA=$(semilla_de_consola)
fi
if [ -z "$SEMILLA" ]; then
  # Último recurso: la que se apuntó la vez anterior. Solo vale si no se ha
  # cambiado de mundo; por eso el panel borra este fichero al cambiar.
  SEMILLA=$(cat "$CACHE_SEMILLA" 2>/dev/null | tr -dc '\-0-9')
  [ -n "$SEMILLA" ] && decir "el servidor no contesta; uso la semilla apuntada ($SEMILLA)"
fi
if [ -z "$SEMILLA" ]; then
  decir "no puedo sacar la semilla: el servidor de Minecraft no está listo todavía. Reintento."
  exit 1
fi
mkdir -p "$(dirname "$CACHE_SEMILLA")"
printf '%s\n' "$SEMILLA" > "$CACHE_SEMILLA"

# ── 4 · a escuchar ───────────────────────────────────────────────────────
JAVA=$(command -v java || echo /usr/bin/java)
decir "semilla $SEMILLA · puerto $PUERTO · $HILOS hilo(s)"
exec "$JAVA" "-Xmx$MEMORIA" -cp "$CP:$CLASES" califree.Biomas \
     "$SEMILLA" --servicio --puerto "$PUERTO" --hilos "$HILOS"
