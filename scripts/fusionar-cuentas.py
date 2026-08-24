#!/usr/bin/env python3
"""Fusiona cuentas viejas con los perfiles actuales del servidor.

Qué hace (decidido con Juan, agosto 2026):

  FUSIONES (solo estadísticas, se SUMAN al perfil actual):
      josemori12  -> josemori67
      Sofiadiaz   -> sofidiaz
      kkkren      -> kkrenalga0228
      Nachardo    -> tommy__odd      (viejas + las del servidor actual)

  ALTAS (estadísticas + sus items, SIN xp y SIN logros):
      ZCP2007, tommy__odd
      Si ya tienen .dat en el servidor (viajó con el mundo y ya se puso en el
      spawn en su día), NO se toca: ese es el bueno. Solo si no lo tuvieran se
      escribe el del respaldo, con la posición forzada al spawn y el xp a 0.
      Nunca se mezclan inventarios de DataVersion distinta: el conversor de
      Minecraft solo actúa sobre el archivo entero, así que meter items de 1.18
      en un perfil de 1.21 los rompería.

  RETIRADA de Nachardo:
      sus archivos se ARCHIVAN (no se borran) en ~/minecraft/players-archive/
      sale de whitelist.json, de ops.json y del login del panel
      entra tommy__odd en whitelist + login nuevo con contraseña temporal

Uso:
    python3 ~/panel/scripts/fusionar-cuentas.py             # simulacro, no toca nada
    python3 ~/panel/scripts/fusionar-cuentas.py --aplicar   # de verdad

El servidor de Minecraft TIENE QUE ESTAR APAGADO (guarda las stats en memoria
y al apagarse machacaría lo que escribamos).
"""
import json, os, secrets, shutil, subprocess, sys, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))          # ~/panel  -> para importar server.py
import nbt as _n
from server import MC_DIR, hash_pw, load_users, save_users, rcon_try, DEFAULT_PERMS

VIEJO   = Path(os.environ.get("OLDPLAYERS", Path.home() / "oldplayers"))
VIVO    = MC_DIR / "world/players"
ARCHIVO = MC_DIR / "players-archive"
SPAWN   = (886.5, 79.0, 191.5)
APLICAR = "--aplicar" in sys.argv or "--apply" in sys.argv
FORZAR  = "--forzar" in sys.argv
OTRAVEZ = "--otra-vez" in sys.argv
MARCA   = MC_DIR / ".fusion-cuentas-hecha.json"

# (nombre viejo, uuid viejo, nombre actual, uuid actual)
FUSIONES = [
    ("josemori12", "e26246dc-be26-3a63-9d9b-b6fcd172a8e9", "josemori67",    "7134fe20-e765-4cda-be15-b66c00607323"),
    ("Sofiadiaz",  "e7138714-01dc-4509-bf21-6b4f86147bd6", "sofidiaz",      "a29b2cb8-3d93-4815-a294-b35d530c6696"),
    ("kkkren",     "2f1c00a2-ca35-3d9c-b90b-f4d8531b472e", "kkrenalga0228", "482019ae-2e5f-4bc0-84f0-b665844fbb10"),
    ("Nachardo",   "00e074e0-0797-4989-889a-a08fa1127154", "tommy__odd",    "06d6f241-cfaa-430d-9893-80282bbf624a"),
]
ALTAS = [   # (nombre, uuid, hereda_dat_de_uuid o None -> usa su .dat viejo movido al spawn)
    ("ZCP2007",    "4c988640-bdad-38df-ac8a-08ad7223ae01", None),
    ("tommy__odd", "06d6f241-cfaa-430d-9893-80282bbf624a", "00e074e0-0797-4989-889a-a08fa1127154"),
]
RETIRA_NOMBRE = "Nachardo"
RETIRA_UUID   = "00e074e0-0797-4989-889a-a08fa1127154"

# contadores que NO son totales acumulados: son "desde la última vez"
NO_SUMAR = {"minecraft:time_since_death", "minecraft:time_since_rest"}

CAMBIOS = []          # (descripción, función que lo aplica)
AVISOS  = []


def log(msg=""):
    print(msg)


def paso(desc, fn=None):
    CAMBIOS.append((desc, fn))
    log(f"   • {desc}")


# ---------------------------------------------------------------- estadísticas
def leer_stats(p: Path):
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def sumar(dst: dict, src: dict):
    for k, v in src.items():
        if isinstance(v, dict):
            sumar(dst.setdefault(k, {}), v)
        elif isinstance(v, (int, float)):
            if k in NO_SUMAR:
                dst.setdefault(k, v)
            else:
                dst[k] = dst.get(k, 0) + v


def horas(st):
    return round((st or {}).get("stats", {}).get("minecraft:custom", {})
                 .get("minecraft:play_time", 0) / 20 / 3600, 1)


# ------------------------------------------------------------------- .dat NBT
def dat_leer(p: Path):
    nombre, root, gz = _n.load(p)
    return nombre, root, gz


def dat_version(root):
    t = _n.cget(root.v, "DataVersion")
    return t.v if t else 0


def dat_al_spawn(root):
    c = root.v
    pos = _n.cget(c, "Pos")
    if pos is not None:
        pos.v.items[:] = list(SPAWN)
    mot = _n.cget(c, "Motion")
    if mot is not None:
        mot.v.items[:] = [0.0, 0.0, 0.0]
    fd = _n.cget(c, "FallDistance")
    if fd is not None:
        fd.v = 0.0
    dim = _n.cget(c, "Dimension")
    if dim is not None:
        dim.v = b"minecraft:overworld" if dim.t == 8 else 0


def dat_sin_xp(root):
    for k in ("XpLevel", "XpTotal"):
        t = _n.cget(root.v, k)
        if t is not None:
            t.v = 0
    t = _n.cget(root.v, "XpP")
    if t is not None:
        t.v = 0.0


def _slot(item):
    t = _n.cget(item, "Slot")
    return t.v if t is not None else None


def _poner_slot(item, n):
    for par in item:
        if par[0] == b"Slot":
            par[1].v = n
            return True
    return False


def _describe(item):
    d = {}
    for nom, tag in item:
        d[nom.decode()] = tag.v
    iid = d.get("id", b"?")
    iid = iid.decode() if isinstance(iid, bytes) else str(iid)
    n = d.get("count", d.get("Count", 1))
    return f"{iid.replace('minecraft:', '')} x{n}"


def items_de(root):
    """Lista legible de todo lo que lleva encima y en el cofre del End."""
    out = []
    for clave in ("Inventory", "EnderItems"):
        t = _n.cget(root.v, clave)
        if t is None:
            continue
        for it in t.v.items:
            out.append(_describe(it) + ("" if clave == "Inventory" else " (cofre del End)"))
    return out


def resumen_dat(root):
    def n(clave):
        t = _n.cget(root.v, clave)
        return len(t.v.items) if t is not None else 0
    xp = _n.cget(root.v, "XpLevel")
    pos = _n.cget(root.v, "Pos")
    p = "?" if pos is None else "(%.0f, %.0f, %.0f)" % tuple(pos.v.items)
    return f"{n('Inventory')} items, {n('EnderItems')} en el cofre del End, " \
           f"nivel {xp.v if xp else 0}, en {p}"


def fusionar_inventarios(destino_root, origen_root):
    """Mete los items de origen en huecos libres del inventario de destino.
    Todo se remapea a slots 0..35 (los slots 100-103 / -106 ya no existen en 26.2).
    Devuelve (metidos, sobrantes)."""
    dc, oc = destino_root.v, origen_root.v
    oinv = _n.cget(oc, "Inventory")
    oend = _n.cget(oc, "EnderItems")

    def lista(comp, clave):
        """Devuelve el Tag de lista, creándolo si falta, y arregla el tipo de
        elemento cuando la lista está vacía (etype 0 = TAG_End, no serializable)."""
        t = _n.cget(comp, clave)
        if t is None:
            t = _n.Tag(9, _n.NList(10, []))
            comp.append([clave.encode(), t])
        if t.v.etype == 0:
            t.v.etype = 10
        return t

    dinv = lista(dc, "Inventory")
    dend = lista(dc, "EnderItems")
    metidos, sobrantes = [], []

    def libres(nl, tope):
        usados = {_slot(i) for i in nl.items} if nl else set()
        return [s for s in range(tope) if s not in usados]

    huecos_inv = libres(dinv.v, 36)
    huecos_end = libres(dend.v, 27)

    fuentes = []
    if oinv is not None:
        fuentes += list(oinv.v.items)
    if oend is not None:
        fuentes += list(oend.v.items)

    for it in fuentes:
        if huecos_inv:
            _poner_slot(it, huecos_inv.pop(0))
            dinv.v.items.append(it)
            metidos.append(_describe(it))
        elif huecos_end:
            _poner_slot(it, huecos_end.pop(0))
            dend.v.items.append(it)
            metidos.append(_describe(it) + " (cofre del End)")
        else:
            sobrantes.append(_describe(it))
    return metidos, sobrantes


# ------------------------------------------------------------------- ficheros
def json_leer(p: Path, por_defecto):
    try:
        return json.loads(p.read_text())
    except Exception:
        return por_defecto


def escribir_json(p: Path, data):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


def mc_encendido():
    try:
        r = subprocess.run(["systemctl", "is-active", "minecraft"],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


# =============================================================== programa
def main():
    log("=" * 72)
    log("  FUSIÓN DE CUENTAS  " + ("— APLICANDO DE VERDAD" if APLICAR else "— SIMULACRO (no se toca nada)"))
    log("=" * 72)

    if not VIEJO.is_dir():
        log(f"\n✗ No encuentro {VIEJO}. Súbelo primero desde el Mac:")
        log("    scp -i ~/oracle-mc.key -r ~/Downloads/players ubuntu@132.145.136.215:~/oldplayers")
        return 1
    for sub in ("stats", "data"):
        if not (VIEJO / sub).is_dir():
            log(f"✗ Falta {VIEJO/sub}")
            return 1

    if MARCA.exists() and not OTRAVEZ:
        try:
            m = json.loads(MARCA.read_text())
        except Exception:
            m = {}
        log(f"\n✗ Esta fusión YA se aplicó el {m.get('fecha', '?')}.")
        log("  Volver a ejecutarla sumaría las mismas horas otra vez.")
        log(f"  Si de verdad hace falta repetirla, borra {MARCA} o añade --otra-vez.")
        return 1

    encendido = mc_encendido()
    log(f"\nMinecraft: {'ENCENDIDO' if encendido else 'apagado'}   "
        f"| datos viejos: {VIEJO}   | vivos: {VIVO}")
    if APLICAR and encendido and not FORZAR:
        log("\n✗ Apaga el servidor antes de aplicar (guarda las stats en memoria y")
        log("  las volvería a escribir encima al apagarse):")
        log("      sudo systemctl stop minecraft")
        log("  …y cuando termine el script:  sudo systemctl start minecraft")
        return 1

    ejecutar = []
    # uuid destino -> {"nombre", "base", "aportes": [(etiqueta, stats)]}
    aportes = {}

    def aporta(uuid, nombre, etiqueta, s):
        e = aportes.setdefault(uuid, {"nombre": nombre,
                                      "base": leer_stats(VIVO / "stats" / f"{uuid}.json"),
                                      "aportes": []})
        e["aportes"].append((etiqueta, s))

    # ---------------------------------------------------------- 0) copia de seguridad
    if APLICAR:
        ts = time.strftime("%Y%m%d-%H%M%S")
        dest = MC_DIR / "backups" / f"players-antes-fusion-{ts}.tar.gz"
        dest.parent.mkdir(exist_ok=True)
        subprocess.run(["tar", "czf", str(dest), "-C", str(VIVO.parent), VIVO.name], check=True)
        log(f"\n✔ Copia de seguridad: {dest}  ({dest.stat().st_size/1e6:.1f} MB)")

    # ---------------------------------------------------------- 1) estadísticas
    log("\n── 1. Estadísticas que se fusionan ─────────────────────────────────")
    for viejo_n, viejo_u, nuevo_n, nuevo_u in FUSIONES:
        s_viejo = leer_stats(VIEJO / "stats" / f"{viejo_u}.json")
        s_vivo = leer_stats(VIVO / "stats" / f"{viejo_u}.json") if viejo_u != nuevo_u else None

        log(f"\n  {viejo_n} → {nuevo_n}")
        if s_viejo:
            log(f"     {viejo_n:<14s} respaldo : {horas(s_viejo):7.1f} h   DataVersion {s_viejo.get('DataVersion')}")
        if s_vivo:
            log(f"     {viejo_n:<14s} servidor : {horas(s_vivo):7.1f} h   DataVersion {s_vivo.get('DataVersion')}")

        # MISMO UUID en el respaldo y en el servidor = el mismo archivo, el del
        # servidor es el más nuevo (aunque le hayan cambiado la DataVersion al
        # migrar el mundo). Sumar los dos sería contar las horas dos veces.
        if s_viejo and s_vivo:
            if horas(s_viejo) <= horas(s_vivo):
                log("     ↳ el respaldo es una copia anterior del MISMO archivo; se ignora "
                    "(si no, se contarían las horas dos veces)")
                s_viejo = None
            else:
                AVISOS.append(f"{viejo_n}: el respaldo tiene MÁS horas que el archivo del "
                              f"servidor ({horas(s_viejo)} vs {horas(s_vivo)}) — revisar a mano")

        if s_viejo:
            aporta(nuevo_u, nuevo_n, f"{viejo_n} (respaldo)", s_viejo)
        if s_vivo:
            aporta(nuevo_u, nuevo_n, f"{viejo_n} (servidor)", s_vivo)
        if not (s_viejo or s_vivo):
            log("     ↳ no hay nada que sumar")

    # ---------------------------------------------------------- 2) altas / .dat
    log("\n── 2. Perfiles nuevos (estadísticas + sus items, sin xp, sin logros) ──")
    for nombre, uuid, hereda in ALTAS:
        d_vivo  = VIVO  / "data" / f"{uuid}.dat"
        d_viejo = VIEJO / "data" / f"{uuid}.dat"
        d_padre = VIVO  / "data" / f"{hereda}.dat" if hereda else None
        log(f"\n  {nombre}")

        if d_vivo.exists():
            # Caso normal aquí: el .dat ya viajó con el mundo y ya lo pusimos en el
            # spawn en su día. Es el bueno: no se toca nada.
            _, root, _ = dat_leer(d_vivo)
            log(f"     ya tiene su .dat en el servidor (DataVersion {dat_version(root)}) — "
                f"{resumen_dat(root)}")
            log("     no se toca: es el suyo y ya está en el spawn")
            if d_padre is not None and d_padre.exists():
                _, pr, _ = dat_leer(d_padre)
                log(f"     ojo: el .dat de {RETIRA_NOMBRE} (DataVersion {dat_version(pr)}) "
                    f"tiene {resumen_dat(pr)}")
                for it in items_de(pr):
                    log(f"        · {it}")
                if items_de(pr):
                    AVISOS.append(f"{nombre}: las cosas de {RETIRA_NOMBRE} NO se pasan "
                                  f"(su .dat es de otra versión de Minecraft y mezclarlo las "
                                  f"corrompería). Si las quiere, se le dan con /give.")

        elif d_padre is not None and d_padre.exists():
            raiz, base_root, _ = dat_leer(d_padre)
            log(f"     no tiene .dat propio → hereda el de {RETIRA_NOMBRE} "
                f"(DataVersion {dat_version(base_root)}) — {resumen_dat(base_root)}")
            def hacer(root=base_root, d_vivo=d_vivo, raiz=raiz):
                d_vivo.parent.mkdir(parents=True, exist_ok=True)
                _n.save(d_vivo, raiz, root, gz=True)
            ejecutar.append(hacer)

        elif d_viejo.exists():
            raiz, root, _ = dat_leer(d_viejo)
            dat_al_spawn(root)
            dat_sin_xp(root)
            log(f"     .dat del respaldo (DataVersion {dat_version(root)}) → spawn {SPAWN}, "
                f"xp a 0 — {resumen_dat(root)}")
            def hacer(root=root, d_vivo=d_vivo, raiz=raiz):
                d_vivo.parent.mkdir(parents=True, exist_ok=True)
                _n.save(d_vivo, raiz, root, gz=True)
            ejecutar.append(hacer)
        else:
            log("     no hay .dat por ningún lado; solo tendrá estadísticas")

        # estadísticas: SIEMPRE (aunque el .dat no se toque)
        s_viejo = leer_stats(VIEJO / "stats" / f"{uuid}.json")
        s_vivo  = leer_stats(VIVO  / "stats" / f"{uuid}.json")
        if s_viejo and s_vivo and horas(s_viejo) <= horas(s_vivo):
            log(f"     estadísticas: ya están en el servidor ({horas(s_vivo)} h); "
                f"el respaldo ({horas(s_viejo)} h) se ignora")
        elif s_viejo:
            log(f"     estadísticas del respaldo: {horas(s_viejo)} h")
            aporta(uuid, nombre, f"{nombre} (respaldo)", s_viejo)
        else:
            log("     sin estadísticas en el respaldo")

    # logros de Nachardo → tommy__odd (los del servidor actual, no los del respaldo)
    a_nach = VIVO / "advancements" / f"{RETIRA_UUID}.json"
    a_tom = VIVO / "advancements" / "06d6f241-cfaa-430d-9893-80282bbf624a.json"
    if a_nach.exists() and not a_tom.exists():
        log(f"\n  Logros: los de {RETIRA_NOMBRE} EN ESTE SERVIDOR pasan a tommy__odd")
        log("          (no se importa ningún logro del respaldo viejo)")
        def hacer(a_nach=a_nach, a_tom=a_tom):
            a_tom.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(a_nach, a_tom)
        ejecutar.append(hacer)

    # ------------------------------------------------- resumen de estadísticas
    log("\n── Resultado de las estadísticas ──────────────────────────────────")
    for uuid, e in aportes.items():
        base = e["base"] or {"stats": {}, "DataVersion": 0}
        actual = horas(base)
        total = round(actual + sum(horas(s) for _, s in e["aportes"]), 1)
        detalle = " + ".join(f"{horas(s)} h de {et}" for et, s in e["aportes"])
        log(f"   {e['nombre']:<14s} {actual:7.1f} h  →  {total:7.1f} h     ({detalle})")

        def hacer(uuid=uuid, base=base, ap=list(e["aportes"])):
            out = json.loads(json.dumps(base))
            out.setdefault("stats", {})
            for _, s in ap:
                sumar(out["stats"], s.get("stats", {}))
            if not out.get("DataVersion"):
                out["DataVersion"] = max([s.get("DataVersion", 0) for _, s in ap] or [0])
            destino = VIVO / "stats" / f"{uuid}.json"
            destino.parent.mkdir(parents=True, exist_ok=True)
            escribir_json(destino, out)
        ejecutar.append(hacer)

    # ---------------------------------------------------------- 3) retirar Nachardo
    log("\n── 3. Retirada de Nachardo ────────────────────────────────────────")
    mover = []
    for sub, ext in (("stats", ".json"), ("advancements", ".json"),
                     ("data", ".dat"), ("data", ".dat_old")):
        f = VIVO / sub / f"{RETIRA_UUID}{ext}"
        if f.exists():
            mover.append(f)
    for f in mover:
        log(f"   archivar  {f.relative_to(VIVO)}  →  players-archive/")
    def hacer(mover=mover):
        if not mover:
            return
        ARCHIVO.mkdir(exist_ok=True)
        for f in mover:
            d = ARCHIVO / f.parent.name
            d.mkdir(exist_ok=True)
            shutil.move(str(f), str(d / f.name))
    ejecutar.append(hacer)

    # whitelist
    wl = json_leer(MC_DIR / "whitelist.json", [])
    nombres = {e.get("name", "").lower() for e in wl}
    nueva_wl = [e for e in wl if e.get("name", "").lower() != RETIRA_NOMBRE.lower()]
    for nombre, uuid, _h in ALTAS:
        if nombre.lower() not in nombres:
            nueva_wl.append({"uuid": uuid, "name": nombre})
            log(f"   whitelist +{nombre}")
    if len(nueva_wl) != len(wl) or nueva_wl != wl:
        log(f"   whitelist −{RETIRA_NOMBRE}   ({len(wl)} → {len(nueva_wl)} jugadores)")
        def hacer(nueva_wl=nueva_wl):
            escribir_json(MC_DIR / "whitelist.json", nueva_wl)
        ejecutar.append(hacer)

    # usercache (para que el panel muestre el nombre y no el uuid)
    uc = json_leer(MC_DIR / "usercache.json", [])
    uc_nombres = {e.get("name", "").lower() for e in uc}
    faltan = [(n, u) for n, u, _h in ALTAS if n.lower() not in uc_nombres]
    if faltan:
        log("   usercache +" + ", ".join(n for n, _ in faltan))
        def hacer(uc=uc, faltan=faltan):
            cad = time.strftime("%d %b %Y %H:%M:%S +0000", time.gmtime(time.time() + 365 * 86400))
            for n, u in faltan:
                uc.append({"name": n, "uuid": u, "expiresOn": cad})
            escribir_json(MC_DIR / "usercache.json", uc)
        ejecutar.append(hacer)

    # ops
    ops = json_leer(MC_DIR / "ops.json", [])
    era_op = next((e for e in ops if e.get("name", "").lower() == RETIRA_NOMBRE.lower()), None)
    if era_op:
        nuevos_ops = [e for e in ops if e is not era_op]
        nuevos_ops.append({"uuid": "06d6f241-cfaa-430d-9893-80282bbf624a", "name": "tommy__odd",
                           "level": era_op.get("level", 4),
                           "bypassesPlayerLimit": era_op.get("bypassesPlayerLimit", False)})
        log(f"   ops: −{RETIRA_NOMBRE}, +tommy__odd (nivel {era_op.get('level', 4)})")
        def hacer(nuevos_ops=nuevos_ops):
            escribir_json(MC_DIR / "ops.json", nuevos_ops)
        ejecutar.append(hacer)
    else:
        log(f"   ops: {RETIRA_NOMBRE} no era operador")

    # login del panel
    users = load_users()
    clave_nach = next((k for k in users if k.lower() == RETIRA_NOMBRE.lower()), None)
    pw_nueva = None
    if clave_nach:
        viejo = users[clave_nach]
        pw_nueva = secrets.token_urlsafe(9)
        log(f"   panel: se borra el usuario '{clave_nach}' (rol {viejo.get('role')}) y se crea 'tommy__odd'")
        def hacer(users=users, clave_nach=clave_nach, viejo=viejo, pw=pw_nueva):
            del users[clave_nach]
            users["tommy__odd"] = {"hash": hash_pw(pw),
                                   "role": viejo.get("role", "mod"),
                                   "perms": viejo.get("perms", {}),
                                   "must_change": True}
            save_users(users)
        ejecutar.append(hacer)
    else:
        log("   panel: Nachardo no tiene usuario en el panel")

    # ---------------------------------------------------------- aplicar
    log("\n" + "=" * 72)
    if AVISOS:
        log("AVISOS:")
        for a in AVISOS:
            log("   ! " + a)
        log("")

    if not APLICAR:
        log(f"SIMULACRO: {len(ejecutar)} cambios listos. Nada se ha tocado.")
        log("Para aplicarlo de verdad:")
        log("   sudo systemctl stop minecraft")
        log("   python3 ~/panel/scripts/fusionar-cuentas.py --aplicar")
        log("   sudo systemctl start minecraft")
        return 0

    for fn in ejecutar:
        fn()
    MARCA.write_text(json.dumps({"fecha": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "cambios": len(ejecutar),
                                 "fusiones": [[a, c] for a, _b, c, _d in FUSIONES],
                                 "altas": [n for n, _u, _h in ALTAS],
                                 "retirado": RETIRA_NOMBRE}, indent=2, ensure_ascii=False))
    log(f"✔ {len(ejecutar)} cambios aplicados.")
    if pw_nueva:
        log("")
        log("  ┌──────────────────────────────────────────────┐")
        log(f"  │  Panel — usuario: tommy__odd                 │")
        log(f"  │  contraseña temporal: {pw_nueva:<22s} │")
        log("  │  (se la pedirá cambiar al entrar)            │")
        log("  └──────────────────────────────────────────────┘")
    log("\nArranca el servidor:  sudo systemctl start minecraft")
    log("y luego recarga el panel:  sudo systemctl restart panel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
