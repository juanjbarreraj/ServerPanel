#!/usr/bin/env python3
"""
Genera comandos /give para devolverle a tommy__odd lo que tenía Nachardo.

Por qué esto y no una fusión de archivos: el .dat de Nachardo es de Minecraft
1.18 (DataVersion 2865) y el de tommy__odd de 1.21 (3953). El conversor de
Minecraft trabaja sobre el ARCHIVO ENTERO según su DataVersion raíz, así que
mezclar items de las dos épocas en un solo .dat rompe uno de los dos lados.
Con /give es el propio servidor el que crea los items en el formato correcto:
si un comando estuviera mal, simplemente falla y no se pierde nada.

Traduce el NBT viejo (tag/Enchantments/Damage/display) al formato moderno de
componentes (1.20.5+).

Uso:
    python3 ~/panel/scripts/rescatar-nachardo.py            # comandos listos
    python3 ~/panel/scripts/rescatar-nachardo.py --levels   # formato 1.20.5–1.21.4
    python3 ~/panel/scripts/rescatar-nachardo.py --dat RUTA --para NOMBRE
"""
import sys
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI.parent))
import nbt as _n
from server import MC_DIR

DESTINO = "tommy__odd"
DAT = MC_DIR / "players-archive" / "data" / "00e074e0-0797-4989-889a-a08fa1127154.dat"
LEVELS = "--levels" in sys.argv          # formato de encantamientos anterior a 1.21.5

for i, a in enumerate(sys.argv):
    if a == "--dat" and i + 1 < len(sys.argv):
        DAT = Path(sys.argv[i + 1])
    if a == "--para" and i + 1 < len(sys.argv):
        DESTINO = sys.argv[i + 1]


def txt(v):
    return v.decode() if isinstance(v, bytes) else str(v)


def con_espacio(iid):
    iid = txt(iid)
    return iid if ":" in iid else "minecraft:" + iid


def encantamientos(lista):
    """[{id, lvl}] (1.18)  ->  {"minecraft:x":N, ...}"""
    pares = []
    for e in lista.items:
        d = {k.decode(): t for k, t in e}
        pares.append('"%s":%d' % (con_espacio(d["id"].v), int(d["lvl"].v)))
    cuerpo = "{" + ",".join(pares) + "}"
    return "{levels:" + cuerpo + "}" if LEVELS else cuerpo


def componentes(tag):
    """El `tag` viejo -> [(clave, valor)] del formato moderno de componentes."""
    if tag is None:
        return []
    d = {k.decode(): t for k, t in tag.v}
    out = []
    if "Enchantments" in d and d["Enchantments"].v.items:
        out.append(("minecraft:enchantments", encantamientos(d["Enchantments"].v)))
    if "StoredEnchantments" in d and d["StoredEnchantments"].v.items:
        out.append(("minecraft:stored_enchantments", encantamientos(d["StoredEnchantments"].v)))
    if "Damage" in d and int(d["Damage"].v):
        out.append(("minecraft:damage", "%d" % int(d["Damage"].v)))
    if "RepairCost" in d and int(d["RepairCost"].v):
        out.append(("minecraft:repair_cost", "%d" % int(d["RepairCost"].v)))
    if "display" in d:
        nom = _n.cget(d["display"].v, "Name")
        if nom is not None:
            # en 1.18 el nombre es un JSON de texto; va como cadena SNBT entre comillas simples
            out.append(("minecraft:custom_name", "'%s'" % txt(nom.v).replace("'", "\\'")))
    if "Fireworks" in d:
        vuelo = _n.cget(d["Fireworks"].v, "Flight")
        out.append(("minecraft:fireworks",
                    "{flight_duration:%d,explosions:[]}" % int(vuelo.v if vuelo is not None else 1)))
    return out


def como_mapa(pares):
    """Dentro de components:{...} de un ItemStack NBT -> "clave":valor"""
    return ",".join('"%s":%s' % kv for kv in pares)


def como_corchetes(pares):
    """Detrás del id en /give -> [clave=valor,...]"""
    return "[" + ",".join("%s=%s" % kv for kv in pares) + "]" if pares else ""


def item_pila(comp):
    """Un item del NBT viejo -> {id:"...",count:N,components:{...}} moderno."""
    d = {k.decode(): t for k, t in comp}
    iid = con_espacio(d["id"].v)
    cnt = int(d["Count"].v if "Count" in d else d.get("count").v)
    partes = componentes(d.get("tag"))
    s = 'id:"%s",count:%d' % (iid, cnt)
    if partes:
        s += ",components:{" + como_mapa(partes) + "}"
    return "{" + s + "}", iid, cnt


def item_suelto(comp):
    """Un item -> el trozo [componentes] que va detrás del id en /give."""
    d = {k.decode(): t for k, t in comp}
    iid = con_espacio(d["id"].v)
    cnt = int(d["Count"].v if "Count" in d else d.get("count").v)
    return iid, como_corchetes(componentes(d.get("tag"))), cnt


def main():
    if not DAT.exists():
        print("✗ No encuentro %s" % DAT)
        print("  (si aún no aplicaste la fusión, el archivo está en "
              "~/minecraft/world/players/data/)")
        return 1
    _nombre, root, _gz = _n.load(DAT)
    ender = _n.cget(root.v, "EnderItems")
    inv = _n.cget(root.v, "Inventory")
    fuentes = list(inv.v.items) if inv is not None else []
    fuentes += list(ender.v.items) if ender is not None else []
    if not fuentes:
        print("El .dat no tiene nada que rescatar.")
        return 0

    print("=" * 72)
    print("  Rescate de las cosas de Nachardo para %s" % DESTINO)
    print("  origen: %s  (DataVersion %s)"
          % (DAT, (_n.cget(root.v, "DataVersion") or _n.Tag(3, "?")).v))
    print("  formato de encantamientos: %s" % ("1.20.5–1.21.4 (levels)" if LEVELS
                                               else "1.21.5+ (mapa directo)"))
    print("=" * 72)
    print("\n%s TIENE QUE ESTAR CONECTADO. Pega los comandos en la consola del" % DESTINO)
    print("panel (o en el servidor sin la barra inicial).\n")

    simples, shulkers = [], []
    for it in fuentes:
        d = {k.decode(): t for k, t in it}
        if txt(d["id"].v).endswith("shulker_box"):
            shulkers.append(it)
        else:
            simples.append(it)

    print("── 1. Items sueltos " + "─" * 50)
    for it in simples:
        iid, corch, cnt = item_suelto(it)
        print("/give %s %s%s %d" % (DESTINO, iid, corch, cnt))

    for k, sh in enumerate(shulkers, 1):
        d = {k2.decode(): t for k2, t in sh}
        iid = con_espacio(d["id"].v)
        dentro = []
        tag = d.get("tag")
        bet = _n.cget(tag.v, "BlockEntityTag") if tag is not None else None
        items = _n.cget(bet.v, "Items") if bet is not None else None
        if items is not None:
            for x in items.v.items:
                dx = {k2.decode(): t for k2, t in x}
                pila, _i, _c = item_pila(x)
                dentro.append("{slot:%d,item:%s}" % (int(dx["Slot"].v), pila))
        print("\n── 2. Shulker box %d (%d cosas dentro) %s"
              % (k, len(dentro), "─" * 34))
        print("/give %s %s[minecraft:container=[%s]] 1"
              % (DESTINO, iid, ",".join(dentro)))

        print("\n── 3. Por si el comando de arriba falla: los %d items uno a uno %s"
              % (len(dentro), "─" * 8))
        if items is not None:
            for x in items.v.items:
                xid, corch, cnt = item_suelto(x)
                print("/give %s %s%s %d" % (DESTINO, xid, corch, cnt))

    print("\nConsejo: prueba primero con uno corto (el tótem). Si la consola se")
    print("queja del formato de encantamientos, vuelve a correr esto con --levels.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
