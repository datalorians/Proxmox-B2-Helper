#!/usr/bin/env bash
# Proxmox configs -> Backblaze B2 via rclone (mode A)
set -euo pipefail

ENV_FILE=${ENV_FILE:-/root/proxmox-backup/backup.env}
RCLONE_CONFIG=${RCLONE_CONFIG:-/root/.config/rclone/rclone.conf}

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[[ -f "$ENV_FILE" ]] || die "Missing env file: $ENV_FILE (copy backup.env.sample and fill it)"
set -a
source "$ENV_FILE"
set +a

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "Command not found: $1"; }
require_cmd tar
require_cmd sha256sum

# B2 uploads use rclone unless DRY_RUN skips remote work
if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
  require_cmd rclone
  [[ -n "${B2_ACCOUNT_ID:-}" && -n "${B2_APP_KEY:-}" && -n "${B2_BUCKET:-}" ]] || die "B2_ACCOUNT_ID/B2_APP_KEY/B2_BUCKET must be set"
fi

TS=$(date -u +%Y%m%dT%H%M%SZ)
ARCHIVE_DIR=${LOCAL_ARCHIVE_DIR:-/var/backups/proxmox-b2/configs}
CACHE_DIR=${LOCAL_CACHE:-/var/backups/proxmox-b2/cache}
KEEP_LOCAL=${KEEP_LOCAL:-0}
mkdir -p "$ARCHIVE_DIR" "$CACHE_DIR"
TMPDIR="$CACHE_DIR/run-$TS"
mkdir -p "$TMPDIR"

archive_path="$ARCHIVE_DIR/proxmox-configs-$TS.tar.gz"
target_remote="${RCLONE_REMOTE:-proxmox-b2}:${B2_BUCKET:-unset}/${B2_PREFIX:-proxmox/configs}"

capture_metadata() {
  log "Collecting metadata into $TMPDIR"
  pveversion -v > "$TMPDIR/pveversion.txt" || true
  lsblk -o NAME,SIZE,FSTYPE,TYPE,MOUNTPOINT,LABEL,UUID > "$TMPDIR/lsblk.txt" || true
  df -h > "$TMPDIR/df-h.txt" || true
  mount > "$TMPDIR/mount.txt" || true
  (command -v vgs >/dev/null && vgs) > "$TMPDIR/vgs.txt" || true
  (command -v lvs >/dev/null && lvs) > "$TMPDIR/lvs.txt" || true
  (command -v pvs >/dev/null && pvs) > "$TMPDIR/pvs.txt" || true
}

build_tar() {
  log "Building archive $archive_path"
  capture_metadata
  # Assemble paths conditionally so missing files do not fail the run
  declare -a paths=(
    etc/pve
    var/lib/pve-cluster
    etc/network/interfaces
    etc/hosts
    etc/fstab
    etc/apt/sources.list
    etc/apt/sources.list.d
    etc/sysctl.conf
    etc/sysctl.d
    etc/modprobe.d
    etc/modules-load.d
    etc/lvm
    etc/zfs
    etc/ssh/sshd_config
    etc/ssh/ssh_config
    etc/vzdump.conf
    root/.ssh
  )
  # Filter to existing entries
  declare -a existing=()
  for p in "${paths[@]}"; do
    [[ -e "/$p" ]] && existing+=("$p")
  done
  tar -czpf "$archive_path" --warning=no-file-ignored --ignore-failed-read -C / "${existing[@]}" -C "$TMPDIR" .
  sha256sum "$archive_path" > "$archive_path.sha256"
}

ensure_rclone_remote() {
  [[ "${DRY_RUN:-0}" -eq 0 ]] || return 0
  mkdir -p "$(dirname "$RCLONE_CONFIG")"
  if ! rclone --config "$RCLONE_CONFIG" listremotes 2>/dev/null | grep -Fxq "${RCLONE_REMOTE:-proxmox-b2}:"; then
    log "Creating rclone remote ${RCLONE_REMOTE:-proxmox-b2}"
    rclone config create "${RCLONE_REMOTE:-proxmox-b2}" b2 account "$B2_ACCOUNT_ID" key "$B2_APP_KEY" --config "$RCLONE_CONFIG" --non-interactive
    chmod 600 "$RCLONE_CONFIG"
  fi
}

upload_archive() {
  [[ "${DRY_RUN:-0}" -eq 0 ]] && log "Uploading to $target_remote"
  if [[ "${DRY_RUN:-0}" -eq 0 ]]; then
    rclone copy "$archive_path" "$target_remote" --config "$RCLONE_CONFIG" --b2-hard-delete
  else
    log "DRY_RUN=1, skipping upload"
  fi
}

prune_remote() {
  [[ "${DRY_RUN:-0}" -eq 0 ]] || { log "DRY_RUN=1, skipping remote prune"; return; }
  local keep=${RETENTION_COUNT:-4}
  mapfile -t files < <(rclone lsf "$target_remote" --config "$RCLONE_CONFIG" --files-only | sort)
  local total=${#files[@]}
  (( total <= keep )) && { log "Remote retention OK (total=$total keep=$keep)"; return; }
  local remove_count=$(( total - keep ))
  log "Pruning $remove_count old archives (keep newest $keep)"
  for ((i=0; i<remove_count; i++)); do
    rclone deletefile "$target_remote/${files[$i]}" --config "$RCLONE_CONFIG"
  done
}

cleanup_local() {
  if [[ "${KEEP_LOCAL:-0}" -eq 0 ]]; then
    rm -f "$archive_path" "$archive_path.sha256" 2>/dev/null || true
    log "Removed local archive (KEEP_LOCAL=0)"
  else
    log "Keeping local archive (KEEP_LOCAL!=0)"
  }
}

main() {
  log "Starting configs backup (mode=${BACKUP_MODE:-configs}, dry_run=${DRY_RUN:-0})"
  build_tar
  ensure_rclone_remote
  upload_archive
  prune_remote
  cleanup_local
  log "Done. Archive at $archive_path"
}

main "$@"
