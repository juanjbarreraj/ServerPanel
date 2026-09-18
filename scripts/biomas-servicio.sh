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
# 🔴 El fuente va por CONTENIDO, no por fecha. Un Commit+Sync —o cualquier
# rsync o checkout— le cambia el mtime a Biomas.java aunque no cambie ni una
# línea. Con la fecha dentro de la firma, el panel decía «el lector de biomas se
# compiló con una versión anterior del código» DESPUÉS DE CADA DESPLIEGUE, y
# recompilar son minutos. Un aviso que sale cuando no toca deja de leerse.
# El jar sí va por fecha y tamaño: nadie lo reescribe, y hacerle un sha1 a 25 MB
# cada vez que alguien abre la pestaña sería tirar CPU.
FIRMA="$(stat -c '%n %Y %s' "$JAR" 2>/dev/null) | $(sha1sum "$PANEL/scripts/Biomas.java" 2>/dev/null | cut -c1-16)"
if [ ! -f "$CLASES/califree/Biomas.class" ] || [ "$(cat "$SELLO" 2>/dev/null)" != "$FIRMA" ]; then
  # El compilador se elige POR VERSIÓN, no por nombre de carpeta. Ordenando
  # /usr/lib/jvm por texto, «openjdk-21» queda el último y se elegiría el 21 —
  # que no sabe ni abrir un jar de Java 25 y fallaría con un error críptico
  # sobre versiones de fichero de clase.
  # BIOMAS_JAVAC fija el compilador a mano. Sirve en una máquina con varios JDK
  # donde el más nuevo no es el bueno y, de paso, permite probar el camino de
  # «aquí no hay compilador» sin desinstalar nada.
  JAVAC=""; MEJOR=0
  if [ -n "${BIOMAS_JAVAC:-}" ]; then
    [ -x "${BIOMAS_JAVAC}" ] && JAVAC="${BIOMAS_JAVAC}"
  else
    for c in $(command -v javac 2>/dev/null) /usr/lib/jvm/*/bin/javac /opt/*/bin/javac; do
      [ -x "$c" ] || continue
      v=$("$c" -version 2>&1 | grep -v '^Picked up' | grep -oE '[0-9]+' | head -1)
      [ -z "$v" ] && continue
      if [ "$v" -gt "$MEJOR" ]; then MEJOR=$v; JAVAC=$c; fi
    done
  fi
  ANTES="$(cat "$SELLO" 2>/dev/null || true)"
  if [ -z "$JAVAC" ]; then
    # Seguir con las clases de antes SOLO si el jar es el mismo que la última
    # vez —o sea, lo que cambió fue el fuente—. Si cambió el jar, esas clases
    # son de otra versión de Minecraft, y arrancar con ellas no da un error
    # claro: da un NoSuchMethodError quince segundos más tarde, en bucle, con
    # el panel enseñando «apagado» y ni una pista de por qué. Pasó con la 26.3.
    if [ -f "$CLASES/califree/Biomas.class" ] && [ "${ANTES%% | *}" = "${FIRMA%% | *}" ]; then
      decir "no hay compilador, pero el jar no ha cambiado; sigo con lo compilado de antes"
    elif [ -f "$CLASES/califree/Biomas.class" ]; then
      decir "no hay compilador de Java y lo compilado es de OTRA versión de Minecraft."
      decir "No arranco: correría con las clases equivocadas y fallaría en bucle."
      decir "instálalo una vez con:  sudo bash ~/panel/scripts/biomas-instalar.sh"
      exit 1
    else
      decir "no hay compilador de Java y no hay nada compilado."
      decir "instálalo con:  sudo bash ~/panel/scripts/biomas-instalar.sh"
      decir "(tiene que ser el JDK de la MISMA versión de Java con la que corre"
      decir " Minecraft; el default-jdk de Ubuntu 24.04 es el 21 y no sirve)"
      exit 1
    fi
  else
    decir "compilando contra $(basename "$JAR")…"
    mkdir -p "$CLASES"
    # Fuera las clases de la versión anterior ANTES de compilar. Con ellas ahí,
    # cualquier comprobación por existencia da por buena una compilación que
    # falló — que es justo lo que hacía este script hasta la 26.3.
    rm -rf "$CLASES/califree"
    "$JAVAC" -nowarn -cp "$CP" -d "$CLASES" "$PANEL/scripts/Biomas.java" 2>&1 |
        sed 's/^/[biomas] /' >&2
    # PIPESTATUS[0] y no $?: el $? de una tubería es el del ÚLTIMO mandato, o
    # sea el del `sed`, que sale 0 siempre. Mirar eso era como no mirar nada.
    COMPILO=${PIPESTATUS[0]}
    if [ "$COMPILO" = "0" ] && [ -f "$CLASES/califree/Biomas.class" ]; then
      echo "$FIRMA" > "$SELLO"
      decir "compilado"
    else
      # El sello se borra: si se quedara puesto diría que las clases son de este
      # jar, y el panel daría el mapa por al día mientras el servicio se muere.
      rm -f "$SELLO"
      decir "no compiló contra $(basename "$JAR") (javac salió con $COMPILO)."
      decir "Esto pasa cuando Minecraft cambia el nombre de algún método en la"
      decir "versión nueva. Los errores de arriba dicen exactamente cuál."
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
# El java que ejecuta tiene que ser al menos tan nuevo como el javac que
# compiló, o se queja de la versión del fichero de clase. Se elige por versión
# por el mismo motivo que el compilador.
JAVA=""; MEJORJ=0
for c in $(command -v java 2>/dev/null) /usr/lib/jvm/*/bin/java /opt/*/bin/java; do
  [ -x "$c" ] || continue
  v=$("$c" -version 2>&1 | grep -v '^Picked up' | grep -oE '[0-9]+' | head -1)
  [ -z "$v" ] && continue
  if [ "$v" -gt "$MEJORJ" ]; then MEJORJ=$v; JAVA=$c; fi
done
[ -z "$JAVA" ] && JAVA=/usr/bin/java
decir "semilla $SEMILLA · puerto $PUERTO · $HILOS hilo(s)"

# ── 🔴 ESTE PROCESO ESTABA ROTANDO EL LOG DE MINECRAFT ───────────────────
#
# El lector de biomas lleva el jar del servidor en el classpath, así que log4j2
# coge la configuración que viene DENTRO del jar: la de Minecraft. Esa escribe
# en `logs/latest.log` **relativo al directorio de trabajo**, y la unidad de
# systemd tiene WorkingDirectory=/home/ubuntu/minecraft. O sea: el mismo
# fichero que usa el servidor. Y al arrancar lo ROTA.
#
# Medido en el servidor de Juan el 18/09/2026, con gente jugando:
#
#     latest.log ......... 284 bytes, 4 líneas
#     de Minecraft ....... 0
#     del lector de biomas 4
#
# Minecraft se quedó escribiendo en el fichero que este proceso le renombró
# debajo, así que la Historia y la consola del panel dejaron de ver nada — sin
# un solo error, hasta que Minecraft se reiniciara por su cuenta. Los cinco
# `.log.gz` de 177 bytes del día 17 son exactamente eso: logs con estas cuatro
# líneas y nada más.
#
# Dos cinturones, porque este fallo no se ve desde dentro:
#
#   1. configuración propia, que solo escribe por consola — la recoge journald,
#      que es donde se mira este servicio (`journalctl -u biomas`);
#   2. y aun así se trabaja desde otra carpeta, para que si algún día cambia el
#      nombre de la propiedad, el fichero que se cree sea nuestro y no el suyo.
CONF_LOG="$CLASES/log4j2-biomas.xml"
cat > "$CONF_LOG" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<Configuration status="WARN">
  <Appenders>
    <Console name="Consola" target="SYSTEM_OUT">
      <PatternLayout pattern="[%d{HH:mm:ss}] [%t/%level]: %msg%n"/>
    </Console>
  </Appenders>
  <Loggers>
    <Root level="info"><AppenderRef ref="Consola"/></Root>
  </Loggers>
</Configuration>
XML
APARTE="$CLASES/aparte"
mkdir -p "$APARTE" && cd "$APARTE" || cd /tmp
# Nada de lo de arriba depende del directorio de trabajo: todas las rutas del
# guion son absolutas y Biomas.java no abre un solo fichero.
exec "$JAVA" "-Xmx$MEMORIA" \
     "-Dlog4j2.configurationFile=$CONF_LOG" \
     "-Dlog4j.configurationFile=$CONF_LOG" \
     -cp "$CP:$CLASES" califree.Biomas \
     "$SEMILLA" --servicio --puerto "$PUERTO" --hilos "$HILOS"
