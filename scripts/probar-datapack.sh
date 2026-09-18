#!/bin/bash
# Arranca un servidor de usar y tirar SOLO para ver si un datapack carga.
#
# ─────────────────────────────────────────────────────────────────────────
# Por qué existe
# ─────────────────────────────────────────────────────────────────────────
# El 18/09/2026 el datapack de vigilancia dejó el servidor sin arrancar. El
# fallo estaba escrito desde que se instaló, pero no se vio, y la razón es
# importante:
#
#   · `reload` NO lo habría detectado. En esta versión los logros son datos de
#     REGISTRO, y los registros no se recargan: se cargan al arrancar el
#     mundo. O sea que un datapack roto se ve bien en un servidor encendido y
#     tumba el siguiente arranque, que puede ser días después.
#
# Por eso ningún datapack vuelve a entrar en el mundo de verdad sin pasar por
# aquí. Esto arranca un servidor aparte, con un mundo nuevo en /tmp, en otro
# puerto, y mira si carga. El mundo de verdad ni se toca ni se abre.
#
#   bash scripts/probar-datapack.sh ~/minecraft/world/datapacks/vigilancia
#   bash scripts/probar-datapack.sh            # prueba TODOS los del mundo
#
# Tarda un minuto y pico. Códigos de salida, y la diferencia importa:
#   0 = carga · 1 = NO carga · 2 = no he podido probarlo (que no es lo mismo)
# ─────────────────────────────────────────────────────────────────────────
set -u

MC="${MC_DIR:-$HOME/minecraft}"
PANEL="${PANEL_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
PUERTO="${PRUEBA_PUERTO:-25599}"
ESPERA="${PRUEBA_ESPERA:-180}"
BANCO=$(mktemp -d /tmp/prueba-datapack-XXXXXX)

decir(){ echo "[prueba] $*"; }
limpia(){ rm -rf "$BANCO"; }
trap limpia EXIT

# ── el jar ───────────────────────────────────────────────────────────────
# Por FECHA, no por nombre (ver claude/el-jar-por-nombre.md).
LANZADOR="$MC/server.jar"
if [ ! -f "$LANZADOR" ]; then
  decir "no encuentro $LANZADOR"; exit 2  # 2 = no he podido probar
fi

# ── qué se prueba ────────────────────────────────────────────────────────
MUNDO_REAL="$MC/world"
if [ $# -ge 1 ]; then
  PACKS=("$@")
else
  PACKS=()
  for d in "$MUNDO_REAL"/datapacks/*; do
    [ -e "$d" ] && PACKS+=("$d")
  done
fi
if [ ${#PACKS[@]} -eq 0 ]; then
  decir "no hay datapacks que probar"; exit 0
fi

# ── el banco de pruebas ──────────────────────────────────────────────────
# Las librerías y las versiones se enlazan, no se copian: son cientos de MB y
# el lanzador se conforma con encontrarlas ya desempaquetadas.
mkdir -p "$BANCO/mundos/mundo/datapacks"
ln -s "$MC/libraries" "$BANCO/libraries" 2>/dev/null
ln -s "$MC/versions"  "$BANCO/versions"  2>/dev/null
cp "$LANZADOR" "$BANCO/server.jar"
echo "eula=true" > "$BANCO/eula.txt"
cat > "$BANCO/server.properties" <<PROPS
server-port=$PUERTO
online-mode=false
level-name=mundo
level-type=minecraft\:flat
max-players=1
view-distance=2
simulation-distance=2
spawn-protection=0
sync-chunk-writes=false
enable-rcon=false
enable-query=false
PROPS

for p in "${PACKS[@]}"; do
  if [ ! -e "$p" ]; then decir "no existe: $p"; exit 2; fi
  cp -r "$p" "$BANCO/mundos/mundo/datapacks/"
  decir "se prueba: $(basename "$p")"
done

# ── el log, a parte ──────────────────────────────────────────────────────
# 🔴 Sin esto, este servidor de mentira escribiría —y ROTARÍA— el
# `logs/latest.log` del servidor de verdad si alguien lo corriera desde
# ~/minecraft. Es exactamente el fallo que dejó la Historia en blanco durante
# días (claude/log-rotado-por-biomas.md). Aquí se trabaja desde $BANCO y
# además se le da una configuración propia, de consola.
CONF="$BANCO/log4j2-prueba.xml"
cat > "$CONF" <<'XML'
<?xml version="1.0" encoding="UTF-8"?>
<Configuration status="WARN">
  <Appenders>
    <Console name="Consola" target="SYSTEM_OUT">
      <PatternLayout pattern="[%d{HH:mm:ss}] [%t/%level]: %msg%n"/>
    </Console>
  </Appenders>
  <Loggers><Root level="info"><AppenderRef ref="Consola"/></Root></Loggers>
</Configuration>
XML

JAVA=""; MEJOR=0
for c in $(command -v java 2>/dev/null) /usr/lib/jvm/*/bin/java; do
  [ -x "$c" ] || continue
  v=$("$c" -version 2>&1 | grep -v '^Picked up' | grep -oE '[0-9]+' | head -1)
  [ -z "$v" ] && continue
  if [ "$v" -gt "$MEJOR" ]; then MEJOR=$v; JAVA=$c; fi
done
[ -z "$JAVA" ] && JAVA=/usr/bin/java

# ── a correr ─────────────────────────────────────────────────────────────
decir "arrancando en el puerto $PUERTO (mundo de mentira, un minuto)…"
SALIDA="$BANCO/salida.log"
cd "$BANCO" || exit 1
"$JAVA" -Xmx1G -XX:+UseSerialGC \
        "-Dlog4j2.configurationFile=$CONF" \
        "-Dlog4j.configurationFile=$CONF" \
        -jar "$BANCO/server.jar" nogui \
        --universe "$BANCO/mundos" --world mundo --port "$PUERTO" \
        > "$SALIDA" 2>&1 &
PID=$!

# Se espera a UNA de las dos: el «Done» de un arranque bueno, o la queja de
# los datapacks. Mirar solo si el proceso vive daría por bueno un arranque que
# se cae dos segundos después.
ESTADO="tiempo"
for _ in $(seq 1 "$ESPERA"); do
  if grep -q 'Done ([0-9.]*s)!' "$SALIDA" 2>/dev/null; then ESTADO="bien"; break; fi
  if grep -qE "Failed to load datapacks|Failed to load registries|Failed to parse" \
       "$SALIDA" 2>/dev/null; then ESTADO="mal"; break; fi
  kill -0 $PID 2>/dev/null || { ESTADO="muerto"; break; }
  sleep 1
done

kill $PID 2>/dev/null
for _ in $(seq 1 15); do kill -0 $PID 2>/dev/null || break; sleep 1; done
kill -9 $PID 2>/dev/null

echo
case "$ESTADO" in
  bien)
    decir "✔ el datapack CARGA. El servidor de verdad puede reiniciarse."
    grep -E "Loaded [0-9]+ .*pack|Done \(" "$SALIDA" | tail -3 | sed 's/^/        /'
    exit 0 ;;
  mal|muerto)
    decir "✘ el datapack NO carga. NO lo dejes en el mundo de verdad."
    echo
    grep -vE "^[[:space:]]*at |^[[:space:]]*\.\.\. " "$SALIDA" \
      | grep -A4 -iE "ERROR|Failed to (load|parse)" | head -40 | sed 's/^/        /'
    exit 1 ;;
  *)
    decir "✘ no dio señales en $ESPERA s — esto NO dice que el datapack esté mal,"
    decir "  dice que la prueba no llegó a ninguna conclusión. Últimas líneas:"
    tail -15 "$SALIDA" | sed 's/^/        /'
    exit 2 ;;
esac
