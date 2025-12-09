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

## Setup
1) Install dependencies:
   ```bash
   apt-get install -y rclone python3-flask
   ```

2) Configure B2 (or B2) creds:
   ```bash
   cp backup.env.sample backup.env
   # edit backup.env: set B2_ACCOUNT_ID, B2_APP_KEY, B2_BUCKET, etc.
   ```

3) Optional: top-bar button helper
   - Edit `reapply-ui.sh` and set `BACKUP_UI_URL` (e.g., `http://127.0.0.1:8800/` or your Tailscale URL).
   - Install the service/timer: `proxmox-ui-override.service` / `.timer` (sample units here).

4) Systemd units (samples):
   - `proxmox-config-b2.service` / `.timer` — scheduled config backups.
   - `proxmox-backup-gui.service` — runs the Flask UI.
   - `proxmox-ui-override.service` / `.timer` — optional top-bar button reapply.
   Adjust paths inside units if your layout differs.

5) Run the UI locally for testing:
   ```bash
   export ENV_FILE=/root/proxmox-backup/backup.env  # adjust path
   python3 gui/app.py
   # access at http://127.0.0.1:8800
   ```

## Safety & secrets
- Do NOT commit `backup.env` or any real credentials/keys.
- Do NOT commit dumps/logs/archives. `.gitignore` excludes common patterns; add more as needed.
- `reapply-ui.sh` uses `BACKUP_UI_URL` from the environment; defaults to `http://127.0.0.1:8800/` to avoid leaking private IPs.

## GitHub
This repo is intended to be private. Before pushing, verify no secrets or private IPs are present.

## Getting started (quick run)
1) Copy env and fill in B2 values:
   ```bash
   cp backup.env.sample backup.env
   # edit backup.env: set B2_ACCOUNT_ID, B2_APP_KEY, B2_BUCKET, etc.
   ```
2) Install deps:
   ```bash
   apt-get install -y rclone python3-flask
   ```
3) Run the UI:
   ```bash
   ENV_FILE=$PWD/backup.env python3 gui/app.py
   # then open http://127.0.0.1:8800
   ```
4) Optional: install systemd units (adjust paths):
   - `proxmox-config-b2.service` / `.timer` for scheduled config backups
   - `proxmox-backup-gui.service` for the UI
   - `proxmox-ui-override.service` / `.timer` for the Proxmox top-bar button

## Notes
- Portions of this project were assisted using Cursor with the GPT-5 model.
