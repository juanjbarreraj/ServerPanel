#!/usr/bin/env python3
"""
Censo de mascotas y bichos con nombre: quién hay, y quién ha dejado de estar.

POR QUÉ HAY QUE HACERLO ASÍ
---------------------------
Minecraft **no apunta en ningún sitio** que un mob se haya muerto. No hay línea
en el log, no hay fichero, no hay evento. Lo comprobé en el jar del servidor:
la lista entera de disparadores de logro (`CriteriaTriggers`) tiene 58 entradas
y ninguna es «un bicho ha muerto». Las dos que se le acercan son

    player_killed_entity     un jugador mata a algo
    entity_killed_player     algo mata a un jugador

O sea que del datapack de vigilancia (`vigilancia.py`) solo se puede sacar la
muerte que causa un JUGADOR. Y las mascotas casi nunca mueren así: mueren de un
creeper, de una caída, de lava, ahogadas, de un esqueleto.

La única señal que queda es **que el bicho deje de estar en los ficheros del
mundo**. Un animal domesticado o con nombre no despawnea nunca, así que si
desaparece de `entities/*.mca` es que murió. Eso es lo que hace esto.

CÓMO SE EVITAN LOS FALSOS POSITIVOS
-----------------------------------
Un bicho puede «no estar» sin haber muerto, y cada caso tiene su respuesta:

  · se movió a otro chunk y el chunk de destino aún no se ha guardado
        → antes de dar a nadie por muerto se pide `save-all flush` y se vuelve
          a mirar el mundo ENTERO, no solo los ficheros que cambiaron;
  · se fue al Nether o al End
        → el repaso mira las tres dimensiones;
  · va montado en una barca, en un minecarta o encima de otro bicho
        → se recorren los `Passengers`;
  · es un loro y está posado en el hombro de alguien
        → entonces no es una entidad del mundo: vive dentro del `.dat` del
          jugador, y ahí se mira también;
  · se cambió de mundo
        → el censo es de UN mundo; al cambiar se tira y se empieza de cero, sin
          dar por muerto a nadie.

Y aun así no se anuncia a la primera: hace falta que siga sin aparecer en una
segunda pasada, al menos ESPERA segundos después. Vale más tardar seis minutos
en contar una muerte que contar una que no fue.

LO QUE NO SE PUEDE SABER
------------------------
Cómo murió, y quién lo mató (salvo que lo matara un jugador y el datapack de
vigilancia lo pillara). No está escrito en ninguna parte.

USO
---
    python3 ~/panel/scripts/mascotas.py              # quién hay ahora mismo
    python3 ~/panel/scripts/mascotas.py --pasada     # una pasada como la del panel
    python3 ~/panel/scripts/mascotas.py --olvidar    # borra el censo y empieza de cero
"""
import importlib.util, json, os, sys, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
import nbt                                                    # noqa: E402

HOME  = Path.home()
MC    = Path(os.environ.get("MC_DIR", HOME / "minecraft"))
PANEL = Path(os.environ.get("PANEL_DIR", AQUI.parent))
ESTADO_F = PANEL / "data" / "mascotas.json"

# Cuánto tiene que llevar sin aparecer antes de darlo por muerto.
ESPERA = 120

# Un mob puede tener `Owner` sin ser una mascota (una flecha guarda quién la
# tiró, los colmillos del evocador también). Todos esos tienen algo en común:
# no tienen vida. Pedir `Health` deja fuera flechas, items, cuadros, marcos,
# pantallas, barcas y minecartas de una sola vez.
NO_CUENTAN = {
    "armor_stand",      # los maniquíes del panel son armor_stand: no son mascotas
    "mannequin",
    "player",
}


def _modulo(nombre, fichero):
    spec = importlib.util.spec_from_file_location(nombre, AQUI / fichero)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# Se reutiliza el lector de mundo que ya existe en vez de copiarlo otra vez: la
# forma de leer un `CustomName` o un `Owner` ha cambiado varias veces entre
# versiones y no puede haber dos sitios donde eso se entienda de forma distinta.
_be = None

def be():
    global _be
    if _be is None:
        _be = _modulo("buscar_entidad", "buscar-entidad.py")
    # se reapunta en cada uso: el panel importa este módulo y le cambia MC
    # después, y si esto solo se hiciera al cargarlo miraría el mundo de otro.
    _be.MC = MC
    _be.WORLD = MC / "world"
    return _be


# ---------------------------------------------------------------- identidad
def uuid_de(ent):
    """El UUID de la entidad: cuatro enteros desde 1.16. Es lo único que no
    cambia aunque le renombren o se mueva."""
    t = nbt.cget(ent, "UUID")
    if t is None:
        return None
    try:
        v = list(t.v.items) if hasattr(t.v, "items") else list(t.v)
    except Exception:
        return None
    if len(v) != 4:
        return None
    u = "".join("%08x" % (x & 0xFFFFFFFF) for x in v)
    return "%s-%s-%s-%s-%s" % (u[0:8], u[8:12], u[12:16], u[16:20], u[20:32])


def dueño_uuid(ent):
    """El UUID del dueño, con guiones. `Owner` es 4 enteros en las versiones
    nuevas y una cadena en las viejas."""
    t = nbt.cget(ent, "Owner") or nbt.cget(ent, "OwnerUUID")
    if t is None:
        return None
    v = t.v
    if isinstance(v, bytes):
        s = v.decode("utf-8", "replace").strip()
        return s or None
    try:
        items = list(v.items) if hasattr(v, "items") else list(v)
    except Exception:
        return None
    if len(items) != 4:
        return None
    u = "".join("%08x" % (x & 0xFFFFFFFF) for x in items)
    return "%s-%s-%s-%s-%s" % (u[0:8], u[8:12], u[12:16], u[16:20], u[20:32])


def ficha(ent, dim):
    """Devuelve la ficha si es una mascota o un bicho con nombre; si no, None."""
    tipo = be().id_de(ent)
    if tipo in NO_CUENTAN:
        return None
    if nbt.cget(ent, "Health") is None:        # no es un ser vivo
        return None
    dueño = dueño_uuid(ent)
    nombre = be().nombre_de(ent)
    if not dueño and not nombre:
        return None
    u = uuid_de(ent)
    if not u:
        return None
    return u, {"n": nombre, "tipo": tipo, "dueño": dueño, "dim": dim,
               "pos": be().pos_de(ent)}


# ------------------------------------------------------------------ escaneo
def ficheros_de_region():
    """[(clave, dim, ruta)] de las regiones de ENTIDADES de las tres dimensiones.

    🔴 `region/` solo se mira si NO hay `entities/`. Desde 1.17 las entidades
    viven en su propia carpeta y `region/` son los BLOQUES: los ficheros gordos,
    cientos de megas, que además se reescriben cada vez que alguien pone o quita
    algo. Meterlos aquí sería descomprimir y parsear el mundo entero cada tres
    minutos para no encontrar ni un bicho. Solo los mundos anteriores a 1.17
    guardan las entidades ahí, y para esos sí hay que mirarlas."""
    fuera = []
    for dim in ("overworld", "nether", "end"):
        carp = be().carpetas(dim, "entities")
        cual = "entities"
        if not carp:
            carp, cual = be().carpetas(dim, "region"), "region"
        if not carp:
            continue
        for f in sorted(carp.glob("r.*.mca")):
            fuera.append(("%s/%s/%s" % (dim, cual, f.name), dim, f))
    return fuera


def mirar(ficheros):
    """{uuid: ficha} de lo que haya en esos ficheros. La ficha lleva `reg`."""
    hallados = {}
    for clave, dim, f in ficheros:
        for root in be().leer_region(f):
            lista = nbt.cget(root.v, "Entities")
            if lista is None:
                continue
            for cruda in (getattr(lista.v, "items", None) or []):
                for ent in be().recorrer(cruda):
                    r = ficha(ent, dim)
                    if r:
                        r[1]["reg"] = clave
                        hallados[r[0]] = r[1]
    return hallados


def en_hombros():
    """Los loros posados en el hombro de alguien NO son entidades del mundo:
    viven dentro del `.dat` del jugador. Sin mirar aquí, sentarse un loro en el
    hombro parecería que se murió."""
    fuera = set()
    for sub in ("world/players/data", "world/playerdata"):
        carp = MC / sub
        if not carp.is_dir():
            continue
        for p in carp.glob("*.dat"):
            try:
                _n, root, _gz = nbt.load(p)
            except Exception:
                continue
            for lado in ("ShoulderEntityLeft", "ShoulderEntityRight"):
                t = nbt.cget(root.v, lado)
                if t is None or not isinstance(t.v, list):
                    continue
                u = uuid_de(t.v)
                if u:
                    fuera.add(u)
    return fuera


def mundo_actual():
    try:
        return json.loads((MC / "mundos" / "activo.json").read_text()).get("slug") or "?"
    except Exception:
        return "?"          # panel sin la pestaña Mundo: siempre el mismo


# ------------------------------------------------------------------- estado
def cargar():
    try:
        d = json.loads(ESTADO_F.read_text())
        if isinstance(d, dict) and d.get("v") == 1:
            return d
    except Exception:
        pass
    return {"v": 1, "mundo": None, "t": 0, "bichos": {}, "regiones": {},
            "sospechosos": {}}


def guardar(est):
    ESTADO_F.parent.mkdir(parents=True, exist_ok=True)
    tmp = ESTADO_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(est))
    os.replace(tmp, ESTADO_F)


# ------------------------------------------------------------------ la pasada
def pasada(guardar_ya=None, ahora=None):
    """Una vuelta. Devuelve qué ha cambiado.

    `guardar_ya` es una función que pide al servidor `save-all flush` (el panel
    le pasa la suya por RCON). Solo se llama cuando hay alguien que podría
    haberse muerto, que es casi nunca: no se molesta al juego por mirar.
    """
    ahora = ahora or time.time()
    est = cargar()
    salida = {"muertas": [], "nuevas": [], "leidos": 0, "total": 0,
              "completo": False, "primera": False, "cambio_de_mundo": False}

    mundo = mundo_actual()
    if est.get("mundo") != mundo:
        # Un censo que nunca ha existido no es «un cambio de mundo»: la primera
        # vez no se ha cambiado de nada.
        habia = est.get("mundo") is not None
        # Otro mundo: sus bichos son otros. Comparar los de antes con los de
        # ahora daría por muertas a TODAS las mascotas del mundo anterior.
        est = {"v": 1, "mundo": mundo, "t": 0, "bichos": {}, "regiones": {},
               "sospechosos": {}}
        salida["cambio_de_mundo"] = habia

    todos = ficheros_de_region()
    salida["total"] = len(todos)
    ahora_mt = {}
    for clave, dim, f in todos:
        try:
            ahora_mt[clave] = f.stat().st_mtime
        except OSError:
            pass

    primera = not est["regiones"] and not est["bichos"]
    salida["primera"] = primera
    if primera:
        # La primera vez no se puede echar de menos a nadie: no había censo.
        est["bichos"] = mirar(todos)
        salida["leidos"] = len(todos)
        salida["completo"] = True
    else:
        cambiados = [x for x in todos
                     if ahora_mt.get(x[0]) != est["regiones"].get(x[0])]
        salida["leidos"] = len(cambiados)
        hallados = mirar(cambiados)
        clave_cambiada = {x[0] for x in cambiados}

        # Quién se conocía ANTES de esta pasada. Hace falta guardarlo aquí: en
        # cuanto se mezclan los hallazgos, un bicho que acaba de aparecer ya
        # parece de toda la vida, y entonces no se le puede reconocer como el
        # gemelo del que falta (el caso del loro que vuelve con otro UUID).
        conocidos_antes = set(est["bichos"])
        nuevas = {}
        for u, f in hallados.items():
            if u not in est["bichos"]:
                nuevas[u] = f
            est["bichos"][u] = f

        # Un sospechoso que reaparece en cualquier fichero que haya cambiado
        # deja de serlo, y eso sale gratis.
        for u in list(est["sospechosos"]):
            if u in hallados:
                est["sospechosos"].pop(u, None)

        # Sospechoso: el fichero donde vivía se ha reescrito y ya no está en él.
        faltan = [u for u, f in est["bichos"].items()
                  if f.get("reg") in clave_cambiada and u not in hallados
                  and u not in est["sospechosos"]]
        if faltan:
            # Barato y quita el caso más molesto: un loro en el hombro no está
            # en el mundo y lo estaría dando por perdido cada tres minutos.
            hombros = en_hombros()
            for u in faltan:
                if u not in hombros:
                    est["sospechosos"][u] = ahora

        # 🔴 Aquí está la diferencia entre que esto cueste nada o cueste caro.
        # Lo NORMAL es que un bicho «falte» un rato porque su dueño lo lleva
        # detrás y ha cruzado a otra región que aún no se ha guardado. Si por
        # cada una de esas se repasara el mundo entero y se pidiera al juego un
        # `save-all flush`, un lobo siguiendo a alguien costaría un repaso cada
        # tres minutos. Así que primero se le da tiempo: solo se mira en serio
        # a quien lleva ya ESPERA segundos sin aparecer por ninguna parte.
        maduros = [u for u, d in est["sospechosos"].items() if ahora - d >= ESPERA]

        if maduros:
            if guardar_ya:
                try:
                    guardar_ya()
                    time.sleep(4)
                except Exception:
                    pass
                for clave, dim, f in todos:
                    try:
                        ahora_mt[clave] = f.stat().st_mtime
                    except OSError:
                        pass
            vivos = mirar(todos)
            salida["leidos"] = len(todos)
            salida["completo"] = True
            hombros = en_hombros()

            # Un loro que se sube al hombro de alguien deja de ser una entidad
            # del mundo, y al bajarse puede volver con OTRO UUID. Si aparece uno
            # igual —misma especie, mismo dueño, mismo nombre— y no lo conocía,
            # es el mismo bicho: se le cambia la ficha en vez de enterrarlo.
            desconocidos = [(u, f) for u, f in vivos.items()
                            if u not in conocidos_antes]

            def gemelo(f):
                for u2, f2 in desconocidos:
                    if (f2.get("tipo") == f.get("tipo")
                            and f2.get("dueño") == f.get("dueño")
                            and f2.get("n") == f.get("n")):
                        return u2, f2
                return None

            # Se revisa a TODOS los conocidos, no solo a los del fichero que
            # cambió: después de mirar el mundo entero, no estar es no estar.
            for u in list(est["bichos"]):
                if u in vivos:
                    est["bichos"][u] = vivos[u]
                    est["sospechosos"].pop(u, None)
                    continue
                if u in hombros:
                    est["sospechosos"].pop(u, None)      # va de paseo en el hombro
                    continue
                g = gemelo(est["bichos"][u])
                if g:
                    desconocidos.remove(g)
                    nuevas.pop(g[0], None)
                    est["bichos"].pop(u, None)
                    est["bichos"][g[0]] = g[1]
                    est["sospechosos"].pop(u, None)
                    continue
                est["sospechosos"].setdefault(u, ahora)
        salida["nuevas"] = [dict(f, uuid=u) for u, f in nuevas.items()]

        # Se da por muerto lo que lleve ESPERA segundos sin aparecer, y solo si
        # esta pasada ha mirado el mundo entero: si no, no se ha buscado bien.
        if salida["completo"]:
            for u, desde in list(est["sospechosos"].items()):
                if ahora - desde < ESPERA:
                    continue
                f = est["bichos"].pop(u, None) or {}
                est["sospechosos"].pop(u, None)
                salida["muertas"].append(dict(f, uuid=u, t=desde))

    est["regiones"] = ahora_mt
    est["t"] = ahora
    est["mundo"] = mundo
    guardar(est)
    return salida


# --------------------------------------------------------------------- CLI
def _pinta(f):
    quien = f.get("n") or "(sin nombre)"
    p = f.get("pos")
    return "  %-26s %-14s %-10s %s%s" % (
        quien, f.get("tipo") or "?", f.get("dim") or "?",
        ("x %d y %d z %d" % tuple(p)) if p else "",
        ("   de %s" % f["dueño"][:8]) if f.get("dueño") else "")


def main():
    if "--olvidar" in sys.argv:
        if ESTADO_F.exists():
            ESTADO_F.unlink()
            print("censo borrado: la próxima pasada empieza de cero")
        else:
            print("no había censo que borrar")
        return 0

    if "--pasada" in sys.argv:
        t0 = time.time()
        r = pasada()
        print("mundo «%s» · %d/%d ficheros leídos%s · %.1f s"
              % (mundo_actual(), r["leidos"], r["total"],
                 " (repaso completo)" if r["completo"] else "", time.time() - t0))
        if r["cambio_de_mundo"]:
            print("  se cambió de mundo: censo empezado de cero, nadie dado por muerto")
        if r["primera"]:
            print("  primera pasada: solo se ha apuntado quién hay")
        for f in r["nuevas"]:
            print("  + nuevo   %s" % _pinta(f).strip())
        for f in r["muertas"]:
            print("  ✝ MURIÓ   %s" % _pinta(f).strip())
        if not r["nuevas"] and not r["muertas"] and not r["primera"]:
            print("  sin novedades")
        return 0

    t0 = time.time()
    todos = ficheros_de_region()
    print("leyendo %d ficheros de entidades…" % len(todos))
    hallados = mirar(todos)
    con_dueño = [f for f in hallados.values() if f.get("dueño")]
    con_nombre = [f for f in hallados.values() if f.get("n")]
    print("\n%d mascotas y bichos con nombre  (%.1f s)" % (len(hallados), time.time() - t0))
    print("  · %d domesticados (tienen dueño)" % len(con_dueño))
    print("  · %d con nombre puesto" % len(con_nombre))
    print()
    for f in sorted(hallados.values(), key=lambda f: (not f.get("n"), f.get("tipo") or "")):
        print(_pinta(f))
    return 0


if __name__ == "__main__":
    sys.exit(main())
