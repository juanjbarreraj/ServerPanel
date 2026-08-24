#!/usr/bin/env python3
"""Fusiona cuentas viejas con los perfiles actuales del servidor.

Qué hace (decidido con Juan, agosto 2026):

  FUSIONES (solo estadísticas, se SUMAN al perfil actual):
      josemori12  -> josemori67
      Sofiadiaz   -> sofidiaz
      kkkren      -> kkrenalga0228
      Nachardo    -> tommy__odd      (viejas + las del servidor actual)

  ALTAS (estadísticas + items del inventario, SIN xp y SIN logros):
      ZCP2007     nuevo en el servidor, aparece en el spawn (886.5, 79, 191.5)
      tommy__odd  hereda el .dat vivo de Nachardo (posición, inventario, xp
                  actuales) y encima se le meten sus items viejos

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

        # si el mismo uuid está en respaldo y en el servidor con la MISMA versión de
        # datos, el respaldo es una copia vieja del mismo archivo → no sumar dos veces
        if s_viejo and s_vivo and s_viejo.get("DataVersion") == s_vivo.get("DataVersion"):
            if horas(s_viejo) <= horas(s_vivo):
                log("     ↳ el respaldo es una copia antigua de lo que ya hay en el servidor; se ignora")
                s_viejo = None
            else:
                AVISOS.append(f"{viejo_n}: respaldo y servidor tienen la misma DataVersion "
                              f"pero el respaldo tiene MÁS horas — revisar a mano")

        if s_viejo:
            aporta(nuevo_u, nuevo_n, f"{viejo_n} (respaldo)", s_viejo)
        if s_vivo:
            aporta(nuevo_u, nuevo_n, f"{viejo_n} (servidor)", s_vivo)
        if not (s_viejo or s_vivo):
            log("     ↳ no hay nada que sumar")

    # ---------------------------------------------------------- 2) altas / .dat
    log("\n── 2. Perfiles nuevos (inventario, sin xp, sin logros) ─────────────")
    for nombre, uuid, hereda in ALTAS:
        d_vivo = VIVO / "data" / f"{uuid}.dat"
        d_viejo = VIEJO / "data" / f"{uuid}.dat"
        log(f"\n  {nombre}")

        if hereda:
            d_padre = VIVO / "data" / f"{hereda}.dat"
            if not d_padre.exists():
                log(f"     ✗ no existe {d_padre.name} en el servidor — se usará su .dat viejo")
                hereda = None

        if hereda:
            raiz, base_root, _ = dat_leer(VIVO / "data" / f"{hereda}.dat")
            log(f"     base: .dat de {RETIRA_NOMBRE} del servidor (DataVersion {dat_version(base_root)})"
                f" — conserva posición, xp e inventario actuales")
            if d_vivo.exists():   # ya había jugado con el nombre nuevo: no perder sus cosas
                _, suyo_root, _ = dat_leer(d_vivo)
                m, s = fusionar_inventarios(base_root, suyo_root)
                log(f"     ! ya tenía perfil propio en el servidor: se conservan sus {len(m)} items")
                AVISOS.append(f"{nombre}: ya tenía .dat propio; su posición/xp actuales se pierden "
                              f"(se queda con las de {RETIRA_NOMBRE}), pero sus items se conservan")
                for x in s:
                    AVISOS.append(f"{nombre}: no cupo {x}")
            if d_viejo.exists():
                _, viejo_root, _ = dat_leer(d_viejo)
                dv_b, dv_v = dat_version(base_root), dat_version(viejo_root)
                if dv_v >= 3818 and dv_b >= 3818:
                    metidos, sobrantes = fusionar_inventarios(base_root, viejo_root)
                    for m in metidos:
                        log(f"        + {m}")
                    for s in sobrantes:
                        log(f"        ! sin hueco: {s}")
                        AVISOS.append(f"{nombre}: no cupo {s}")
                else:
                    AVISOS.append(f"{nombre}: los items viejos son de DataVersion {dv_v} y el perfil "
                                  f"actual es {dv_b}; no se mezclan para no corromperlos")
                    log(f"     ! items viejos en formato antiguo (DataVersion {dv_v}) — NO se mezclan")
            def hacer(root=base_root, d_vivo=d_vivo, raiz=raiz):
                d_vivo.parent.mkdir(parents=True, exist_ok=True)
                _n.save(d_vivo, raiz, root, gz=True)
            ejecutar.append(hacer)
        else:
            if not d_viejo.exists():
                log("     ✗ no hay .dat viejo; solo tendrá estadísticas")
                continue
            raiz, root, _ = dat_leer(d_viejo)
            dv = dat_version(root)
            if d_vivo.exists():
                log(f"     ! ya tiene .dat en el servidor — NO se toca (se respeta lo actual)")
                AVISOS.append(f"{nombre}: ya existía en el servidor; su .dat no se sobrescribió")
                continue
            dat_al_spawn(root)
            dat_sin_xp(root)
            inv = _n.cget(root.v, "Inventory")
            end = _n.cget(root.v, "EnderItems")
            log(f"     .dat viejo (DataVersion {dv}) → spawn {SPAWN}, xp a 0, "
                f"{len(inv.v.items) if inv else 0} items + {len(end.v.items) if end else 0} en el cofre del End")
            def hacer(root=root, d_vivo=d_vivo, raiz=raiz):
                d_vivo.parent.mkdir(parents=True, exist_ok=True)
                _n.save(d_vivo, raiz, root, gz=True)
            ejecutar.append(hacer)

        # estadísticas propias del alta (las del respaldo)
        s_viejo = leer_stats(VIEJO / "stats" / f"{uuid}.json")
        if s_viejo:
            log(f"     estadísticas propias del respaldo: {horas(s_viejo)} h")
            aporta(uuid, nombre, f"{nombre} (respaldo)", s_viejo)

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
