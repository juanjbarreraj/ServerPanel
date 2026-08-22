#!/usr/bin/env python3
"""Serve Actual Panel — self-hosted control panel for Juan's Minecraft server.

Single-file Flask backend. Auth: scrypt-hashed passwords, signed session cookies,
roles (admin/mod/viewer) + per-user permission toggles. Talks to the server via
RCON (localhost), log tailing, stat files, and systemctl (sudo rule for restart).
"""
import base64, hashlib, hmac, json, os, re, secrets, socket, struct, subprocess, threading, time
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory, abort

# ------------------------------------------------------------------ config
PANEL_DIR = Path(os.environ.get("PANEL_DIR", Path(__file__).resolve().parent))
MC_DIR    = Path(os.environ.get("MC_DIR", "/home/ubuntu/minecraft"))
DATA_DIR  = PANEL_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
USERS_F   = DATA_DIR / "users.json"
SECRET_F  = DATA_DIR / "secret.key"
AUDIT_F   = DATA_DIR / "audit.log"
SERVICE   = os.environ.get("MC_SERVICE", "minecraft")
SESSION_HOURS = 24 * 7

if not SECRET_F.exists():
    SECRET_F.write_bytes(secrets.token_bytes(32))
    os.chmod(SECRET_F, 0o600)
SECRET = SECRET_F.read_bytes()

app = Flask(__name__, static_folder=None)

# roles -> default permission toggles (admin implicitly has all)
DEFAULT_PERMS = {
    "mod":    {"view_dashboard": True, "view_players": True, "view_console": False,
               "whitelist": True, "ban": True, "kick": True, "restart": False,
               "backups": False, "say": True},
    "viewer": {"view_dashboard": True, "view_players": True, "view_console": False,
               "whitelist": False, "ban": False, "kick": False, "restart": False,
               "backups": False, "say": False},
}
ALL_PERMS = ["view_dashboard", "view_players", "view_console", "whitelist",
             "ban", "kick", "restart", "backups", "say", "player_actions",
             "memories", "memories_upload"]
DEFAULT_PERMS["mod"]["player_actions"] = True
DEFAULT_PERMS["viewer"]["player_actions"] = False
DEFAULT_PERMS["mod"]["memories"] = True         # ver memorias
DEFAULT_PERMS["mod"]["memories_upload"] = True  # subir fotos
DEFAULT_PERMS["viewer"]["memories"] = True
DEFAULT_PERMS["viewer"]["memories_upload"] = False
# vista pública (entra con el PIN compartido): solo mirar + subir a memorias
DEFAULT_PERMS["public"] = {"view_dashboard": True, "view_players": True,
                           "memories": True, "memories_upload": True}

# ------------------------------------------------------------------ helpers
def hash_pw(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()

def check_pw(pw: str, stored: str) -> bool:
    try:
        salt_b64, h_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except Exception:
        return False

_users_lock = threading.Lock()
def load_users() -> dict:
    if not USERS_F.exists():
        return {}
    return json.loads(USERS_F.read_text())

def save_users(users: dict):
    with _users_lock:
        tmp = USERS_F.with_suffix(".tmp")
        tmp.write_text(json.dumps(users, indent=2))
        os.chmod(tmp, 0o600)
        tmp.replace(USERS_F)

def audit(user: str, action: str):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {user}: {action}\n"
    with open(AUDIT_F, "a") as f:
        f.write(line)

def sign(payload: str) -> str:
    mac = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + mac

def unsign(token: str):
    try:
        payload, mac = token.rsplit(".", 1)
        good = hmac.new(SECRET, payload.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(mac, good):
            return payload
    except Exception:
        pass
    return None

def current_user():
    tok = request.cookies.get("panel_session")
    if not tok:
        return None
    payload = unsign(tok)
    if not payload:
        return None
    try:
        name, expires = payload.split("|")
        if time.time() > float(expires):
            return None
    except Exception:
        return None
    if name == "__public__":
        return {"name": "invitado", "role": "public", "perms": {}, "must_change": False}
    users = load_users()
    u = users.get(name)
    if not u:
        return None
    return {"name": name, **u}

def has_perm(u, perm: str) -> bool:
    if u is None:
        return False
    if u.get("role") == "admin":
        return True
    perms = u.get("perms", {})
    if perm not in perms:  # new permissions inherit role defaults
        return bool(DEFAULT_PERMS.get(u.get("role", "viewer"), {}).get(perm))
    return bool(perms.get(perm))

def require(perm=None):
    u = current_user()
    if u is None:
        abort(401)
    if perm and not has_perm(u, perm):
        abort(403)
    return u

# login rate limiting: ip -> [timestamps]
_attempts = {}
def rate_limited(ip: str) -> bool:
    now = time.time()
    lst = [t for t in _attempts.get(ip, []) if now - t < 300]
    _attempts[ip] = lst
    return len(lst) >= 8

# ------------------------------------------------------------------ rcon
class Rcon:
    def __init__(self, host, port, password, timeout=4.0):
        self.host, self.port, self.password, self.timeout = host, port, password, timeout

    def _pkt(self, req_id, ptype, body):
        data = struct.pack("<ii", req_id, ptype) + body.encode("utf-8") + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    def command(self, cmd: str) -> str:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as s:
            s.settimeout(self.timeout)
            s.sendall(self._pkt(1, 3, self.password))
            self._read(s)  # auth response
            s.sendall(self._pkt(2, 2, cmd))
            resp = self._read(s)
            return resp

    def _read(self, s) -> str:
        raw = b""
        while len(raw) < 4:
            chunk = s.recv(4 - len(raw))
            if not chunk:
                raise ConnectionError("rcon closed")
            raw += chunk
        (length,) = struct.unpack("<i", raw)
        body = b""
        while len(body) < length:
            chunk = s.recv(length - len(body))
            if not chunk:
                break
            body += chunk
        req_id, ptype = struct.unpack("<ii", body[:8])
        if req_id == -1:
            raise PermissionError("rcon auth failed")
        return body[8:-2].decode("utf-8", "replace")

def read_properties() -> dict:
    props = {}
    try:
        for line in (MC_DIR / "server.properties").read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                props[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return props

def rcon() -> Rcon:
    p = read_properties()
    return Rcon("127.0.0.1", int(p.get("rcon.port", 25575)), p.get("rcon.password", ""))

def rcon_try(cmd: str):
    try:
        return True, rcon().command(cmd)
    except Exception as e:
        return False, f"(server not reachable: {e})"

# ------------------------------------------------------------------ server info
def service_state() -> str:
    try:
        out = subprocess.run(["systemctl", "is-active", SERVICE],
                             capture_output=True, text=True, timeout=5).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"

def proc_metrics() -> dict:
    # memory of java process + system cpu
    mem_used_mb = None
    try:
        pids = subprocess.run(["pgrep", "-f", "server.jar"], capture_output=True, text=True).stdout.split()
        if pids:
            rss = 0
            for pid in pids:
                with open(f"/proc/{pid}/status") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            rss += int(line.split()[1])
            mem_used_mb = rss // 1024
    except Exception:
        pass
    total_mb = free_mb = None
    try:
        info = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            info[k] = int(v.strip().split()[0])
        total_mb = info["MemTotal"] // 1024
        free_mb = info.get("MemAvailable", 0) // 1024
    except Exception:
        pass
    return {"mc_mem_mb": mem_used_mb, "sys_total_mb": total_mb, "sys_free_mb": free_mb,
            "load": os.getloadavg()[0] if hasattr(os, "getloadavg") else None}

_last_tick = {"t": 0, "val": None}
def tick_ms():
    # cache /tick query for 10s to avoid spamming
    if time.time() - _last_tick["t"] < 10:
        return _last_tick["val"]
    ok, out = rcon_try("tick query")
    val = None
    if ok:
        m = re.search(r"(\d+[.,]\d+)ms", out.replace("§", ""))
        if m:
            val = float(m.group(1).replace(",", "."))
    _last_tick.update(t=time.time(), val=val)
    return val

def online_players():
    ok, out = rcon_try("list")
    if not ok:
        return None
    m = re.search(r"players online:?\s*(.*)$", out.strip(), re.IGNORECASE | re.DOTALL)
    names = []
    if m and m.group(1).strip():
        names = [n.strip() for n in m.group(1).split(",") if n.strip()]
    return names

def usercache() -> dict:
    try:
        data = json.loads((MC_DIR / "usercache.json").read_text())
        return {e["uuid"]: e["name"] for e in data}
    except Exception:
        return {}

def whitelist() -> list:
    try:
        return json.loads((MC_DIR / "whitelist.json").read_text())
    except Exception:
        return []

def stats_dir() -> Path:
    for cand in (MC_DIR / "world/players/stats", MC_DIR / "world/stats"):
        if cand.is_dir():
            return cand
    return MC_DIR / "world/players/stats"

def player_stats():
    names = usercache()
    online = set(online_players() or [])
    out = []
    sdir = stats_dir()
    ddir = sdir.parent / "data"
    for f in sorted(sdir.glob("*.json")):
        uuid = f.stem
        try:
            st = json.loads(f.read_text()).get("stats", {}).get("minecraft:custom", {})
        except Exception:
            st = {}
        name = names.get(uuid, uuid[:8])
        dat = ddir / f"{uuid}.dat"
        last_seen = dat.stat().st_mtime if dat.exists() else f.stat().st_mtime
        out.append({
            "uuid": uuid, "name": name, "online": name in online,
            "play_hours": round(st.get("minecraft:play_time", 0) / 20 / 3600, 1),
            "deaths": st.get("minecraft:deaths", 0),
            "mob_kills": st.get("minecraft:mob_kills", 0),
            "player_kills": st.get("minecraft:player_kills", 0),
            "walked_km": round(st.get("minecraft:walk_one_cm", 0) / 100000, 1),
            "jumps": st.get("minecraft:jump", 0),
            "damage_taken": round(st.get("minecraft:damage_taken", 0) / 10, 0),
            "damage_dealt": round(st.get("minecraft:damage_dealt", 0) / 10, 0),
            "last_seen": last_seen,
        })
    out.sort(key=lambda p: (-p["online"], -p["play_hours"]))
    return out

# ------------------------------------------------------------------ auth routes
@app.post("/api/login")
def api_login():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    if rate_limited(ip):
        return jsonify(error="Demasiados intentos — espera 5 minutos"), 429
    body = request.get_json(silent=True) or {}
    name = (body.get("username") or "").strip()
    pw = body.get("password") or ""
    users = load_users()
    u = users.get(name)
    if not u or not check_pw(pw, u["hash"]):
        _attempts.setdefault(ip, []).append(time.time())
        audit(name or "?", f"FAILED login from {ip}")
        return jsonify(error="Usuario o contraseña incorrectos"), 401
    if u.get("totp"):
        tmp = sign(f"2fa|{name}|{time.time() + 180}")
        audit(name, f"password OK, esperando codigo 2FA ({ip})")
        return jsonify(need_2fa=True, token=tmp)
    expires = time.time() + SESSION_HOURS * 3600
    tok = sign(f"{name}|{expires}")
    resp = jsonify(ok=True, user={"name": name, "role": u["role"],
                                  "perms": u.get("perms", {}),
                                  "must_change": u.get("must_change", False)})
    resp.set_cookie("panel_session", tok, httponly=True, secure=True,
                    samesite="Strict", max_age=SESSION_HOURS * 3600)
    audit(name, f"logged in from {ip}")
    return resp

# ---------- TOTP (2FA) — implementación pura, RFC 6238 ----------
def _totp_code(secret_b32: str, counter: int) -> str:
    key = base64.b32decode(secret_b32)
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[19] & 0x0F
    num = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{num:06d}"

def _totp_ok(secret_b32: str, code: str) -> bool:
    try:
        code = re.sub(r"\D", "", code or "")[:6]
        now = int(time.time() // 30)
        return any(hmac.compare_digest(_totp_code(secret_b32, now + d), code)
                   for d in (-1, 0, 1))
    except Exception:
        return False

@app.post("/api/login2")
def api_login2():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    if rate_limited(ip):
        return jsonify(error="Demasiados intentos — espera 5 minutos"), 429
    body = request.get_json(silent=True) or {}
    payload = unsign(body.get("token") or "")
    if not payload or not payload.startswith("2fa|"):
        return jsonify(error="Sesión 2FA inválida — vuelve a ingresar"), 401
    _tag, name, exp = payload.split("|")
    if time.time() > float(exp):
        return jsonify(error="El código tardó demasiado — vuelve a ingresar"), 401
    users = load_users()
    u = users.get(name)
    if not u or not u.get("totp"):
        return jsonify(error="Sesión 2FA inválida"), 401
    if not _totp_ok(u["totp"], body.get("code")):
        _attempts.setdefault(ip, []).append(time.time())
        audit(name, f"FAILED 2FA code from {ip}")
        return jsonify(error="Código incorrecto"), 401
    expires = time.time() + SESSION_HOURS * 3600
    tok = sign(f"{name}|{expires}")
    resp = jsonify(ok=True, user={"name": name, "role": u["role"],
                                  "perms": u.get("perms", {}),
                                  "must_change": u.get("must_change", False)})
    resp.set_cookie("panel_session", tok, httponly=True, secure=True,
                    samesite="Strict", max_age=SESSION_HOURS * 3600)
    audit(name, f"logged in with 2FA from {ip}")
    return resp

# ---------- PIN público ----------
@app.post("/api/pin")
def api_pin():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "?")
    if rate_limited("pin:" + ip):
        return jsonify(error="rate", retry=300), 429
    body = request.get_json(silent=True) or {}
    pin = (body.get("pin") or "").strip()
    real = get_settings().get("public_pin")
    if not real:
        return jsonify(error="unset"), 503
    if not pin or not hmac.compare_digest(pin.lower(), real.lower()):
        _attempts.setdefault("pin:" + ip, []).append(time.time())
        audit("?", f"FAILED public PIN from {ip}")
        return jsonify(error="wrong"), 401
    expires = time.time() + 30 * 86400
    tok = sign(f"__public__|{expires}")
    resp = jsonify(ok=True, user={"name": "invitado", "role": "public",
                                  "perms": {}, "must_change": False})
    resp.set_cookie("panel_session", tok, httponly=True, secure=True,
                    samesite="Strict", max_age=30 * 86400)
    audit("public", f"PIN accepted from {ip}")
    return resp

@app.post("/api/logout")
def api_logout():
    resp = jsonify(ok=True)
    resp.delete_cookie("panel_session")
    return resp

@app.get("/api/me")
def api_me():
    u = current_user()
    if not u:
        return jsonify(user=None)
    return jsonify(user={"name": u["name"], "role": u["role"],
                         "perms": u.get("perms", {}),
                         "must_change": u.get("must_change", False)})

@app.post("/api/password")
def api_password():
    u = require()
    body = request.get_json(silent=True) or {}
    old, new = body.get("old") or "", body.get("new") or ""
    if len(new) < 8:
        return jsonify(error="La nueva contraseña debe tener al menos 8 caracteres"), 400
    users = load_users()
    if not check_pw(old, users[u["name"]]["hash"]):
        return jsonify(error="La contraseña actual no es correcta"), 403
    users[u["name"]]["hash"] = hash_pw(new)
    users[u["name"]]["must_change"] = False
    save_users(users)
    audit(u["name"], "changed their password")
    return jsonify(ok=True)

# ------------------------------------------------------------------ info routes
@app.get("/api/status")
def api_status():
    u = require("view_dashboard")
    props = read_properties()
    players = online_players()
    return jsonify({
        "state": service_state(),
        "reachable": players is not None,
        "online": players or [],
        "max_players": int(props.get("max-players", 0) or 0),
        "motd": props.get("motd", "").replace("\\n", " "),
        "version": "26.2",
        "view_distance": props.get("view-distance"),
        "simulation_distance": props.get("simulation-distance"),
        "difficulty": props.get("difficulty"),
        "tick_ms": tick_ms(),
        "metrics": proc_metrics(),
        "whitelist_count": len(whitelist()),
        "engine": engine_kind(),
        "time": time.time(),
    })

@app.get("/api/players")
def api_players():
    require("view_players")
    return jsonify(players=player_stats(), whitelist=whitelist())

@app.get("/api/audit")
def api_audit():
    u = require()
    if u["role"] != "admin":
        abort(403)
    lines = []
    if AUDIT_F.exists():
        lines = AUDIT_F.read_text().splitlines()[-200:]
    return jsonify(lines=lines)

# ------------------------------------------------------------------ action routes
def _csrf_ok():
    return request.headers.get("X-Panel", "") == "1"   # simple CSRF shield (custom header)

def do_cmd(u, perm, cmd, action_desc):
    if not _csrf_ok():
        abort(400)
    if not has_perm(u, perm):
        abort(403)
    ok, out = rcon_try(cmd)
    audit(u["name"], f"{action_desc} -> {out[:120]}")
    return jsonify(ok=ok, output=out)

SAFE_NAME = re.compile(r"^[A-Za-z0-9_]{3,16}$")

@app.post("/api/whitelist/add")
def api_wl_add():
    u = require(); body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not SAFE_NAME.match(name):
        return jsonify(error="Nombre de Minecraft inválido"), 400
    return do_cmd(u, "whitelist", f"whitelist add {name}", f"whitelisted {name}")

@app.post("/api/whitelist/remove")
def api_wl_remove():
    u = require(); body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not SAFE_NAME.match(name):
        return jsonify(error="Nombre de Minecraft inválido"), 400
    return do_cmd(u, "whitelist", f"whitelist remove {name}", f"removed {name} from whitelist")

@app.post("/api/ban")
def api_ban():
    u = require(); body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not SAFE_NAME.match(name):
        return jsonify(error="Nombre de Minecraft inválido"), 400
    return do_cmd(u, "ban", f"ban {name}", f"BANNED {name}")

@app.post("/api/pardon")
def api_pardon():
    u = require(); body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not SAFE_NAME.match(name):
        return jsonify(error="Nombre de Minecraft inválido"), 400
    return do_cmd(u, "ban", f"pardon {name}", f"unbanned {name}")

@app.post("/api/kick")
def api_kick():
    u = require(); body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not SAFE_NAME.match(name):
        return jsonify(error="Nombre de Minecraft inválido"), 400
    return do_cmd(u, "kick", f"kick {name}", f"kicked {name}")

@app.post("/api/say")
def api_say():
    u = require(); body = request.get_json(silent=True) or {}
    msg = (body.get("message") or "").strip()[:200]
    if not msg:
        return jsonify(error="El mensaje está vacío"), 400
    msg = msg.replace("\n", " ")
    return do_cmd(u, "say", f"say [{u['name']}] {msg}", f"said: {msg}")

@app.post("/api/restart")
def api_restart():
    u = require()
    if not _csrf_ok() or not has_perm(u, "restart"):
        abort(403)
    audit(u["name"], "RESTARTED the server")
    subprocess.Popen(["sudo", "-n", "systemctl", "restart", SERVICE])
    return jsonify(ok=True, output="Reiniciando…")

# ------------------------------------------------------------------ console
@app.get("/api/console/tail")
def api_console():
    require("view_console")
    log = MC_DIR / "logs/latest.log"
    try:
        with open(log, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 40_000))
            text = f.read().decode("utf-8", "replace")
        lines = text.splitlines()[-250:]
    except FileNotFoundError:
        lines = ["(no log yet)"]
    return jsonify(lines=lines)

@app.post("/api/console/send")
def api_console_send():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    cmd = (body.get("command") or "").strip()[:300]
    if not cmd or cmd.startswith("/"):
        cmd = cmd.lstrip("/")
    if not cmd:
        return jsonify(error="El comando está vacío"), 400
    ok, out = rcon_try(cmd)
    audit(u["name"], f"console: {cmd}")
    return jsonify(ok=ok, output=out)

# ------------------------------------------------------------------ backups
@app.get("/api/backups")
def api_backups():
    require("backups")
    bdir = MC_DIR / "backups"
    items = []
    if bdir.is_dir():
        for f in sorted(bdir.glob("world-*.tar.gz"), reverse=True):
            items.append({"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 1),
                          "mtime": f.stat().st_mtime})
    return jsonify(backups=items)

@app.post("/api/backups/now")
def api_backup_now():
    u = require("backups")
    if not _csrf_ok():
        abort(400)
    script = MC_DIR / "backup.sh"
    if not script.exists():
        return jsonify(error="backup.sh not found"), 500
    subprocess.Popen(["bash", str(script)])
    audit(u["name"], "started a manual backup")
    return jsonify(ok=True, output="Copia iniciada — aparece en la lista en ~1 minuto")

@app.get("/api/backups/download/<name>")
def api_backup_dl(name):
    require("backups")
    if not re.match(r"^world-[\w.-]+\.tar\.gz$", name):
        abort(400)
    return send_file(MC_DIR / "backups" / name, as_attachment=True)

# ------------------------------------------------------------------ inventory x-ray (admin only)
DATA_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

def _uuid_name(uuid):
    return usercache().get(uuid) or uuid[:8]

# ---- item detail extraction (nombre custom, lore, encantamientos, trim…) ----
_MC_NAMED_COLORS = {
    "black": "#000000", "dark_blue": "#0000AA", "dark_green": "#00AA00", "dark_aqua": "#00AAAA",
    "dark_red": "#AA0000", "dark_purple": "#AA00AA", "gold": "#FFAA00", "gray": "#AAAAAA",
    "dark_gray": "#555555", "blue": "#5555FF", "green": "#55FF55", "aqua": "#55FFFF",
    "red": "#FF5555", "light_purple": "#FF55FF", "yellow": "#FFFF55", "white": "#FFFFFF",
}
_SECT_CODE = re.compile("§([0-9a-fk-orx])", re.I)

def _pyify(t, v):
    """NBT payload -> plain python (para componentes de texto)."""
    import nbt as _n
    if t == _n.TAG_STRING:
        return v.decode("utf-8", "replace")
    if t == _n.TAG_LIST:
        return [_pyify(v.etype, i) for i in v.items]
    if t == _n.TAG_COMPOUND:
        return {k.decode("utf-8", "replace"): _pyify(tag.t, tag.v) for k, tag in v}
    return v

_CODE_HEX = {"0":"#000000","1":"#0000AA","2":"#00AA00","3":"#00AAAA","4":"#AA0000","5":"#AA00AA",
             "6":"#FFAA00","7":"#AAAAAA","8":"#555555","9":"#5555FF","a":"#55FF55","b":"#55FFFF",
             "c":"#FF5555","d":"#FF55FF","e":"#FFFF55","f":"#FFFFFF"}

def _legacy_segments(s, base):
    """'§eYELMO §cDE...' -> segmentos con estilos, fiel a los códigos legacy."""
    segs, cur, buf, i = [], dict(base), "", 0
    def flush():
        nonlocal buf
        if buf:
            segs.append({**cur, "t": buf})
            buf = ""
    while i < len(s):
        ch = s[i]
        if ch == "§" and i + 1 < len(s):
            code = s[i + 1].lower()
            i += 2
            flush()
            if code in _CODE_HEX:
                cur = dict(base)          # un código de color resetea estilos
                cur["c"] = _CODE_HEX[code]
            elif code == "l": cur["b"] = True
            elif code == "o": cur["i"] = True
            elif code == "n": cur["u"] = True
            elif code == "m": cur["s"] = True
            elif code == "r": cur = dict(base)
            continue
        buf += ch
        i += 1
    flush()
    return segs

def _seg_walk(x, inh, out, depth=0):
    """Componente de texto (str|dict|list) -> lista de segmentos {t,c,b,i,u,s} con herencia."""
    if x is None or depth > 12:
        return
    if isinstance(x, (int, float, bool)):
        out.append({**inh, "t": str(x)})
        return
    if isinstance(x, str):
        st = x.strip()
        if st[:1] in "{[":
            try:
                _seg_walk(json.loads(st), inh, out, depth + 1)
                return
            except Exception:
                pass
        if "§" in x:
            out.extend(_legacy_segments(x, inh))
        elif x:
            out.append({**inh, "t": x})
        return
    if isinstance(x, list):
        for i in x:
            _seg_walk(i, inh, out, depth + 1)
        return
    if isinstance(x, dict):
        st = dict(inh)
        c = x.get("color")
        if isinstance(c, str):
            st["c"] = c if c.startswith("#") else _MC_NAMED_COLORS.get(c, st.get("c"))
        for key, flag in (("bold", "b"), ("italic", "i"), ("underlined", "u"),
                          ("strikethrough", "s")):
            if key in x:
                v = x[key]
                st[flag] = (v != "false") if isinstance(v, str) else bool(v)
        t = x.get("text", "")
        if isinstance(t, str):
            if "§" in t:
                out.extend(_legacy_segments(t, st))
            elif t:
                out.append({**st, "t": t})
        else:
            _seg_walk(t, st, out, depth + 1)
        if not t and x.get("translate"):
            out.append({**st, "t": str(x["translate"]).split(".")[-1].replace("_", " ")})
        for e in (x.get("extra") or []):
            _seg_walk(e, st, out, depth + 1)
        return
    out.append({**inh, "t": str(x)})

def _segments(raw):
    """-> lista compacta de segmentos; [] si no hay texto."""
    out = []
    _seg_walk(raw, {}, out)
    segs = []
    for s in out:
        t = s.pop("t", "")
        if not t:
            continue
        seg = {"t": t[:200]}
        for k in ("c", "b", "i", "u", "s"):
            if s.get(k):
                seg[k] = s[k] if k == "c" else True
            elif k == "i" and s.get("i") is False:
                seg["i"] = False
        segs.append(seg)
        if len(segs) >= 40:
            break
    return segs

def _text_component(raw):
    """Compat: -> (texto plano, primer color, italic|None)."""
    segs = _segments(raw)
    text = "".join(s["t"] for s in segs)
    color = next((s["c"] for s in segs if s.get("c")), None)
    italic = next((s["i"] for s in segs if "i" in s), None)
    return text, color, italic

def _ench_entries(ct):
    """componente enchantments/stored_enchantments -> [(id_corto, nivel)] en cualquier formato."""
    import nbt as _n
    out = []
    payload = ct.v
    if isinstance(payload, _n.NList):            # formato viejo: lista de {id, lvl}
        for item in payload.items:
            eid, lvl = None, 1
            for k, tg in item:
                if k == b"id" and tg.t == _n.TAG_STRING:
                    eid = tg.v.decode().replace("minecraft:", "")
                elif k in (b"lvl", b"level"):
                    lvl = int(tg.v)
            if eid:
                out.append((eid, lvl))
        return out
    entries = payload                             # compound: {levels:{...}} o mapa directo
    for k, tg in payload:
        if k == b"levels" and tg.t == _n.TAG_COMPOUND:
            entries = tg.v
            break
    for k, tg in entries:
        key = k.decode()
        if key in ("show_in_tooltip", "levels"):
            continue
        if tg.t in (_n.TAG_BYTE, _n.TAG_SHORT, _n.TAG_INT):
            out.append((key.replace("minecraft:", ""), int(tg.v)))
    return out

def _item_dict(comp_items):
    """comp_items = payload list of an item compound -> friendly dict."""
    import nbt as _n
    d = {"id": "", "count": 1, "slot": None, "enchanted": False, "inside": None, "damage": None,
         "name": None, "name_color": None, "name_italic": None, "name_seg": None, "ench": [],
         "trim": None, "lore": [], "unbreakable": False, "rarity": None, "max_damage": None}
    custom_name = item_name = None
    custom_seg = item_seg = None
    for name, tag in comp_items:
        key = name.decode()
        if key == "id":
            d["id"] = tag.v.decode().replace("minecraft:", "")
        elif key in ("count", "Count"):
            d["count"] = tag.v
        elif key == "Slot":
            d["slot"] = tag.v
        elif key == "components":
            for cn, ct in tag.v:
                ck = cn.decode()
                if ck in ("minecraft:enchantments", "minecraft:stored_enchantments"):
                    ent = _ench_entries(ct)
                    if ent:
                        d["enchanted"] = True
                        d["ench"] += [{"id": e, "lvl": l} for e, l in ent]
                elif ck == "minecraft:custom_name":
                    raw = _pyify(ct.t, ct.v)
                    custom_name = _text_component(raw)
                    custom_seg = _segments(raw)
                elif ck == "minecraft:item_name":
                    raw = _pyify(ct.t, ct.v)
                    item_name = _text_component(raw)
                    item_seg = _segments(raw)
                elif ck == "minecraft:lore":
                    try:
                        for entry in _pyify(ct.t, ct.v)[:16]:
                            segs = _segments(entry)
                            if segs:
                                d["lore"].append({"seg": segs})
                    except Exception:
                        pass
                elif ck == "minecraft:trim":
                    mat = pat = None
                    for k2, tg2 in ct.v:
                        if k2 == b"material":
                            mat = tg2.v.decode().replace("minecraft:", "") if tg2.t == _n.TAG_STRING else "custom"
                        elif k2 == b"pattern":
                            pat = tg2.v.decode().replace("minecraft:", "") if tg2.t == _n.TAG_STRING else "custom"
                    if mat or pat:
                        d["trim"] = {"pattern": pat, "material": mat}
                elif ck == "minecraft:base_color" and ct.t == _n.TAG_STRING:
                    d["shield_base"] = ct.v.decode().replace("minecraft:", "")
                elif ck == "minecraft:banner_patterns":
                    pats = []
                    try:
                        for entry in ct.v.items:
                            pat = col = None
                            for k2, tg2 in entry:
                                if k2 == b"pattern" and tg2.t == _n.TAG_STRING:
                                    pat = tg2.v.decode().replace("minecraft:", "")
                                elif k2 == b"color" and tg2.t == _n.TAG_STRING:
                                    col = tg2.v.decode()
                            if pat and col:
                                pats.append([pat, col])
                    except Exception:
                        pass
                    if pats:
                        d["banner_pats"] = pats[:8]
                elif ck == "minecraft:unbreakable":
                    d["unbreakable"] = True
                elif ck == "minecraft:rarity" and ct.t == _n.TAG_STRING:
                    d["rarity"] = ct.v.decode()
                elif ck == "minecraft:max_damage":
                    d["max_damage"] = ct.v
                elif ck == "minecraft:damage":
                    d["damage"] = ct.v
                elif ck == "minecraft:container":
                    inner = []
                    for entry in ct.v.items:  # list of {slot, item}
                        slot_v, item_d = None, None
                        for en, et in entry:
                            if en == b"slot":
                                slot_v = et.v
                            elif en == b"item":
                                item_d = _item_dict(et.v)
                        if item_d is not None:
                            item_d["slot"] = slot_v
                            inner.append(item_d)
                    d["inside"] = inner
                elif ck == "minecraft:bundle_contents":
                    d["inside"] = [_item_dict(it) for it in ct.v.items]
    named = custom_name or item_name
    if named:
        d["name"] = named[0][:120] or None
        d["name_color"] = named[1]
        d["name_italic"] = named[2] if named[2] is not None else (custom_name is not None)
        d["name_seg"] = custom_seg or item_seg
    return d

def _load_player(uuid):
    import nbt as _n
    path = MC_DIR / "world/players/data" / f"{uuid}.dat"
    if not path.exists():
        return None, None
    name, root, gz = _n.load(path)
    return path, root

@app.get("/api/player/<uuid>/gear")
def api_player_gear(uuid):
    u = require()
    if u["role"] != "admin":
        abort(403)
    uuid = uuid.lower()
    if not DATA_UUID.match(uuid):
        abort(400)
    import nbt as _n
    path, root = _load_player(uuid)
    if root is None:
        return jsonify(error="No hay datos de este jugador"), 404
    c = root.v
    def num(key, default=0):
        t = _n.cget(c, key)
        return t.v if t else default
    inv = _n.cget(c, "Inventory")
    ender = _n.cget(c, "EnderItems")
    equipment = {}
    eq = _n.cget(c, "equipment")
    if eq:
        for en, et in eq.v:
            equipment[en.decode()] = _item_dict(et.v)
    else:  # older format: armor in slots 100-103, offhand -106
        pass
    pname = _uuid_name(uuid)
    online = pname in (online_players() or [])
    audit(u["name"], f"viewed inventory of {pname}")
    return jsonify({
        "uuid": uuid, "name": pname, "online": online,
        "health": round(float(num("Health", 0)), 1),
        "food": num("foodLevel", 0),
        "xp_level": num("XpLevel", 0),
        "inventory": [_item_dict(it) for it in inv.v.items] if inv else [],
        "ender": [_item_dict(it) for it in ender.v.items] if ender else [],
        "equipment": equipment,
    })

def _slot_command_name(slot, where):
    if where == "ender":
        return f"enderchest.{slot}"
    if 0 <= slot <= 8:
        return f"hotbar.{slot}"
    if 9 <= slot <= 35:
        return f"inventory.{slot-9}"
    if slot == 100: return "armor.feet"
    if slot == 101: return "armor.legs"
    if slot == 102: return "armor.chest"
    if slot == 103: return "armor.head"
    if slot == -106: return "weapon.offhand"
    return None

@app.post("/api/player/<uuid>/remove_item")
def api_player_remove(uuid):
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    uuid = uuid.lower()
    if not DATA_UUID.match(uuid):
        abort(400)
    body = request.get_json(silent=True) or {}
    where = body.get("where")          # 'inv' | 'ender' | 'equipment'
    slot = body.get("slot")            # int for inv/ender; str key for equipment
    nested = body.get("nested")        # index inside shulker/bundle, or None
    pname = _uuid_name(uuid)
    online = pname in (online_players() or [])
    if online:
        if nested is not None or where == "equipment":
            return jsonify(error=f"{pname} está conectado — para editar dentro de cajas o armadura debe salir primero"), 409
        cmd_slot = _slot_command_name(int(slot), "ender" if where == "ender" else "inv")
        if not cmd_slot:
            return jsonify(error="Slot inválido"), 400
        ok, out = rcon_try(f"item replace entity {pname} {cmd_slot} with minecraft:air")
        audit(u["name"], f"removed item (online) from {pname} {cmd_slot} -> {out[:80]}")
        return jsonify(ok=ok, output=out)
    # offline: NBT surgery with backup
    import nbt as _n
    path = MC_DIR / "world/players/data" / f"{uuid}.dat"
    if not path.exists():
        return jsonify(error="No hay archivo de este jugador"), 404
    nm, root, gz = _n.load(path)
    c = root.v
    removed = False
    if where in ("inv", "ender"):
        lst = _n.cget(c, "Inventory" if where == "inv" else "EnderItems")
        if lst:
            for i, it in enumerate(lst.v.items):
                slot_t = _n.cget(it, "Slot")
                if slot_t is not None and slot_t.v == int(slot):
                    if nested is None:
                        del lst.v.items[i]
                        removed = True
                    else:
                        comps = _n.cget(it, "components")
                        if comps:
                            for cn, ct in comps.v:
                                if cn in (b"minecraft:container",):
                                    for j, entry in enumerate(ct.v.items):
                                        sv = None
                                        for en, et in entry:
                                            if en == b"slot": sv = et.v
                                        if sv == int(nested):
                                            del ct.v.items[j]; removed = True; break
                                elif cn == b"minecraft:bundle_contents":
                                    if 0 <= int(nested) < len(ct.v.items):
                                        del ct.v.items[int(nested)]; removed = True
                    break
    elif where == "equipment":
        eq = _n.cget(c, "equipment")
        if eq and _n.cdel(eq.v, str(slot)):
            removed = True
    if not removed:
        return jsonify(error="No se encontró ese ítem (¿cambió algo?)"), 404
    shutil_backup = str(path) + f".bak-{int(time.time())}"
    import shutil as _sh
    _sh.copy2(path, shutil_backup)
    _n.save(path, nm, root, gz=True)
    _n.load(path)  # verify it still parses
    audit(u["name"], f"removed item (offline) from {pname} {where}[{slot}]" + (f" inside[{nested}]" if nested is not None else ""))
    return jsonify(ok=True, output=f"Ítem eliminado de {pname}")

@app.post("/api/player/action")
def api_player_action():
    u = require("player_actions")
    if not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    act = body.get("act")
    if not SAFE_NAME.match(name) or act not in ("heal", "feed", "kill"):
        return jsonify(error="Petición inválida"), 400
    if name not in (online_players() or []):
        return jsonify(error=f"{name} no está conectado ahora"), 409
    cmds = {"heal": f"effect give {name} minecraft:instant_health 1 10 true",
            "feed": f"effect give {name} minecraft:saturation 1 10 true",
            "kill": f"kill {name}"}
    ok, out = rcon_try(cmds[act])
    audit(u["name"], f"{act.upper()} on {name} -> {out[:80]}")
    return jsonify(ok=ok, output=out or "Hecho")

# ------------------------------------------------------------------ plugins (admin)
PLUGIN_DIR = MC_DIR / "plugins"

def engine_kind():
    if list(MC_DIR.glob("paper-*.jar")) or (MC_DIR / ".paper-engine").exists():
        return "paper"
    return "vanilla"

@app.get("/api/plugins")
def api_plugins():
    u = require()
    if u["role"] != "admin":
        abort(403)
    items = []
    if PLUGIN_DIR.is_dir():
        for f in sorted(PLUGIN_DIR.glob("*.jar")):
            items.append({"name": f.name, "size_mb": round(f.stat().st_size / 1e6, 2)})
    return jsonify(engine=engine_kind(), plugins=items)

@app.post("/api/plugins/upload")
def api_plugins_upload():
    u = require()
    if u["role"] != "admin":
        abort(403)
    if engine_kind() != "paper":
        return jsonify(error="El motor aún es vanilla — primero hay que migrar a Paper"), 400
    f = request.files.get("file")
    if not f or not f.filename.lower().endswith(".jar"):
        return jsonify(error="Sube un archivo .jar"), 400
    fname = re.sub(r"[^A-Za-z0-9._-]", "_", f.filename)[:80]
    PLUGIN_DIR.mkdir(exist_ok=True)
    dest = PLUGIN_DIR / fname
    f.save(dest)
    if dest.stat().st_size > 40_000_000:
        dest.unlink()
        return jsonify(error="Máximo 40 MB"), 400
    audit(u["name"], f"uploaded plugin {fname}")
    return jsonify(ok=True, output=f"{fname} subido — se activa al reiniciar")

@app.post("/api/plugins/delete")
def api_plugins_delete():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    name = re.sub(r"[^A-Za-z0-9._-]", "_", (body.get("name") or ""))
    src = PLUGIN_DIR / name
    if not src.exists():
        return jsonify(error="No existe ese plugin"), 404
    trash = PLUGIN_DIR / "_removed"
    trash.mkdir(exist_ok=True)
    src.rename(trash / f"{int(time.time())}-{name}")
    audit(u["name"], f"removed plugin {name}")
    return jsonify(ok=True, output=f"{name} quitado — se aplica al reiniciar")

# ------------------------------------------------------------------ item icons (self-hosted, extracted from the official client)
ICONS_DIR = PANEL_DIR / "icons"

# ítems cuya textura no se llama igual que el ítem
ICON_ALIAS = {
    "magma_block": "magma", "snow_block": "snow", "quartz_block": "quartz_block_side",
    "grass_block": "grass_block_side", "dirt_path": "dirt_path_top", "farmland": "farmland_moist",
    "glass_pane": "glass", "smooth_stone": "smooth_stone", "smooth_stone_slab": "smooth_stone",
    "smooth_sandstone": "sandstone_top", "smooth_red_sandstone": "red_sandstone_top",
    "smooth_quartz": "quartz_block_bottom", "dried_kelp_block": "dried_kelp_side",
    "tnt": "tnt_side", "crafting_table": "crafting_table_front", "furnace": "furnace_front",
    "smoker": "smoker_front", "blast_furnace": "blast_furnace_front", "loom": "loom_front",
    "cartography_table": "cartography_table_side3", "smithing_table": "smithing_table_front",
    "fletching_table": "fletching_table_front", "lodestone": "lodestone_top",
    "respawn_anchor": "respawn_anchor_top_off", "hay_block": "hay_block_side",
    "bone_block": "bone_block_side", "melon": "melon_side", "pumpkin": "pumpkin_side",
    "cake": "cake_side", "composter": "composter_side", "barrel": "barrel_side",
    "beehive": "beehive_front", "bee_nest": "bee_nest_front", "ancient_debris": "ancient_debris_side",
    "basalt": "basalt_side", "polished_basalt": "polished_basalt_side", "podzol": "podzol_side",
    "mycelium": "mycelium_side", "crimson_nylium": "crimson_nylium_side",
    "warped_nylium": "warped_nylium_side", "decorated_pot": "decorated_pot_side",
    "chiseled_bookshelf": "chiseled_bookshelf_empty", "sculk_sensor": "sculk_sensor_top",
    "calibrated_sculk_sensor": "calibrated_sculk_sensor_top", "jukebox": "jukebox_side",
    "note_block": "note_block", "piston": "piston_side", "sticky_piston": "piston_side",
    "dispenser": "dispenser_front", "dropper": "dropper_front", "observer": "observer_front",
    "daylight_detector": "daylight_detector_top", "enchanting_table": "enchanting_table_side",
    "end_portal_frame": "end_portal_frame_side", "spawner": "spawner", "beacon": "beacon",
}
_SUFFIX_STRIP = ("_slab", "_stairs", "_wall", "_fence_gate", "_fence", "_button",
                 "_pressure_plate", "_pane", "_carpet")

def _icon_candidates(iid):
    out, seen = [], set()
    def add(x):
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    def gen(x):
        add(x)
        if x in ICON_ALIAS:
            add(ICON_ALIAS[x])
        for suf in _SUFFIX_STRIP:
            if x.endswith(suf):
                b = x[: -len(suf)]
                for c in (b, b + "s", b + "_planks", b + "_wool", b + "_block", b + "_top", b + "_side"):
                    add(c)
                if b in ICON_ALIAS:
                    add(ICON_ALIAS[b])
                break
        if x.endswith("_block"):
            add(x[:-6])
        for suf in ("_top", "_side", "_front"):
            add(x + suf)
    gen(iid)
    if iid.startswith("waxed_"):
        gen(iid[6:])
    return out

_icon_cache = {}

@app.get("/icons/<path:iid>")
def api_icon(iid):
    iid = re.sub(r"[^a-z0-9_.]", "", iid.lower())
    if not iid.endswith(".png"):
        abort(404)
    base = iid[:-4]
    hit = _icon_cache.get(base)
    if hit is None:
        hit = ""
        r = ICONS_DIR / "render" / (base + ".png")   # render 3D del juego: prioridad
        if r.exists():
            hit = str(r)
        else:
            for cand in _icon_candidates(base):
                for sub in ("render", "item", "block", "misc"):
                    f = ICONS_DIR / sub / (cand + ".png")
                    if f.exists():
                        hit = str(f)
                        break
                if hit:
                    break
        _icon_cache[base] = hit
    if not hit:
        abort(404)
    return send_file(hit, max_age=604800)

# ---- escudo con estandarte real (compuesto al vuelo, cacheado) ----
_DYE_RGB = {"white": (249,255,254), "orange": (249,128,29), "magenta": (199,78,189),
    "light_blue": (58,179,218), "yellow": (254,216,61), "lime": (128,199,31),
    "pink": (243,139,170), "gray": (71,79,82), "light_gray": (157,157,151),
    "cyan": (22,156,156), "purple": (137,50,184), "blue": (60,68,170),
    "brown": (131,84,50), "green": (94,124,22), "red": (176,46,38), "black": (29,29,33)}
_SHIELD_QUAD = [(17,5),(45,11),(45,59),(17,53)]
_SHIELD_FRONT = (2,2,14,24)

def _persp_coeffs(dest, src):
    A, B = [], []
    for (X, Y), (x, y) in zip(dest, src):
        A.append([x,y,1,0,0,0,-X*x,-X*y]); B.append(X)
        A.append([0,0,0,x,y,1,-Y*x,-Y*y]); B.append(Y)
    M = [row[:] + [b] for row, b in zip(A, B)]
    for col in range(8):
        piv = max(range(col,8), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            return None
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        M[col] = [v/pv for v in M[col]]
        for r in range(8):
            if r != col and M[r][col]:
                f = M[r][col]
                M[r] = [a - f*b for a, b in zip(M[r], M[col])]
    return [M[i][8] for i in range(8)]

_BANNER_QUAD = [(18, 5), (44, 10), (44, 57), (18, 52)]
_BANNER_FRONT = (1, 1, 21, 41)

@app.get("/icons/banner/<spec>")
def api_icon_banner(spec):
    """banner con patrones: /icons/banner/red~stripe_center.white~cross.black.png"""
    spec = re.sub(r"[^a-z0-9_.~]", "", spec.lower())
    if not spec.endswith(".png"):
        abort(404)
    key = spec[:-4][:180]
    out = ICONS_DIR / "composited" / f"banner~{key}.png"
    if not out.exists():
        try:
            from PIL import Image
            parts = key.split("~")
            base_col = parts[0] if parts and parts[0] in _DYE_RGB else "white"
            base_tex = None
            for cand in ("entity/banner/base.png", "entity/banner_base.png"):
                f = ICONS_DIR / cand
                if f.exists():
                    base_tex = f
                    break
            if base_tex is None:
                abort(404)
            def tinted(png, col):
                p = Image.open(png).convert("RGBA").crop(_BANNER_FRONT).resize((20*8, 40*8), Image.NEAREST)
                r, g, b = _DYE_RGB.get(col, (255, 255, 255))
                pr, pg, pb, pa = p.split()
                return Image.merge("RGBA", (pr.point([i*r//255 for i in range(256)]),
                                            pg.point([i*g//255 for i in range(256)]),
                                            pb.point([i*b//255 for i in range(256)]), pa))
            front = tinted(base_tex, base_col)
            for pp in parts[1:]:
                if "." in pp:
                    pat, col = pp.split(".", 1)
                    pf = ICONS_DIR / "entity" / "banner" / f"{re.sub(r'[^a-z_]', '', pat)}.png"
                    if pf.exists():
                        front.alpha_composite(tinted(pf, col))
            cv = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            co = _persp_coeffs([(0,0),(front.width,0),(front.width,front.height),(0,front.height)], _BANNER_QUAD)
            if co is None:
                abort(404)
            cv.alpha_composite(front.transform((64, 64), Image.PERSPECTIVE, co, resample=Image.NEAREST))
            out.parent.mkdir(exist_ok=True)
            cv.save(out, "PNG")
        except Exception:
            abort(404)
    return send_file(out, max_age=604800)

@app.get("/icons/shield/<spec>")
def api_icon_shield(spec):
    spec = re.sub(r"[^a-z0-9_.~]", "", spec.lower())
    if not spec.endswith(".png"):
        abort(404)
    key = spec[:-4][:180]
    out = ICONS_DIR / "composited" / f"shield~{key}.png"
    if not out.exists():
        try:
            from PIL import Image
            parts = key.split("~")
            base_col = parts[0] if parts and parts[0] in _DYE_RGB else None
            names = ("shield_base.png", "shield/base.png", "shield/shield_base.png") if base_col \
                else ("shield_base_nopattern.png", "shield/base_nopattern.png", "shield/shield_base_nopattern.png")
            base_tex = None
            for nm in names:
                f = ICONS_DIR / "entity" / nm
                if f.exists():
                    base_tex = f
                    break
            if base_tex is None:
                abort(404)
            tex = Image.open(base_tex).convert("RGBA")
            front = tex.crop(_SHIELD_FRONT).resize((12*8, 22*8), Image.NEAREST)
            def tint_overlay(pat_name, col):
                pf = ICONS_DIR / "entity" / "shield" / f"{pat_name}.png"
                if not pf.exists() or col not in _DYE_RGB:
                    return
                p = Image.open(pf).convert("RGBA").crop(_SHIELD_FRONT).resize(front.size, Image.NEAREST)
                r, g, b = _DYE_RGB[col]
                pr, pg, pb, pa = p.split()
                lutr = [int(i*r/255) for i in range(256)]
                lutg = [int(i*g/255) for i in range(256)]
                lutb = [int(i*b/255) for i in range(256)]
                tp = Image.merge("RGBA", (pr.point(lutr), pg.point(lutg), pb.point(lutb), pa))
                front.alpha_composite(tp)
            if base_col:
                tint_overlay("base", base_col)
                for pp in parts[1:]:
                    if "." in pp:
                        pat, col = pp.split(".", 1)
                        tint_overlay(re.sub(r"[^a-z_]", "", pat), col)
            cv = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            co = _persp_coeffs([(0,0),(front.width,0),(front.width,front.height),(0,front.height)], _SHIELD_QUAD)
            if co is None:
                abort(404)
            cv.alpha_composite(front.transform((64, 64), Image.PERSPECTIVE, co, resample=Image.NEAREST))
            out.parent.mkdir(exist_ok=True)
            cv.save(out, "PNG")
        except Exception:
            abort(404)
    return send_file(out, max_age=604800)

# ---- ícono de armadura con su trim compuesto (textura base + overlay teñido) ----
_ARMOR_MAT = {"golden": "gold", "iron": "iron", "diamond": "diamond", "netherite": "netherite"}

@app.get("/icons/armor/<spec>")
def api_icon_armor(spec):
    spec = re.sub(r"[^a-z0-9_+.]", "", spec.lower())
    if not spec.endswith(".png") or "+" not in spec:
        abort(404)
    item_id, mat = spec[:-4].split("+", 1)
    mat = re.sub(r"[^a-z_]", "", mat)
    out = ICONS_DIR / "composited" / f"{item_id}+{mat}.png"
    if not out.exists():
        piece = next((p for s, p in (("_helmet", "helmet"), ("_chestplate", "chestplate"),
                                     ("_leggings", "leggings"), ("_boots", "boots"))
                      if item_id.endswith(s)), None)
        base_f = ICONS_DIR / "item" / f"{item_id}.png"
        trim_f = ICONS_DIR / "trims" / "items" / f"{piece}_trim.png" if piece else None
        # si el material del trim es igual al de la armadura, el juego usa la paleta _darker
        pal_name = mat
        if _ARMOR_MAT.get(item_id.split("_")[0]) == mat and \
                (ICONS_DIR / "trims" / "color_palettes" / f"{mat}_darker.png").exists():
            pal_name = f"{mat}_darker"
        pal_f = ICONS_DIR / "trims" / "color_palettes" / f"{pal_name}.png"
        if not (piece and base_f.exists() and trim_f.exists() and pal_f.exists()):
            abort(404)
        try:
            from PIL import Image
            base = Image.open(base_f).convert("RGBA")
            trim = Image.open(trim_f).convert("RGBA")
            pal = Image.open(pal_f).convert("RGBA")
            colors = [pal.getpixel((i, 0)) for i in range(pal.width)]
            if trim.size != base.size:
                trim = trim.resize(base.size, Image.NEAREST)
            grays = sorted({p[0] for p in trim.getdata() if p[3] > 8})
            n = max(1, len(grays))
            gmap = {g: colors[min(int(i * len(colors) / n), len(colors) - 1)]
                    for i, g in enumerate(grays)}
            px_t, px_b = trim.load(), base.load()
            for y in range(base.size[1]):
                for x in range(base.size[0]):
                    r, g, b, a = px_t[x, y]
                    if a > 8:
                        cr, cg, cb, _ca = gmap.get(r, (r, g, b, a))
                        px_b[x, y] = (cr, cg, cb, 255)
            out.parent.mkdir(exist_ok=True)
            base.save(out, "PNG")
        except Exception:
            abort(404)
    return send_file(out, max_age=604800)

# ------------------------------------------------------------------ branding
SETTINGS_F = DATA_DIR / "settings.json"

def get_settings():
    try:
        return json.loads(SETTINGS_F.read_text())
    except Exception:
        return {}

@app.get("/api/branding")
def api_branding():
    s = get_settings()
    return jsonify(name=s.get("name", "Serve Actual"))

@app.post("/api/settings/name")
def api_settings_name():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()[:40]
    if not name:
        return jsonify(error="El nombre no puede estar vacío"), 400
    s = get_settings()
    s["name"] = name
    SETTINGS_F.write_text(json.dumps(s))
    audit(u["name"], f"renamed panel to: {name}")
    return jsonify(ok=True)

# ------------------------------------------------------------------ settings (admin)
@app.post("/api/settings/motd")
def api_settings_motd():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    text = (body.get("motd") or "").strip()[:120]
    if not text:
        return jsonify(error="La descripción no puede estar vacía"), 400
    text = text.replace("\\", "").replace("\n", " ")
    stored = text.replace("|", "\\n")
    pf = MC_DIR / "server.properties"
    lines = pf.read_text().splitlines()
    out, done = [], False
    for ln in lines:
        if ln.startswith("motd="):
            out.append("motd=" + stored)
            done = True
        else:
            out.append(ln)
    if not done:
        out.append("motd=" + stored)
    pf.write_text("\n".join(out) + "\n")
    audit(u["name"], f"changed server description to: {text}")
    return jsonify(ok=True, note="Guardado — se ve al reiniciar el servidor")

# ------------------------------------------------------------------ join requests
JOINREQ_F = DATA_DIR / "join_requests.json"
JOINSTATE_F = DATA_DIR / "joinreq_state.json"
_join_lock = threading.Lock()
JOIN_PATTERNS = [
    re.compile(r"([A-Za-z0-9_]{2,16}) \(/[\d.:]+\) lost connection: You are not white-?listed"),
    re.compile(r"Disconnecting .*?name=([A-Za-z0-9_]{2,16}).*?You are not white-?listed"),
]

def _scan_join_attempts():
    with _join_lock:
        store = json.loads(JOINREQ_F.read_text()) if JOINREQ_F.exists() else {}
        state = json.loads(JOINSTATE_F.read_text()) if JOINSTATE_F.exists() else {"offset": 0, "inode": 0}
        log = MC_DIR / "logs/latest.log"
        try:
            st = log.stat()
        except FileNotFoundError:
            return store
        if st.st_ino != state.get("inode") or st.st_size < state.get("offset", 0):
            state = {"offset": 0, "inode": st.st_ino}
        with open(log, "r", errors="replace") as f:
            f.seek(state["offset"])
            chunk = f.read()
            state["offset"] = f.tell()
        state["inode"] = st.st_ino
        new_names = []
        for line in chunk.splitlines():
            if "not white" not in line:
                continue
            for pat in JOIN_PATTERNS:
                m = pat.search(line)
                if m:
                    new_names.append(m.group(1) or m.group(0))
                    break
        wl = {e["name"].lower() for e in whitelist()}
        now = time.time()
        for n in new_names:
            if n.lower() in wl:
                continue
            e = store.setdefault(n, {"count": 0, "first": now, "last": now, "status": "pending"})
            e["count"] += 1
            e["last"] = now
            if e["status"] == "dismissed":
                e["status"] = "pending"
        JOINREQ_F.write_text(json.dumps(store))
        JOINSTATE_F.write_text(json.dumps(state))
        return store

@app.get("/api/joinreq")
def api_joinreq():
    require("whitelist")
    store = _scan_join_attempts()
    items = [{"name": n, **v} for n, v in store.items() if v["status"] == "pending"]
    items.sort(key=lambda x: -x["last"])
    return jsonify(requests=items)

@app.post("/api/joinreq/accept")
def api_joinreq_accept():
    u = require()
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not SAFE_NAME.match(name):
        return jsonify(error="Nombre inválido"), 400
    resp = do_cmd(u, "whitelist", f"whitelist add {name}", f"ACCEPTED join request of {name}")
    with _join_lock:
        store = json.loads(JOINREQ_F.read_text()) if JOINREQ_F.exists() else {}
        if name in store:
            store[name]["status"] = "accepted"
            JOINREQ_F.write_text(json.dumps(store))
    return resp

@app.post("/api/joinreq/dismiss")
def api_joinreq_dismiss():
    u = require("whitelist")
    if not _csrf_ok():
        abort(400)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    with _join_lock:
        store = json.loads(JOINREQ_F.read_text()) if JOINREQ_F.exists() else {}
        if name in store:
            store[name]["status"] = "dismissed"
            JOINREQ_F.write_text(json.dumps(store))
    audit(u["name"], f"dismissed join request of {name}")
    return jsonify(ok=True)

# ------------------------------------------------------------------ user management (admin)
@app.get("/api/users")
def api_users():
    u = require()
    if u["role"] != "admin":
        abort(403)
    users = load_users()
    return jsonify(users=[{"name": n, "role": v["role"], "perms": v.get("perms", {}),
                           "must_change": v.get("must_change", False)}
                          for n, v in sorted(users.items())],
                   all_perms=ALL_PERMS)

@app.post("/api/users/create")
def api_users_create():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    pw = body.get("password") or ""
    role = body.get("role") or "viewer"
    if not re.match(r"^[A-Za-z0-9_.-]{2,24}$", name):
        return jsonify(error="Usuario inválido (letras/números, 2-24 caracteres)"), 400
    if name.lower() in ("__public__", "invitado", "public"):
        return jsonify(error="Ese nombre está reservado"), 400
    if len(pw) < 8:
        return jsonify(error="La contraseña temporal debe tener 8+ caracteres"), 400
    if role not in ("admin", "mod", "viewer"):
        return jsonify(error="Rol inválido"), 400
    users = load_users()
    if name in users:
        return jsonify(error="Ese usuario ya existe"), 400
    users[name] = {"hash": hash_pw(pw), "role": role,
                   "perms": dict(DEFAULT_PERMS.get(role, DEFAULT_PERMS["viewer"])),
                   "must_change": True}
    save_users(users)
    audit(u["name"], f"created user {name} ({role})")
    return jsonify(ok=True)

@app.post("/api/users/update")
def api_users_update():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    users = load_users()
    if name not in users:
        return jsonify(error="No existe ese usuario"), 404
    if name == u["name"] and body.get("role") and body["role"] != "admin":
        return jsonify(error="No puedes bajarte de rango a ti mismo"), 400
    if body.get("role") in ("admin", "mod", "viewer"):
        users[name]["role"] = body["role"]
    if isinstance(body.get("perms"), dict):
        users[name]["perms"] = {k: bool(v) for k, v in body["perms"].items() if k in ALL_PERMS}
    if body.get("password"):
        if len(body["password"]) < 8:
            return jsonify(error="La contraseña debe tener 8+ caracteres"), 400
        users[name]["hash"] = hash_pw(body["password"])
        users[name]["must_change"] = True
    save_users(users)
    audit(u["name"], f"updated user {name}")
    return jsonify(ok=True)

@app.post("/api/users/delete")
def api_users_delete():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    body = request.get_json(silent=True) or {}
    name = body.get("name")
    if name == u["name"]:
        return jsonify(error="No puedes eliminar tu propia cuenta"), 400
    users = load_users()
    if users.pop(name, None) is None:
        return jsonify(error="No existe ese usuario"), 404
    save_users(users)
    audit(u["name"], f"deleted user {name}")
    return jsonify(ok=True)

# ------------------------------------------------------------------ memorias del server
# v5: dos niveles — GLOBALES (memories/) e INDIVIDUALES (memories/players/<uuid>/).
# Ver: cualquiera con sesión (perm "memories"). Subir: perm "memories_upload"
# (incluye al público con PIN; sistema de honor en carpetas individuales).
# Borrar/editar: SOLO moderadores y admin. Descripción SIEMPRE obligatoria.
MEM_DIR = PANEL_DIR / "memories"
MEM_THUMBS = MEM_DIR / "thumbs"
MEM_META = MEM_DIR / "memories.json"
MEM_PLAYERS = MEM_DIR / "players"
_mem_lock = threading.Lock()
MEM_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

def _mem_paths(target):
    """target: None/'global' -> global; uuid -> carpeta del jugador. -> (dir, thumbs, meta)"""
    if not target or target == "global":
        return MEM_DIR, MEM_THUMBS, MEM_META
    d = MEM_PLAYERS / target
    return d, d / "thumbs", d / "meta.json"

def _mem_load(meta_f):
    try:
        return json.loads(meta_f.read_text())
    except Exception:
        return []

def _mem_save(meta_f, items):
    meta_f.parent.mkdir(parents=True, exist_ok=True)
    tmp = meta_f.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1))
    tmp.replace(MEM_META if meta_f == MEM_META else meta_f)

def _valid_player_target(target):
    if not UUID_RE.match(target or ""):
        return None
    for e in whitelist():
        if e.get("uuid", "").lower() == target:
            return e.get("name") or usercache().get(target) or target[:8]
    return None

@app.get("/api/memories")
def api_memories():
    u = require("memories")
    items = sorted(_mem_load(MEM_META), key=lambda m: -m.get("ts", 0))
    can_edit = u["role"] in ("mod", "admin")
    return jsonify(memories=items, can_delete_any=can_edit, me=u["name"],
                   can_upload=has_perm(u, "memories_upload"))

@app.get("/api/memories/players")
def api_memories_players():
    require("memories")
    out = []
    for e in sorted(whitelist(), key=lambda x: (x.get("name") or "").lower()):
        uid = (e.get("uuid") or "").lower()
        if not UUID_RE.match(uid):
            continue
        meta = _mem_load(MEM_PLAYERS / uid / "meta.json")
        cover = None
        if meta:
            newest = max(meta, key=lambda m: m.get("ts", 0))
            cover = newest.get("thumb") or newest.get("file")
        out.append({"uuid": uid, "name": e.get("name"), "count": len(meta), "cover": cover})
    return jsonify(players=out)

@app.get("/api/memories/player/<uuid>")
def api_memories_player(uuid):
    u = require("memories")
    uuid = uuid.lower()
    name = _valid_player_target(uuid)
    if not name:
        return jsonify(error="Ese jugador no está en la whitelist"), 404
    items = sorted(_mem_load(MEM_PLAYERS / uuid / "meta.json"), key=lambda m: -m.get("ts", 0))
    return jsonify(name=name, memories=items,
                   can_delete_any=u["role"] in ("mod", "admin"),
                   can_upload=has_perm(u, "memories_upload"))

@app.post("/api/memories/upload")
def api_memories_upload():
    u = require("memories_upload")
    if not _csrf_ok():
        abort(403)
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify(error="Sube una imagen"), 400
    ext = Path(f.filename).suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext not in MEM_EXT:
        return jsonify(error="Formato no válido — usa JPG, PNG, GIF o WebP (las HEIC de iPhone hay que convertirlas)"), 400
    title = (request.form.get("title") or "").strip()[:120]
    if len(title) < 3:
        return jsonify(error="La descripción es obligatoria (mínimo 3 letras)"), 400
    target = (request.form.get("target") or "global").strip().lower()
    if target != "global" and not _valid_player_target(target):
        return jsonify(error="Ese jugador no está en la whitelist"), 404
    mdir, mthumbs, mmeta = _mem_paths(target)
    mdir.mkdir(parents=True, exist_ok=True)
    mthumbs.mkdir(exist_ok=True)
    fname = f"mem-{int(time.time())}-{secrets.token_hex(3)}{ext}"
    dest = mdir / fname
    f.save(dest)
    if dest.stat().st_size > 15_000_000:
        dest.unlink()
        return jsonify(error="Máximo 15 MB por foto"), 400
    w = h = 0
    thumb = fname
    try:
        from PIL import Image
        im = Image.open(dest)
        im.load()
        w, h = im.size
        tt = im.convert("RGB")
        tt.thumbnail((640, 640))
        tname = Path(fname).stem + ".jpg"
        tt.save(mthumbs / tname, "JPEG", quality=80, optimize=True)
        thumb = tname
    except Exception:
        pass
    with _mem_lock:
        items = _mem_load(mmeta)
        items.append({"file": fname, "thumb": thumb, "title": title,
                      "uploader": u["name"], "ts": int(time.time() * 1000), "w": w, "h": h})
        _mem_save(mmeta, items)
    audit(u["name"], f"added memory photo {fname} ({title}) target={target}")
    return jsonify(ok=True, output="Foto guardada en las Memorias del server")

@app.post("/api/memories/delete")
def api_memories_delete():
    u = require("memories")
    if u["role"] not in ("mod", "admin") or not _csrf_ok():
        return jsonify(error="Solo los moderadores pueden borrar fotos"), 403
    body = request.get_json(silent=True) or {}
    fname = re.sub(r"[^A-Za-z0-9._-]", "", body.get("file") or "")
    target = (body.get("target") or "global").strip().lower()
    if target != "global" and not UUID_RE.match(target):
        return jsonify(error="Destino inválido"), 400
    mdir, mthumbs, mmeta = _mem_paths(target)
    with _mem_lock:
        items = _mem_load(mmeta)
        if not any(m["file"] == fname for m in items):
            return jsonify(error="Esa foto ya no existe"), 404
        items = [m for m in items if m["file"] != fname]
        _mem_save(mmeta, items)
    trash = MEM_DIR / "_removed"
    trash.mkdir(exist_ok=True)
    src = mdir / fname
    if src.exists():
        src.rename(trash / f"{int(time.time())}-{target}-{fname}")
    tb = mthumbs / (Path(fname).stem + ".jpg")
    if tb.exists():
        tb.unlink()
    audit(u["name"], f"removed memory photo {fname} target={target}")
    return jsonify(ok=True, output="Foto eliminada de las Memorias")

@app.post("/api/memories/edit")
def api_memories_edit():
    u = require("memories")
    if u["role"] not in ("mod", "admin") or not _csrf_ok():
        return jsonify(error="Solo los moderadores pueden editar descripciones"), 403
    body = request.get_json(silent=True) or {}
    fname = re.sub(r"[^A-Za-z0-9._-]", "", body.get("file") or "")
    title = (body.get("title") or "").strip()[:120]
    target = (body.get("target") or "global").strip().lower()
    if len(title) < 3:
        return jsonify(error="La descripción no puede quedar vacía"), 400
    if target != "global" and not UUID_RE.match(target):
        return jsonify(error="Destino inválido"), 400
    _d, _t, mmeta = _mem_paths(target)
    with _mem_lock:
        items = _mem_load(mmeta)
        hit = next((m for m in items if m["file"] == fname), None)
        if not hit:
            return jsonify(error="Esa foto ya no existe"), 404
        hit["title"] = title
        _mem_save(mmeta, items)
    audit(u["name"], f"renamed memory {fname} -> {title}")
    return jsonify(ok=True, output="Descripción actualizada")

@app.get("/memories/<path:p>")
def mem_file(p):
    require("memories")
    parts = [re.sub(r"[^A-Za-z0-9._-]", "", x) for x in p.split("/") if x]
    if parts and parts[0] == "p":                      # individuales: p/<uuid>/[thumbs/]<file>
        if len(parts) == 3 and UUID_RE.match(parts[1]):
            f = MEM_PLAYERS / parts[1] / parts[2]
        elif len(parts) == 4 and parts[2] == "thumbs" and UUID_RE.match(parts[1]):
            f = MEM_PLAYERS / parts[1] / "thumbs" / parts[3]
        else:
            abort(404)
    elif parts and parts[0] == "thumbs" and len(parts) == 2:
        f = MEM_THUMBS / parts[1]
    elif len(parts) == 1:
        f = MEM_DIR / parts[0]
    else:
        abort(404)
    if not f.exists():
        abort(404)
    return send_file(f, max_age=86400)

# ------------------------------------------------------------------ skins (viewer 3D + archivo propio)
SKINS_DIR = DATA_DIR / "skins"
SKINS_CUR = SKINS_DIR / "current"
SKINS_HIS = SKINS_DIR / "history"

def _fetch_skin_info(uuid):
    """Mojang session server -> (skin_url, slim). Lanza excepción si falla."""
    import urllib.request
    uid = uuid.replace("-", "")
    with urllib.request.urlopen(
            f"https://sessionserver.mojang.com/session/minecraft/profile/{uid}", timeout=8) as r:
        prof = json.loads(r.read())
    for prop in prof.get("properties", []):
        if prop.get("name") == "textures":
            tex = json.loads(base64.b64decode(prop["value"]))
            skin = tex.get("textures", {}).get("SKIN", {})
            url = skin.get("url")
            slim = skin.get("metadata", {}).get("model") == "slim"
            return url, slim
    return None, False

def _skin_refresh(uuid, force=False):
    """Baja/actualiza la skin actual (cache 1h). -> (png_path|None, slim)"""
    import urllib.request
    SKINS_CUR.mkdir(parents=True, exist_ok=True)
    png = SKINS_CUR / f"{uuid}.png"
    meta_f = SKINS_CUR / f"{uuid}.json"
    meta = {}
    try:
        meta = json.loads(meta_f.read_text())
    except Exception:
        pass
    if not force and png.exists() and time.time() - meta.get("fetched", 0) < 3600:
        return png, meta.get("slim", False)
    try:
        url, slim = _fetch_skin_info(uuid)
        if not url:
            raise ValueError("sin skin")
        with urllib.request.urlopen(url, timeout=8) as r:
            data = r.read()
        png.write_bytes(data)
        h = hashlib.sha1(data).hexdigest()[:10]
        meta = {"fetched": int(time.time()), "slim": slim, "hash": h, "url": url}
        meta_f.write_text(json.dumps(meta))
        # archivo histórico: guarda solo si la skin cambió
        hdir = SKINS_HIS / uuid
        hdir.mkdir(parents=True, exist_ok=True)
        idx_f = hdir / "index.json"
        try:
            idx = json.loads(idx_f.read_text())
        except Exception:
            idx = []
        if not any(e.get("hash") == h for e in idx):
            day = time.strftime("%Y-%m-%d")
            fn = f"{day}-{h}.png"
            (hdir / fn).write_bytes(data)
            idx.append({"date": day, "file": fn, "hash": h, "slim": slim})
            idx_f.write_text(json.dumps(idx))
        return png, slim
    except Exception:
        if png.exists():
            return png, meta.get("slim", False)
        return None, False

@app.get("/api/skin/<uuid>")
def api_skin(uuid):
    require("view_players")
    uuid = uuid.lower()
    if not UUID_RE.match(uuid):
        abort(400)
    png, _slim = _skin_refresh(uuid)
    if png is None:
        abort(404)
    return send_file(png, max_age=1800)

@app.get("/api/skininfo/<uuid>")
def api_skininfo(uuid):
    require("view_players")
    uuid = uuid.lower()
    if not UUID_RE.match(uuid):
        abort(400)
    _png, slim = _skin_refresh(uuid)
    idx = []
    try:
        idx = json.loads((SKINS_HIS / uuid / "index.json").read_text())
    except Exception:
        pass
    idx.sort(key=lambda e: e.get("date", ""), reverse=True)
    return jsonify(slim=slim, history=idx, name=_uuid_name(uuid))

@app.get("/skinhist/<uuid>/<fn>")
def api_skin_hist(uuid, fn):
    require("view_players")
    uuid = uuid.lower()
    fn = re.sub(r"[^A-Za-z0-9.-]", "", fn)
    f = SKINS_HIS / uuid / fn
    if not UUID_RE.match(uuid) or not f.exists():
        abort(404)
    return send_file(f, max_age=604800)

def _skin_snapshot_loop():
    """Hilo diario: fotografía la skin de cada whitelisted (el archivo crece solo)."""
    mark = SKINS_DIR / "last_snapshot.txt"
    while True:
        try:
            today = time.strftime("%Y-%m-%d")
            done = mark.read_text().strip() if mark.exists() else ""
            if done != today:
                for e in whitelist():
                    uid = (e.get("uuid") or "").lower()
                    if UUID_RE.match(uid):
                        _skin_refresh(uid, force=True)
                        time.sleep(2)
                SKINS_DIR.mkdir(parents=True, exist_ok=True)
                mark.write_text(today)
        except Exception:
            pass
        time.sleep(3600)

threading.Thread(target=_skin_snapshot_loop, daemon=True).start()

# ------------------------------------------------------------------ librería 3D (se auto-descarga en el servidor)
def _fetch_libs():
    import urllib.request
    dest = PANEL_DIR / "static" / "skinview3d.js"
    if dest.exists() and dest.stat().st_size > 100_000:
        return
    for url in ("https://unpkg.com/skinview3d@3.4.1/bundles/skinview3d.bundle.js",
                "https://cdn.jsdelivr.net/npm/skinview3d@3.4.1/bundles/skinview3d.bundle.js"):
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = r.read()
            if len(data) > 100_000:
                dest.write_bytes(data)
                return
        except Exception:
            continue

threading.Thread(target=_fetch_libs, daemon=True).start()

# ------------------------------------------------------------------ sistema (solo admin)
SYS_LOG = DATA_DIR / "system.log"
_sys_lock = threading.Lock()
_sys_busy = {}

def _syslog(line):
    with open(SYS_LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {line}\n")

def _sys_run_bg(job, cmd):
    def worker():
        _syslog(f"[{job}] iniciando: {' '.join(cmd)}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            out = (r.stdout or "") + (r.stderr or "")
            for ln in out.splitlines()[-40:]:
                _syslog(f"[{job}] {ln}")
            _syslog(f"[{job}] terminó con código {r.returncode}")
        except Exception as e:
            _syslog(f"[{job}] ERROR: {e}")
        finally:
            _sys_busy.pop(job, None)
    with _sys_lock:
        if job in _sys_busy:
            return False
        _sys_busy[job] = time.time()
    threading.Thread(target=worker, daemon=True).start()
    return True

def _require_admin():
    u = require()
    if u["role"] != "admin" or not _csrf_ok():
        abort(403)
    return u

@app.get("/api/system/status")
def api_system_status():
    u = require()
    if u["role"] != "admin":
        abort(403)
    lines = []
    try:
        lines = SYS_LOG.read_text().splitlines()[-30:]
    except Exception:
        pass
    s = get_settings()
    users = load_users()
    return jsonify(busy=list(_sys_busy.keys()), log=lines,
                   pin_set=bool(s.get("public_pin")),
                   engine=engine_kind(),
                   skinlib=(PANEL_DIR / "static" / "skinview3d.js").exists(),
                   totp_on=bool(users.get(u["name"], {}).get("totp")))

@app.post("/api/system/set_pin")
def api_system_set_pin():
    u = _require_admin()
    body = request.get_json(silent=True) or {}
    pin = (body.get("pin") or "").strip()
    if not (4 <= len(pin) <= 32):
        return jsonify(error="El PIN debe tener entre 4 y 32 caracteres"), 400
    s = get_settings()
    s["public_pin"] = pin
    SETTINGS_F.write_text(json.dumps(s))
    audit(u["name"], "changed the public PIN")
    return jsonify(ok=True, output="PIN público actualizado")

@app.post("/api/system/restart_panel")
def api_system_restart_panel():
    u = _require_admin()
    audit(u["name"], "panel restart from Sistema")
    _syslog("[panel] reinicio solicitado desde el panel")
    subprocess.Popen(["bash", "-c", "sleep 1; sudo systemctl restart panel"],
                     start_new_session=True)
    return jsonify(ok=True, output="Reiniciando el panel — vuelve en ~10 segundos")

@app.post("/api/system/regen_icons")
def api_system_regen_icons():
    u = _require_admin()
    ok = _sys_run_bg("iconos", ["python3", str(PANEL_DIR / "get-icons.py")])
    audit(u["name"], "regen icons from Sistema")
    return jsonify(ok=ok, output="Regenerando íconos (1-2 min) — mira el registro"
                   if ok else "Ya hay una regeneración corriendo")

@app.post("/api/system/migrate_paper")
def api_system_migrate_paper():
    u = _require_admin()
    if engine_kind() == "paper":
        return jsonify(error="El motor ya es Paper"), 400
    script = PANEL_DIR / "scripts" / "migrate-to-paper.sh"
    if not script.exists():
        script = PANEL_DIR / "migrate-to-paper.sh"
    ok = _sys_run_bg("paper", ["bash", str(script)])
    audit(u["name"], "PAPER MIGRATION from Sistema")
    return jsonify(ok=ok, output="Migrando a Paper — el server estará ~3 min fuera. Mira el registro."
                   if ok else "Ya hay una migración corriendo")

@app.get("/api/system/backup_extras")
def api_system_backup_extras():
    u = require()
    if u["role"] != "admin":
        abort(403)
    import tarfile
    out = Path(f"/tmp/panel-extras-{int(time.time())}.tar.gz")
    with tarfile.open(out, "w:gz") as tar:
        tar.add(DATA_DIR, arcname="data")
        if MEM_DIR.is_dir():
            tar.add(MEM_DIR, arcname="memories")
    audit(u["name"], "downloaded extras backup")
    return send_file(out, as_attachment=True,
                     download_name=f"panel-extras-{time.strftime('%Y%m%d')}.tar.gz")

# ------------------------------------------------------------------ 2FA alta/baja (solo admin)
@app.post("/api/2fa/setup")
def api_2fa_setup():
    u = _require_admin()
    users = load_users()
    sec = base64.b32encode(secrets.token_bytes(20)).decode()
    users[u["name"]]["totp_pending"] = sec
    save_users(users)
    label = f"ServerPanel:{u['name']}"
    uri = f"otpauth://totp/{label}?secret={sec}&issuer=Server%20of%20Califree&digits=6&period=30"
    audit(u["name"], "2FA setup started")
    return jsonify(secret=sec, otpauth=uri)

@app.post("/api/2fa/confirm")
def api_2fa_confirm():
    u = _require_admin()
    body = request.get_json(silent=True) or {}
    users = load_users()
    rec = users.get(u["name"], {})
    sec = rec.get("totp_pending")
    if not sec:
        return jsonify(error="No hay activación pendiente"), 400
    if not _totp_ok(sec, body.get("code")):
        return jsonify(error="Código incorrecto — revisa la app y el reloj del teléfono"), 401
    rec["totp"] = sec
    rec.pop("totp_pending", None)
    save_users(users)
    audit(u["name"], "2FA ACTIVATED")
    return jsonify(ok=True, output="2FA activado — desde ahora el login te pedirá el código")

@app.post("/api/2fa/disable")
def api_2fa_disable():
    u = _require_admin()
    body = request.get_json(silent=True) or {}
    users = load_users()
    rec = users.get(u["name"], {})
    if not rec.get("totp"):
        return jsonify(error="El 2FA no está activo"), 400
    if not _totp_ok(rec["totp"], body.get("code")):
        return jsonify(error="Código incorrecto"), 401
    rec.pop("totp", None)
    save_users(users)
    audit(u["name"], "2FA disabled")
    return jsonify(ok=True, output="2FA desactivado")

# ------------------------------------------------------------------ security headers
@app.after_request
def _sec_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault("Content-Security-Policy",
        "default-src 'self'; img-src 'self' data: https://mc-heads.net; "
        "script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
        "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
        "connect-src 'self'; frame-ancestors 'none'")
    return resp

# ------------------------------------------------------------------ static
@app.get("/")
def index():
    return send_from_directory(PANEL_DIR / "static", "index.html")

@app.get("/static/<path:p>")
def static_files(p):
    return send_from_directory(PANEL_DIR / "static", p)

@app.get("/banner.png")
def banner():
    f = PANEL_DIR / "static" / "banner.png"
    if f.exists():
        return send_file(f)
    abort(404)

# ------------------------------------------------------------------ bootstrap
def ensure_admin():
    """First run: create admin account interactively (called from setup, not web)."""
    users = load_users()
    if users:
        return
    import getpass
    print("No users yet — let's create the ADMIN account.")
    name = input("Admin username [JEYtheFlash]: ").strip() or "JEYtheFlash"
    while True:
        pw = getpass.getpass("Admin password (8+ chars): ")
        if len(pw) >= 8 and pw == getpass.getpass("Repeat password: "):
            break
        print("Passwords too short or didn't match — try again.")
    users[name] = {"hash": hash_pw(pw), "role": "admin", "perms": {}, "must_change": False}
    save_users(users)
    print(f"Admin '{name}' created.")

if __name__ == "__main__":
    import sys
    if "--create-admin" in sys.argv:
        ensure_admin()
        sys.exit(0)
    cert = PANEL_DIR / "data" / "cert.pem"
    key = PANEL_DIR / "data" / "key.pem"
    ctx = (str(cert), str(key)) if cert.exists() and key.exists() else None
    app.run(host="0.0.0.0", port=int(os.environ.get("PANEL_PORT", "8443")),
            ssl_context=ctx, threaded=True)
