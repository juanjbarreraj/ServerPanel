#!/bin/bash
# ============================================================
#  SOLO los iconos del mapa 3D. NO dibuja el terreno.
#
#  Desde que las estructuras se calculan de la semilla y se pintan en la
#  pestaña «Explorar», sobre el mapa 3D ya solo van los LUGARES que pone la
#  gente (base, peligro, granja, tienda). Este script es el que publica esa
#  lista — y el que BORRA del mapa los iconos de estructuras que quedaran de
#  antes, porque BlueMap reescribe el fichero de marcadores entero cada vez.
#
#  Son tres pasos:
#
#    1) scan-structures.py    → escribe el bloque `marker-sets` en las configs
#                               de BlueMap (solo los lugares)
#    2) bluemap-cli --markers → vuelca esas configs al mapa web
#    3) comprobar             → LEE lo que quedó publicado y lo dice
#
#  El paso 2 es una operación APARTE del render (`-r`): sin él, el paso 1 no se
#  ve por ningún lado. Y el paso 3 existe porque este script se dio por bueno
#  una vez sin que nadie mirara el resultado, y los iconos seguían ahí.
#
#  Uso:  bash ~/panel/scripts/marcadores.sh            (rápido, segundos)
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

# El registro es compartido con render-mapa.sh, que lo rota a los 5 MB. Este
# script también escribe en él, así que rota igual: si no, corriéndolo a menudo
# el fichero podía crecer entre renders sin que nadie lo mirara.
if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 5000000 ]; then
  mv -f "$LOG" "$LOG.1"
fi

decir() {
  local linea="[$(date '+%F %T')] $*"
  echo "$linea" >> "$LOG"
  echo "$linea"
}

# Lo normal ya no necesita releer el mundo: el bloque que se publica son los
# lugares puestos a mano, que viven en panel/data/markers.json. `--rapido`
# reutiliza el escaneo guardado y el botón del panel pasa de minutos a
# segundos. `--a-fondo` sigue releyendo las ~340 regiones para refrescar
# data/structures.json, que es lo que usa probar-estructuras.py.
ARGS="--rapido"
case " $* " in *" --a-fondo "*) ARGS="--completo" ;; esac

ARRANQUE=$(date +%s)
decir "───────── iconos: inicio ─────────"

decir "1/4 escribiendo los marcadores en las configs${ARGS:+ ($ARGS)}…"
if ! nice -n 19 ionice -c3 python3 "$PANEL/scripts/scan-structures.py" $ARGS 2>&1 | tee -a "$LOG"; then
  decir "⚠ el escaneo falló; no sigo"
  exit 1
fi

decir "2/4 publicando los marcadores en el mapa…"
if nice -n 19 ionice -c3 java -Xmx1536M -jar bluemap-cli.jar --markers >> "$LOG" 2>&1; then
  decir "marcadores publicados"
else
  CODIGO=$?
  decir "⚠ no pude publicar los marcadores (código $CODIGO)"
  exit "$CODIGO"
fi

# ---- 3/4 comprobar QUÉ quedó publicado --------------------------------------
# BlueMap escribe el fichero de marcadores de cada mapa entero cada vez, así que
# lo que hay en él ES lo que se ve en el navegador. Leerlo es la única forma de
# saber si el trabajo se hizo, y ya salvó una vez de dar por bueno un botón que
# no cambiaba nada.
decir "3/4 comprobando lo que quedó publicado…"
COMPROBACION="$(BM="$BM" python3 - <<'PY' 2>&1
import json, os, re, gzip
from pathlib import Path

bm = Path(os.environ["BM"])

# La carpeta donde BlueMap deja los mapas la manda config/storages/file.conf.
raiz = "web/maps"
conf = bm / "config/storages/file.conf"
try:
    m = re.search(r'^\s*root\s*[:=]\s*"?([^"\n]+)"?', conf.read_text(), re.M)
    if m:
        raiz = m.group(1).strip()
except Exception:
    pass
mapas = (bm / raiz) if not os.path.isabs(raiz) else Path(raiz)

# Que ~/bluemap/web sea un enlace a /var/www/bluemap-web es lo que hace que lo
# que escribe BlueMap sea lo que sirve Caddy. Si algún día deja de serlo, el
# mapa se congela sin dar ni un error: por eso se dice en voz alta.
web = bm / "web"
try:
    destino = os.path.realpath(web)
    print("carpeta web: %s → %s%s" % (web, destino,
          "" if web.is_symlink() else "   ⚠ NO es un enlace"))
except Exception as e:
    print("carpeta web: no la puedo mirar (%s)" % e)

if not mapas.is_dir():
    print("⚠ no encuentro los mapas en %s" % mapas)
    raise SystemExit(3)

sobran = []
vistos = 0
for d in sorted(p for p in mapas.iterdir() if p.is_dir()):
    f = d / "live/markers.json"
    crudo = None
    for cand in (f, f.with_suffix(".json.gz")):
        if cand.exists():
            b = cand.read_bytes()
            crudo = gzip.decompress(b) if cand.suffix == ".gz" else b
            f = cand
            break
    if crudo is None:
        print("  %-12s (sin fichero de marcadores)" % d.name)
        continue
    vistos += 1
    try:
        datos = json.loads(crudo)
    except Exception as e:
        print("  %-12s ⚠ ilegible (%s)" % (d.name, e))
        continue
    partes = []
    for clave, cjto in sorted(datos.items()):
        n = len((cjto or {}).get("markers") or {})
        partes.append("%s (%d)" % (clave, n))
        # Cualquier cosa que no sean los lugares puestos a mano sobra: los
        # iconos de estructuras se fueron a la pestaña Explorar.
        if clave != "lugares":
            sobran.append("%s → %s" % (d.name, clave))
    print("  %-12s %s" % (d.name, ", ".join(partes) if partes else "(vacío) ✔ sin iconos"))

if not vistos:
    print("⚠ ningún mapa tiene fichero de marcadores todavía")
    raise SystemExit(3)
if sobran:
    print("⚠ SIGUE HABIENDO iconos que no son lugares: " + "; ".join(sobran))
    raise SystemExit(4)
print("✔ en el mapa 3D solo quedan los lugares del server")
PY
)"
RES=$?
while IFS= read -r ln; do decir "    $ln"; done <<< "$COMPROBACION"
[ "$RES" -ge 3 ] && decir "⚠ la comprobación no quedó limpia (código $RES)"

# ---- 4/4 reaplicar el parche del mapa ----------------------------------------
# ESTO FALTABA. El parche es el que inyecta la etiqueta de /static/biomas.js en
# la página de BlueMap, con su ?v=N. Ahí vive el CSS que enseña los iconos de
# 64 px a 32 y que hace nítidos los marcadores; sin reaplicarlo, la página del
# mapa sigue pidiendo la versión vieja del fichero y los iconos salen al doble.
#
# Es exactamente el mismo despiste que ya tuvo el botón «Actualizar el mapa» en
# su día: se arregló en render-mapa.sh y se repitió aquí al escribir el script.
if [ -f "$PANEL/scripts/parche-bluemap.sh" ]; then
  decir "4/4 reaplicando el parche del mapa…"
  bash "$PANEL/scripts/parche-bluemap.sh" >> "$LOG" 2>&1 \
    && decir "parche reaplicado" \
    || decir "⚠ no pude reaplicar el parche del mapa"
fi

MINUTOS=$(( ($(date +%s) - ARRANQUE) / 60 ))
decir "───────── iconos: fin, $MINUTOS min ─────────"
decir "Recarga el mapa en el navegador (Cmd+Shift+R) y deberían estar."
exit 0
