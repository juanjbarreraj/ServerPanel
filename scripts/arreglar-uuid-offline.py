#!/usr/bin/env python3
"""
Pasa a un jugador de UUID offline (pirata) a su UUID premium de verdad.

El problema: el servidor está en `online-mode=true`, así que cuando alguien se
conecta Mojang le asigna su UUID premium (v4). Si su entrada de la whitelist y
sus archivos están bajo un UUID offline (v3, derivado del nombre con MD5 de
"OfflinePlayer:<nombre>"), pasan dos cosas: la whitelist no le reconoce y sus
estadísticas, inventario y logros nunca le llegan.

Qué hace:
  1. Quita sus entradas de whitelist.json y usercache.json (para que Minecraft
     no vuelva a resolver el nombre desde su propia caché).
  2. Le pide al servidor, por RCON, `whitelist add <nombre>` → Minecraft
     consulta a Mojang y escribe la entrada con el UUID premium correcto.
  3. Renombra sus archivos del UUID viejo al nuevo:
     world/players/{stats/*.json, data/*.dat, data/*.dat_old, advancements/*.json}
  4. Avisa de cualquier OTRA entrada de la whitelist que siga siendo offline.

El servidor tiene que estar ENCENDIDO (hace falta el RCON) y el jugador NO
puede estar conectado. Si la cuenta no es premium, el paso 2 falla y no se
toca ningún archivo.

Uso:
    python3 ~/panel/scripts/arreglar-uuid-offline.py ZCP2007            # simulacro
    python3 ~/panel/scripts/arreglar-uuid-offline.py ZCP2007 --aplicar

Si el jugador se cambió el nombre de la cuenta, el UUID offline ya no se puede
deducir del nombre nuevo: hay que decirle de qué UUID salen los archivos.
    python3 ~/panel/scripts/arreglar-uuid-offline.py NombreNuevo --desde 4c988640-...
"""
import hashlib, json, shutil, sys, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
from server import MC_DIR, rcon_try, online_players

VIVO = MC_DIR / "world/players"
APLICAR = "--aplicar" in sys.argv
_libres = [a for a in sys.argv[1:] if not a.startswith("--")]
DESDE = None
for _i, _a in enumerate(sys.argv):
    if _a == "--desde" and _i + 1 < len(sys.argv):
        DESDE = sys.argv[_i + 1].lower()
        if DESDE in _libres:
            _libres.remove(DESDE)
NOMBRE = _libres[0] if _libres else "ZCP2007"


def uuid_offline(nombre):
    h = bytearray(hashlib.md5(("OfflinePlayer:" + nombre).encode()).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    x = h.hex()
    return f"{x[0:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:32]}"


def es_offline(u):
    return len(u) == 36 and u[14] == "3"


def leer(p, por_defecto):
    try:
        return json.loads(p.read_text())
    except Exception:
        return por_defecto


def escribir(p, data):
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    tmp.replace(p)


def archivos_de(uuid):
    return [(VIVO / "stats" / f"{uuid}.json"),
            (VIVO / "advancements" / f"{uuid}.json"),
            (VIVO / "data" / f"{uuid}.dat"),
            (VIVO / "data" / f"{uuid}.dat_old")]


def main():
    wl_f = MC_DIR / "whitelist.json"
    uc_f = MC_DIR / "usercache.json"
    wl, uc = leer(wl_f, []), leer(uc_f, [])

    print("=" * 68)
    print("  %s → UUID premium   %s"
          % (NOMBRE, "APLICANDO" if APLICAR else "SIMULACRO (no toca nada)"))
    print("=" * 68)

    viejo = DESDE or uuid_offline(NOMBRE)
    actuales = [e for e in wl if (e.get("name") or "").lower() == NOMBRE.lower()]
    print("\n  UUID de origen         : %s  (%s)"
          % (viejo, "dado a mano" if DESDE else "calculado del nombre"))
    for e in actuales:
        print("  en la whitelist ahora  : %s  (%s)"
              % (e.get("uuid"), "offline" if es_offline(e.get("uuid", "")) else "premium"))
    if actuales and not any(es_offline(e.get("uuid", "")) for e in actuales):
        print("\n  ✔ Ya está con un UUID premium. No hay nada que hacer.")
        return 0

    tiene = [p for p in archivos_de(viejo) if p.exists()]
    print("\n  archivos suyos bajo el UUID viejo:")
    for p in tiene:
        print("     %s  (%.1f KB)" % (p.relative_to(VIVO), p.stat().st_size / 1024))
    if not tiene:
        print("     (ninguno)")

    otros = [e for e in wl if es_offline(e.get("uuid", ""))
             and (e.get("name") or "").lower() != NOMBRE.lower()
             and (e.get("uuid") or "").lower() != viejo]
    if otros:
        print("\n  ⚠ OTRAS entradas de la whitelist con UUID offline (mismo problema):")
        for e in otros:
            print("     %-20s %s" % (e.get("name"), e.get("uuid")))

    conectados = online_players() or []
    if NOMBRE.lower() in [c.lower() for c in conectados]:
        print("\n  ✗ %s está conectado ahora mismo. Que salga y vuelve a correr esto." % NOMBRE)
        return 1

    print("\n  Pasos:")
    print("     1. quitar %s de whitelist.json y usercache.json" % NOMBRE)
    print("     2. RCON: whitelist add %s   (Minecraft pregunta a Mojang)" % NOMBRE)
    print("     3. renombrar sus %d archivos al UUID nuevo" % len(tiene))

    if not APLICAR:
        print("\n  SIMULACRO. Para hacerlo de verdad, con el servidor encendido:")
        print("     python3 ~/panel/scripts/arreglar-uuid-offline.py %s --aplicar" % NOMBRE)
        return 0

    marca = time.strftime("%Y%m%d-%H%M%S")
    shutil.copy2(wl_f, wl_f.with_suffix(".json.antes-" + marca))
    def sobra(e):
        return ((e.get("name") or "").lower() == NOMBRE.lower()
                or (e.get("uuid") or "").lower() == viejo)
    escribir(wl_f, [e for e in wl if not sobra(e)])
    escribir(uc_f, [e for e in uc if not sobra(e)])
    print("\n  ✔ quitado de whitelist y usercache (respaldo: %s)"
          % wl_f.with_suffix(".json.antes-" + marca).name)

    ok, salida = rcon_try("whitelist reload")
    ok, salida = rcon_try("whitelist add %s" % NOMBRE)
    print("  RCON → %s" % (salida or "(sin respuesta)"))

    wl2 = leer(wl_f, [])
    nueva = next((e for e in wl2 if (e.get("name") or "").lower() == NOMBRE.lower()), None)
    if not nueva or es_offline(nueva.get("uuid", "")):
        escribir(wl_f, wl)          # dejarlo todo como estaba
        escribir(uc_f, uc)
        rcon_try("whitelist reload")
        print("\n  ✗ Minecraft no consiguió un UUID premium para %s." % NOMBRE)
        print("    O la cuenta no es de pago, o el servidor no pudo hablar con Mojang.")
        print("    No se ha renombrado ningún archivo y la whitelist quedó como estaba.")
        return 1

    nuevo = nueva["uuid"]
    print("  ✔ UUID premium: %s" % nuevo)

    movidos = 0
    for p in archivos_de(viejo):
        if not p.exists():
            continue
        destino = p.with_name(p.name.replace(viejo, nuevo))
        if destino.exists():
            print("     ! ya existe %s — no se pisa" % destino.relative_to(VIVO))
            continue
        shutil.move(str(p), str(destino))
        print("     %s → %s" % (p.name, destino.name))
        movidos += 1

    uc2 = leer(uc_f, [])
    if not any(e.get("uuid") == nuevo for e in uc2):
        cad = time.strftime("%Y-%m-%d %H:%M:%S +0000", time.gmtime(time.time() + 365 * 86400))
        uc2.append({"name": NOMBRE, "uuid": nuevo, "expiresOn": cad})
        escribir(uc_f, uc2)

    rcon_try("whitelist reload")
    print("\n  ✔ Listo: %d archivos renombrados. %s ya puede entrar y encontrará "
          "sus cosas." % (movidos, NOMBRE))
    print("     Recarga el panel:  sudo systemctl restart panel")
    return 0


if __name__ == "__main__":
    sys.exit(main())
