# ServerPanel — Server of Califree

Self-hosted control panel for a private Minecraft Java server, built from scratch.
Runs 24/7 on Oracle Cloud's Always Free ARM tier at **$0/month**, serving a 20+ player
community across two countries.

Panel de control auto-hospedado para un servidor privado de Minecraft Java, construido
desde cero. Corre 24/7 en el tier gratuito ARM de Oracle Cloud, sirviendo a una
comunidad de 20+ jugadores en dos países.

## Features / Funcionalidades

- **Live dashboard** — server state, online players, tick time (MSPT) and RAM charts via RCON + `/proc`.
- **Player book** — playtime, deaths, kills and distance stats read straight from world files, with stats migrated and merged from a 6-year-old previous world (UUID matching, multi-identity summing).
- **Game-accurate item rendering** — a custom 3D renderer reads the official client's model definitions (`models/`, `items/`, composites) and projects blocks in the game's own GUI dimetric projection; entity items (chests, shulkers, heads, shields with real banner patterns, banners) are built from official entity textures. Enchanted items show the animated glint masked to the item silhouette.
- **Game-faithful tooltips** — per-character colored names (legacy `§` codes and modern JSON components with style inheritance), lore, enchantments with roman numerals, armor trims with material colors, in the Minecraft font.
- **Full inventory X-ray** (admin only) — inventory, Ender Chest, nested shulkers/bundles, item removal online (RCON) or offline (byte-exact NBT surgery with backups, via a hand-written NBT parser/writer).
- **Join requests** — non-whitelisted connection attempts surface by username for one-click accept.
- **Memorias del server** — a permission-gated photo album with uploads, auto-thumbnails and editable titles.
- **Roles & permissions** — admin/mod/viewer with per-user toggles, forced password change, audit log.
- **Hardened serving** — Caddy terminates TLS (auto Let's Encrypt) in front of gunicorn; watchdog cron self-heals the service; scrypt password hashing, HMAC-signed sessions, CSRF header, login rate limiting.

## Architecture / Arquitectura

```
players' browsers ── https://califree.duckdns.org:8443
        │
      Caddy  (TLS, auto-renew, slow-loris shield)
        │ 127.0.0.1:8444
     gunicorn ── Flask app (server.py, single file)
        │            │
     RCON :25575   world files (NBT, stats, logs)
        │
  minecraft.service (vanilla 26.2, systemd, screen)
```

Deployment is automated: every push to `main` triggers a GitHub Action that rsyncs the
code to the VM over SSH, regenerates the icon renders when the generator changed, and
restarts the panel. / El despliegue es automático: cada push a `main` dispara un GitHub
Action que copia el código al servidor, regenera los íconos si el generador cambió y
reinicia el panel.

## Stack

Python 3 (Flask, Pillow, stdlib-only NBT/RCON/TOTP implementations) · vanilla JS single-page
frontend (no frameworks) · Caddy · gunicorn · systemd · GitHub Actions · Oracle Cloud A1 (ARM).

## Repo layout

| Path | What |
|---|---|
| `server.py` | Entire backend: auth, RCON, stats, inventory X-ray, memories, icons |
| `nbt.py` | Byte-exact NBT (Named Binary Tag) reader/writer |
| `get-icons.py` | Asset extractor + 3D inventory-icon renderer (official models & textures) |
| `static/index.html` | The whole frontend (Spanish, Minecraft-inventory × glassmorphism design) |
| `scripts/` | One-time operations (Paper migration, old-world stats import) |
| `.github/workflows/deploy.yml` | Auto-deploy to the Oracle VM |

Secrets (user database, session keys, certificates, photos) never live in this repo —
they stay in `data/`, `memories/` and `icons/` on the server, excluded by `.gitignore`.
