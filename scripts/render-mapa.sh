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
#  Cada pasada hace TRES cosas, en este orden:
#    1) buscar estructuras nuevas   (incremental: solo las regiones que cambiaron)
#    2) renderizar los chunks nuevos
#    3) PUBLICAR los marcadores     (`--markers`, que es un paso aparte de `-r`)
#
#  Uso:  bash ~/panel/scripts/render-mapa.sh                     (lo normal)
#        bash ~/panel/scripts/render-mapa.sh --estructuras-a-fondo (relee las
#                                            340 regiones, ignorando la caché)
#        bash ~/panel/scripts/render-mapa.sh --completo           (rehace TODO,
#                                            mapa y estructuras: horas)
#        bash ~/panel/scripts/render-mapa.sh --desde-cero         (BORRA los
#                                            azulejos y los dibuja de nuevo)
#
#  `--con-estructuras` se sigue aceptando pero ya no hace nada: ahora se escanea
#  siempre. El botón de Sistema lo manda y no pasa nada.
# ============================================================
BM="$HOME/bluemap"
PANEL="$HOME/panel"
LOG="$BM/render.log"
LOCK="$BM/.render.lock"
SELLO="$BM/.ultimo-escaneo"

cd "$BM" 2>/dev/null || { echo "no encuentro $BM"; exit 1; }

# ---- ¿está el mapa congelado a propósito? ------------------------------------
# Al actualizar Minecraft, el mundo pasa a un formato que BlueMap puede tardar
# semanas en soportar. Si se le dejara correr contra un mundo que no entiende,
# escribiría azulejos malos ENCIMA de los buenos y se perdería el mapa que ya
# había. Por eso actualizar.py deja esta nota y aquí se para en seco.
#
# El mapa se sigue viendo: los azulejos de antes están intactos. Se descongela
# desde el botón del panel («Actualizar el mapa a la X»), que prueba con el
# BlueMap más nuevo y solo quita la nota si el render sale bien.
CONGELADO="$PANEL/data/mapa-congelado.json"
if [ -f "$CONGELADO" ] && [ "$1" != "--descongelar" ]; then
  echo "[$(date '+%F %T')] mapa congelado ($(cat "$CONGELADO" | tr -d '\n' | cut -c1-160)); no dibujo nada" >> "$LOG"
  exit 0
fi

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

# Al log Y a la terminal. Corriéndolo a mano, un script que no imprime nada
# durante veinte minutos parece colgado; y desde cron esto no molesta porque el
# fichero de cron lleva MAILTO="".
decir() {
  local linea="[$(date '+%F %T')] $*"
  echo "$linea" >> "$LOG"
  echo "$linea"
}

ARRANQUE=$(date +%s)

# ---- ¿toca repintar desde cero? ---------------------------------------------
# Dos formas de pedirlo: con el argumento, o dejando esta nota para que lo haga
# el render de la madrugada. La nota se borra ANTES de empezar: si el render se
# cae a mitad, el de la noche siguiente no vuelve a borrar el mapa entero por su
# cuenta. Quien quiera repetirlo, que lo pida otra vez.
PENDIENTE="$BM/.repintar-pendiente"
DESDE_CERO=""
case " $* " in *" --desde-cero "*) DESDE_CERO="1" ;; esac
if [ -z "$DESDE_CERO" ] && [ -f "$PENDIENTE" ]; then
  DESDE_CERO="1"
  rm -f "$PENDIENTE"
  echo "[$(date '+%F %T')] había una petición de repintado desde cero; la atiendo" >> "$LOG"
fi

decir "───────── inicio ($*${DESDE_CERO:+ desde-cero}) ─────────"

# ---- 0) borrar los azulejos de antes ----------------------------------------
# Esto es lo único que limpia de verdad un mapa que ya no se corresponde con el
# mundo — por ejemplo después de recortar chunks con MCA Selector. BlueMap solo
# redibuja los chunks MODIFICADOS, y un chunk borrado no está modificado: no
# está. Sin borrar los azulejos, el mapa se queda enseñando un terreno que ya no
# existe, y no hay ninguna opción del CLI que lo arregle (lo comprobé: no existe
# `--purge`, las opciones son -a -b -c -e -f -g -h -l -m --markers -n -r -s -u -v).
if [ -n "$DESDE_CERO" ]; then
  # dónde deja BlueMap los mapas lo manda config/storages/file.conf
  RAIZ_MAPAS=$(sed -n 's/^[[:space:]]*root[[:space:]]*[:=][[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\1/p' \
               "$BM/config/storages/file.conf" 2>/dev/null | head -1)
  [ -z "$RAIZ_MAPAS" ] && RAIZ_MAPAS="web/maps"
  case "$RAIZ_MAPAS" in /*) MAPAS="$RAIZ_MAPAS" ;; *) MAPAS="$BM/$RAIZ_MAPAS" ;; esac
  # Cinturón y tirantes antes de un rm -rf: que la ruta acabe en /maps y que
  # exista. Un fallo aquí se lleva por delante algo que no es el mapa.
  case "$MAPAS" in
    */maps)
      if [ -d "$MAPAS" ]; then
        decir "borrando los azulejos de $MAPAS (el mapa se verá vacío hasta que acabe)…"
        rm -rf "${MAPAS:?}/"*
        decir "azulejos borrados"
      else
        decir "⚠ no encuentro la carpeta de mapas ($MAPAS); dibujo sin borrar"
      fi ;;
    *) decir "⚠ la carpeta de mapas no parece la buena ($MAPAS); NO borro nada" ;;
  esac
fi

# ---- 1) estructuras: AHORA TODAS LAS NOCHES ----------------------------------
# Antes esto solo corría una vez por semana porque releer las ~340 regiones era
# carísimo. Resultado: el terreno que exploraban los jugadores se dibujaba, pero
# se quedaba sin iconos de estructuras hasta el siguiente lunes.
#
# scan-structures.py ya guarda lo que encontró en cada .mca junto con su fecha y
# tamaño, así que solo relee los ficheros que han cambiado. Con eso cabe de sobra
# en el trabajo de cada noche y las estructuras nuevas salen al día siguiente.
ARGS_SCAN=""
case " $* " in *" --estructuras-a-fondo "*) ARGS_SCAN="--completo" ;; esac
case " $* " in *" --completo "*)            ARGS_SCAN="--completo" ;; esac

if [ -f "$PANEL/scripts/scan-structures.py" ]; then
  decir "buscando estructuras nuevas${ARGS_SCAN:+ (a fondo, sin caché)}…"
  if nice -n 19 ionice -c3 python3 "$PANEL/scripts/scan-structures.py" $ARGS_SCAN >> "$LOG" 2>&1; then
    touch "$SELLO"
    decir "estructuras al día"
  else
    decir "⚠ el escaneo de estructuras falló — sigo con el render igualmente"
  fi
else
  decir "⚠ no encuentro scan-structures.py: el mapa se dibujará SIN iconos nuevos"
fi

# ---- 2) el render ------------------------------------------------------------
ARGS="-r"
case " $* " in *" --completo "*) ARGS="-r -f"; decir "RENDER COMPLETO pedido: esto tarda horas" ;; esac
# Con los azulejos borrados no hace falta `-f` (no hay nada que saltarse), pero
# se pone igual: si el borrado no llegó a hacerse, `-r` a secas dejaría el mapa
# viejo intacto y el repintado no habría servido de nada.
[ -n "$DESDE_CERO" ] && { ARGS="-r -f"; decir "REPINTADO DESDE CERO: esto tarda horas"; }

decir "renderizando ($ARGS)…"
nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar $ARGS >> "$LOG" 2>&1
CODIGO=$?
[ "$CODIGO" = 0 ] && decir "render terminado" || decir "⚠ el render salió con código $CODIGO"

# ---- 2b) PUBLICAR los marcadores ---------------------------------------------
# ESTE PASO FALTABA, y era el motivo de fondo de que el terreno nuevo saliera sin
# iconos. scan-structures.py solo escribe los `marker-sets` en config/maps/*.conf;
# BlueMap no los mira al renderizar. `--markers` es una operación aparte de `-r`
# (así lo documenta BlueMap) y es la que los vuelca al mapa web. Sin ella, se
# podían escanear estructuras nuevas todas las noches y no se vería ni una.
# Va DESPUÉS del render para que un `-g/-s` interno del render no las pise.
decir "publicando los marcadores…"
if nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar --markers >> "$LOG" 2>&1; then
  decir "marcadores publicados"
else
  MCOD=$?
  decir "⚠ no pude publicar los marcadores (código $MCOD)"
fi

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
