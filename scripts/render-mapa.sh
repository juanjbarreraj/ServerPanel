#!/bin/bash
# ============================================================
#  Actualiza el mapa de BlueMap. UN SOLO camino para las dos formas de lanzarlo:
#    - el cron de cada noche  (/etc/cron.d/bluemap-render)
#    - el botón "Actualizar el mapa" de la pestaña Sistema
#
#  Antes eran dos comandos distintos escritos a mano, y el del botón se había
#  quedado SIN reaplicar el parche del bioma: al pulsarlo, BlueMap podía
#  reescribir sus ficheros web y el mapa se quedaba sin la etiqueta de
#  /static/biomas.js hasta que alguien corriera el parche a mano.
#
#  Uso:  bash ~/panel/scripts/render-mapa.sh                  (render incremental)
#        bash ~/panel/scripts/render-mapa.sh --con-estructuras (fuerza el escaneo)
#        bash ~/panel/scripts/render-mapa.sh --completo        (rehace TODO, horas)
# ============================================================
BM="$HOME/bluemap"
PANEL="$HOME/panel"
LOG="$BM/render.log"
LOCK="$BM/.render.lock"
SELLO="$BM/.ultimo-escaneo"
DIAS_ESCANEO=7        # las estructuras solo aparecen en chunks nuevos: no hace
                      # falta rastrear el mundo entero cada noche

cd "$BM" 2>/dev/null || { echo "no encuentro $BM"; exit 1; }

# ---- un solo render a la vez -------------------------------------------------
# Sin esto, el cron y el botón de Sistema podían solaparse y dejar los tiles a
# medias. flock lo garantiza incluso entre procesos distintos.
exec 9>"$LOCK" || exit 1
if ! flock -n 9; then
  echo "[$(date '+%F %T')] ya hay un render corriendo; no hago nada" >> "$LOG"
  exit 0
fi

# ---- el registro no puede crecer para siempre --------------------------------
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 5000000 ]; then
  mv -f "$LOG" "$LOG.1"
fi

decir() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

ARRANQUE=$(date +%s)
decir "───────── inicio ($*) ─────────"

# ---- 1) estructuras: pesado, así que solo de vez en cuando --------------------
ESCANEAR=0
case " $* " in *" --con-estructuras "*) ESCANEAR=1 ;; esac
if [ "$ESCANEAR" = 0 ]; then
  if [ ! -f "$SELLO" ]; then
    ESCANEAR=1
  elif [ "$(( ($(date +%s) - $(stat -c%Y "$SELLO")) / 86400 ))" -ge "$DIAS_ESCANEO" ]; then
    ESCANEAR=1
  fi
fi

if [ "$ESCANEAR" = 1 ] && [ -f "$PANEL/scripts/scan-structures.py" ]; then
  decir "escaneando estructuras del mundo (esto es lo lento; toca cada $DIAS_ESCANEO días)"
  if nice -n 19 ionice -c3 python3 "$PANEL/scripts/scan-structures.py" >> "$LOG" 2>&1; then
    touch "$SELLO"
    decir "estructuras al día"
  else
    decir "⚠ el escaneo de estructuras falló — sigo con el render igualmente"
  fi
else
  decir "estructuras: no toca (última vez $(date -r "$SELLO" '+%F' 2>/dev/null || echo nunca))"
fi

# ---- 2) el render ------------------------------------------------------------
ARGS="-r"
case " $* " in *" --completo "*) ARGS="-r -f"; decir "RENDER COMPLETO pedido: esto tarda horas" ;; esac

decir "renderizando ($ARGS)…"
nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar $ARGS >> "$LOG" 2>&1
CODIGO=$?
[ "$CODIGO" = 0 ] && decir "render terminado" || decir "⚠ el render salió con código $CODIGO"

# ---- 3) reaplicar el parche del bioma ----------------------------------------
# SIEMPRE, gane o falle el render: si BlueMap tocó sus ficheros web, la etiqueta
# de /static/biomas.js y el defaultToFlatView se habrían perdido.
if [ -f "$PANEL/scripts/parche-bluemap.sh" ]; then
  bash "$PANEL/scripts/parche-bluemap.sh" >> "$LOG" 2>&1 \
    && decir "parche del bioma reaplicado" \
    || decir "⚠ no pude reaplicar el parche del bioma"
fi

MINUTOS=$(( ($(date +%s) - ARRANQUE) / 60 ))
decir "───────── fin, $MINUTOS min ─────────"
exit "$CODIGO"
