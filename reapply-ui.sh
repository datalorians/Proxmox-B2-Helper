#!/usr/bin/env bash
set -euo pipefail

TARGET="/usr/share/pve-manager/js/pvemanagerlib.js"
BACKUP="/usr/share/pve-manager/js/pvemanagerlib.js.bak"
LOGFILE="/var/log/proxmox-ui-override.log"
# Set to your accessible Backup UI URL (e.g., http://127.0.0.1:8800/ or your Tailscale IP)
BACKUP_UI_URL="http://127.0.0.1:8800/"

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOGFILE"; }

main() {
  if [[ ! -f "$TARGET" ]]; then
    log "Target file not found: $TARGET"
    return 1
  fi

  if [[ ! -f "$BACKUP" ]]; then
    cp "$TARGET" "$BACKUP"
    log "Backup created at $BACKUP"
  fi

  python3 - <<'PYCODE'
from pathlib import Path
import os

target = Path("/usr/share/pve-manager/js/pvemanagerlib.js")
url = os.environ.get("BACKUP_UI_URL", "http://127.0.0.1:8800/")

doc_block = """                        {
                            xtype: 'proxmoxHelpButton',
                            hidden: false,
                            baseCls: 'x-btn',
                            iconCls: 'fa fa-book x-btn-icon-el-default-toolbar-small ',
                            listenToGlobalEvent: false,
                            onlineHelp: 'pve_documentation_index',
                            text: gettext('Documentation'),
                            margin: '0 5 0 0',
                        },
"""

btn_block = f"""                        {{
                            xtype: 'button',
                            baseCls: 'x-btn',
                            iconCls: 'fa fa-external-link',
                            text: 'Backup UI',
                            margin: '0 5 0 0',
                            handler: function () {{
                                const url = '{url}';
                                window.open(url, '_blank', 'noopener');
                            }},
                        }},
"""

old_btn = """                        {
                            xtype: 'button',
                            baseCls: 'x-btn',
                            iconCls: 'fa fa-external-link',
                            text: 'Backup UI',
                            margin: '0 5 0 0',
                            handler: function () {
                                const url = window.location.protocol + '//' + window.location.hostname + ':8800/';
                                window.open(url, '_blank', 'noopener');
                            },
                        },
"""

text = target.read_text()
updated = None

if "text: 'Backup UI'" in text:
    if old_btn in text:
        updated = text.replace(old_btn, btn_block, 1)
    else:
        # If already present but with a different URL, try to replace the handler line
        import re
        pattern = r"(text: 'Backup UI'.*?handler: function \\(\\) \\{)(.*?)(window\\.open\\(url, '_blank', 'noopener'\\);)(.*?\\},)"
        updated = re.sub(
            pattern,
            r"\\1\\n                                const url = '" + url + r"';\\n                                window.open(url, '_blank', 'noopener');\\n                            },",
            text,
            count=1,
            flags=re.S,
        )
else:
    if doc_block in text:
        updated = text.replace(doc_block, doc_block + btn_block, 1)

if not updated or updated == text:
    print("No changes applied; pattern not found or already correct")
    raise SystemExit(0)

target.write_text(updated)
print("patched")
PYCODE

  log "Patched $TARGET"
  systemctl restart pveproxy
  log "Restarted pveproxy"
}

main "$@"
