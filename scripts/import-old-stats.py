#!/usr/bin/env python3
"""Unified old-world stats importer for Serve Actual.

- Imports old-server stats (from ~/oldstats) for the 17 survivors only
- JEYtheFlash = current + old JEYtheFlash + JJReborn + JJ (summed)
- Sums old+new for anyone who already played the new server (never overwrites)
- Archives (not deletes) every non-survivor stats file to keep things clean
- Trims the whitelist to exactly the survivors and reloads it live

Dry run by default:   python3 import-old-stats.py
Apply for real:       python3 import-old-stats.py --apply   (be logged OUT of the game)
"""
import hashlib, json, shutil, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from server import Rcon, read_properties, MC_DIR  # reuse panel plumbing

OLD = Path.home() / "oldstats"
LIVE = MC_DIR / "world/players/stats"
ARCHIVE = MC_DIR / "stats-archive"
APPLY = "--apply" in sys.argv

SURVIVORS = ["Abdullah", "HansPeterJunior", "Chumu19", "Daheezy", "DiosAriza2003",
             "Franccie", "Jakobino155", "JEYtheFlash", "josemori67", "kkrenalga0228",
             "KonataIzu", "LordVargas17", "mrrrt", "Nachardo", "RoBobinho",
             "sofidiaz", "Tazzk93"]
ALIASES = {"JJ": "JEYtheFlash", "JJReborn": "JEYtheFlash"}   # old names -> merge target

def offline_uuid(name):
    h = bytearray(hashlib.md5(("OfflinePlayer:" + name).encode()).digest())
    h[6] = (h[6] & 0x0F) | 0x30
    h[8] = (h[8] & 0x3F) | 0x80
    x = h.hex()
    return f"{x[0:8]}-{x[8:12]}-{x[12:16]}-{x[16:20]}-{x[20:32]}"

def add(dst, src):
    for k, v in src.items():
        if isinstance(v, dict):
            add(dst.setdefault(k, {}), v)
        elif isinstance(v, (int, float)):
            dst[k] = dst.get(k, 0) + v

def hours(j):
    c = j.get("stats", {}).get("minecraft:custom", {})
    t = c.get("minecraft:play_time", c.get("minecraft:play_one_minute", 0))
    return round(t / 72000, 1)

# --- identity maps ---------------------------------------------------------
online = {}
for src in ("whitelist.json", "usercache.json"):
    try:
        for e in json.loads((MC_DIR / src).read_text()):
            online.setdefault(e["name"], e["uuid"].lower())
    except Exception:
        pass

missing = [n for n in SURVIVORS if n not in online]
if missing:
    print(f"WARNING: no online identity known yet for: {', '.join(missing)}")
    print("         (they must be whitelisted/have joined once; their import is skipped this run)\n")

uuid_to_target = {}
for name in SURVIVORS:
    if name in online:
        uuid_to_target[online[name]] = name                # old server online-mode era
        uuid_to_target[offline_uuid(name)] = name          # old server offline-mode era
for old_name, target in ALIASES.items():
    if target in online:
        uuid_to_target[offline_uuid(old_name)] = target

# --- pass 1: import & merge ------------------------------------------------
if not OLD.is_dir():
    sys.exit(f"ERROR: {OLD} not found — scp the old world's stats folder there first")

consumed_dir = OLD / "consumed"
touched = {}
print("== IMPORT PLAN (old file -> survivor) ==")
for f in sorted(OLD.glob("*.json")):
    u = f.stem.lower()
    target = uuid_to_target.get(u)
    if not target:
        print(f"   skip   {f.name}  (not one of the 17 / unknown identity)")
        continue
    j = json.loads(f.read_text())
    print(f"   merge  {f.name}  -> {target}  (+{hours(j)}h)")
    touched.setdefault(target, []).append((f, j))

print("\n== RESULTING TOTALS ==")
for target, items in sorted(touched.items()):
    tgt_file = LIVE / (online[target] + ".json")
    cur = json.loads(tgt_file.read_text()) if tgt_file.exists() else {"stats": {}, "DataVersion": 4903}
    before = hours(cur)
    for _, j in items:
        add(cur["stats"], j.get("stats", {}))
    print(f"   {target}: {before}h now  ->  {hours(cur)}h after merge")
    if APPLY:
        if tgt_file.exists():
            shutil.copy2(tgt_file, str(tgt_file) + ".pre-import")
        tgt_file.write_text(json.dumps(cur))
        consumed_dir.mkdir(exist_ok=True)
        for f, _ in items:
            f.rename(consumed_dir / f.name)

# --- pass 2: archive non-survivor stats on the live server -----------------
keep_uuids = {online[n] for n in SURVIVORS if n in online}
print("\n== CLEANUP (live stats not belonging to the 17) ==")
extra = [f for f in LIVE.glob("*.json") if f.stem.lower() not in keep_uuids]
if not extra:
    print("   nothing to clean")
for f in extra:
    print(f"   archive {f.name}")
    if APPLY:
        ARCHIVE.mkdir(exist_ok=True)
        f.rename(ARCHIVE / f.name)

# --- pass 3: trim whitelist to survivors -----------------------------------
wl_file = MC_DIR / "whitelist.json"
wl = json.loads(wl_file.read_text()) if wl_file.exists() else []
keep, drop = [], []
for e in wl:
    (keep if e["name"] in SURVIVORS else drop).append(e)
print("\n== WHITELIST TRIM ==")
print(f"   keeping {len(keep)}: " + ", ".join(sorted(e['name'] for e in keep)))
print(f"   removing {len(drop)}: " + (", ".join(sorted(e['name'] for e in drop)) or "none"))
if APPLY:
    shutil.copy2(wl_file, str(wl_file) + ".pre-trim")
    wl_file.write_text(json.dumps(keep, indent=2))
    try:
        p = read_properties()
        r = Rcon("127.0.0.1", int(p.get("rcon.port", 25575)), p.get("rcon.password", ""))
        print("   server says: " + r.command("whitelist reload"))
    except Exception as e:
        print(f"   (couldn't reload live: {e} — it applies at next restart)")

print("\nDONE — " + ("changes APPLIED. Removed players now show up in Join Requests when they knock."
                     if APPLY else "dry run only, nothing changed. Re-run with --apply when happy."))
