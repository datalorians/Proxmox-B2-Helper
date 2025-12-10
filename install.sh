#!/usr/bin/env bash
set -euo pipefail

log() {
  echo "[${SCRIPT_NAME}] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    log "ERROR: run as root"
    exit 1
  fi
}

require_proxmox() {
  if ! command -v pveversion >/dev/null 2>&1; then
    log "ERROR: this installer is intended for Proxmox hosts (pveversion not found)"
    exit 1
  fi
}

install_deps() {
  log "Installing dependencies (rclone, python3, venv, pip)..."
  apt-get update -qq
  apt-get install -y -qq rclone python3 python3-venv python3-pip >/dev/null
}

copy_payload() {
  mkdir -p "$DST"
  log "Copying files to $DST"
  rsync -a --exclude ".git" "$SRC/" "$DST/"
}

prepare_env() {
  if [[ ! -f "$DST/backup.env" ]]; then
    cp "$DST/backup.env.sample" "$DST/backup.env"
    # Default to dry-run and placeholder bucket until the user edits
    sed -i 's/^DRY_RUN=.*/DRY_RUN=1/' "$DST/backup.env" || true
    log "Created $DST/backup.env (DRY_RUN=1, placeholder bucket). Edit this file to add B2 creds and set DRY_RUN=0."
  else
    log "Keeping existing $DST/backup.env"
  fi
}

install_units() {
  log "Installing systemd units"
  cp "$DST/proxmox-config-b2.service" /etc/systemd/system/
  cp "$DST/proxmox-config-b2.timer" /etc/systemd/system/
  cp "$DST/proxmox-backup-gui.service" /etc/systemd/system/
  cp "$DST/proxmox-ui-override.service" /etc/systemd/system/
  cp "$DST/proxmox-ui-override.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now proxmox-config-b2.timer proxmox-ui-override.timer proxmox-backup-gui.service
}

apply_ui_button() {
  local url="${BACKUP_UI_URL:-http://127.0.0.1:8800/}"
  log "Applying Proxmox UI button (Backup UI -> ${url})"
  BACKUP_UI_URL="$url" bash "$DST/reapply-ui.sh" || log "WARNING: reapply-ui.sh reported an error"
}

create_paths() {
  mkdir -p /var/backups/proxmox-b2/configs /var/backups/proxmox-b2/vms /var/backups/proxmox-b2/cache
}

print_next_steps() {
  cat <<'EOF'

Install complete.

Next steps:
- Edit /root/proxmox-backup/backup.env: set B2_ACCOUNT_ID, B2_APP_KEY, B2_BUCKET, and set DRY_RUN=0 to enable uploads.
- UI: http://127.0.0.1:8800 (or your Tailscale IP). The Proxmox top bar has a "Backup UI" button.
- Services:
  * proxmox-config-b2.timer (weekly config backups)
  * proxmox-backup-gui.service (Flask UI)
  * proxmox-ui-override.timer (reapplies UI button on boot)
- Run a manual config backup once: systemctl start proxmox-config-b2.service
EOF
}

main() {
  SCRIPT_NAME="install.sh"
  SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  DST="/root/proxmox-backup"

  require_root
  require_proxmox
  install_deps
  copy_payload
  create_paths
  prepare_env
  install_units
  apply_ui_button
  print_next_steps
}

main "$@"
