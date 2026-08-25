#!/bin/bash
# ============================================================
#  SOLO los iconos de estructuras del mapa. NO dibuja el terreno.
#
#  Para cuando el terreno ya está dibujado pero le faltan los iconos, que es
#  justo lo que pasaba antes de arreglar render-mapa.sh. Son dos pasos:
#
#    1) scan-structures.py  → mira el mundo y apunta qué estructuras hay
#                             dónde, en las configs de BlueMap
#    2) bluemap-cli --markers → vuelca esas configs al mapa web
#
#  El paso 2 es una operación APARTE del render (`-r`): sin él, el paso 1 no se
#  ve por ningún lado.
#
#  Uso:  bash ~/panel/scripts/marcadores.sh            (normal: solo lo que cambió)
#        bash ~/panel/scripts/marcadores.sh --a-fondo  (relee el mundo entero)
#
#  El render de cada noche ya hace esto por su cuenta. Este script es para no
#  esperar a la noche.
# ============================================================
BM="$HOME/bluemap"
PANEL="$HOME/panel"
LOG="$BM/render.log"
LOCK="$BM/.render.lock"

cd "$BM" 2>/dev/null || { echo "no encuentro $BM"; exit 1; }

# Mismo candado que render-mapa.sh: si hay un render en marcha, este espera a
# otro momento en vez de pelearse con él por los mismos ficheros.
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
  echo "Hay un render corriendo ahora mismo. Espera a que acabe y vuelve a lanzarlo."
  echo "  (para verlo:  tail -f $LOG )"
  exit 0
fi

decir() {
  local linea="[$(date '+%F %T')] $*"
  echo "$linea" >> "$LOG"
  echo "$linea"
}

ARGS=""
case " $* " in *" --a-fondo "*) ARGS="--completo" ;; esac

ARRANQUE=$(date +%s)
decir "───────── iconos: inicio ─────────"

decir "1/2 buscando estructuras${ARGS:+ (a fondo, sin caché)}…"
if ! nice -n 19 ionice -c3 python3 "$PANEL/scripts/scan-structures.py" $ARGS 2>&1 | tee -a "$LOG"; then
  decir "⚠ el escaneo falló; no sigo"
  exit 1
fi

decir "2/2 publicando los marcadores en el mapa…"
if nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar --markers >> "$LOG" 2>&1; then
  decir "marcadores publicados"
else
  CODIGO=$?
  decir "⚠ no pude publicar los marcadores (código $CODIGO)"
  exit "$CODIGO"
fi

MINUTOS=$(( ($(date +%s) - ARRANQUE) / 60 ))
decir "───────── iconos: fin, $MINUTOS min ─────────"
decir "Recarga el mapa en el navegador (Cmd+Shift+R) y deberían estar."
