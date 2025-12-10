# Proxmox-B2-Helper

A small toolkit to back up Proxmox configs and VM/CT dumps to Backblaze B2 (or any rclone-supported B2), with a simple Flask UI, cost estimates, and one-click uploads/restores.

## Features
- Config backups to B2 via rclone, with retention and optional auto-purge of local archives.
- VM/CT backups (vzdump stop mode) from the UI; upload dumps to B2 and restore from B2.
- Cost estimates (storage + egress) shown per B2 object.
- Simple web UI (Flask) and a Proxmox top-bar button helper (optional).
- Systemd units for scheduled backups and the UI service.

## Contents
- `backup-configs.sh` — config backup script.
- `backup.env.sample` — sample env for B2 creds and settings (do not commit real creds).
- `gui/` — Flask app and dashboard template.
- `reapply-ui.sh` — optional helper to add a Proxmox top-bar "Backup UI" button (uses `BACKUP_UI_URL`).
- `*.service` / `*.timer` — sample systemd units for configs backup, UI, and top-bar button reapply.
- `.gitignore` — excludes secrets, logs, archives.
- `install.sh` — installer for fresh Proxmox hosts (installs deps, copies files to `/root/proxmox-backup`, enables services/timers, applies UI button).

## Install on a fresh Proxmox host (recommended)
Run as root on the Proxmox node:
```bash
./install.sh
```
What it does:
- Verifies Proxmox (`pveversion`), installs deps (rclone, python3, venv, pip).
- Copies the toolkit to `/root/proxmox-backup` and ensures `/var/backups/proxmox-b2/{configs,vms,cache}` exist.
- Installs/enables systemd units: `proxmox-config-b2.timer`, `proxmox-backup-gui.service`, `proxmox-ui-override.timer`.
- Applies the Proxmox top-bar "Backup UI" button via `reapply-ui.sh` (defaults to `http://127.0.0.1:8800/`; set `BACKUP_UI_URL` env to override before running).
- If `backup.env` is missing, creates it from the sample with `DRY_RUN=1` and a placeholder bucket. Edit `/root/proxmox-backup/backup.env` to add B2 creds and set `DRY_RUN=0` to enable uploads.

After install:
- Edit `/root/proxmox-backup/backup.env` (B2 creds, DRY_RUN=0).
- UI: `http://127.0.0.1:8800` or your Tailscale IP. The Proxmox top bar should have “Backup UI”.
- Services: `proxmox-config-b2.timer`, `proxmox-backup-gui.service`, `proxmox-ui-override.timer`.
- Optional: run one config backup manually to verify: `systemctl start proxmox-config-b2.service`.

## Manual setup (if not using install.sh)
1) Install deps:
   ```bash
   apt-get install -y rclone python3-flask
   ```
2) Configure env:
   ```bash
   cp backup.env.sample backup.env
   # edit: B2_ACCOUNT_ID, B2_APP_KEY, B2_BUCKET, B2_PREFIX, DRY_RUN=0 to enable uploads
   ```
3) Run the UI:
   ```bash
   ENV_FILE=$PWD/backup.env python3 gui/app.py
   # open http://127.0.0.1:8800 (or your Tailscale IP)
   ```
4) Optional systemd units (adjust paths):
   - `proxmox-config-b2.service` / `.timer` for scheduled config backups
   - `proxmox-backup-gui.service` for the UI
   - `proxmox-ui-override.service` / `.timer` for the Proxmox top-bar button

## Access
- Serve the UI only on local or Tailscale addresses. It is not meant to be exposed publicly or integrated into the main Proxmox interface beyond the convenience button link.

## Notes
- Portions of this project were assisted using Cursor with the GPT-5 model.
