#!/usr/bin/env python3
import base64
import json
import os
import pathlib
import subprocess
import sys
from datetime import datetime

import flask
import yaml
import time

HERE = pathlib.Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.yml"


def load_config():
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


CFG = load_config()


def detect_tailscale_ip():
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], text=True, timeout=5)
        line = out.strip().splitlines()
        if line:
            return line[0].strip()
    except Exception:
        pass
    return "127.0.0.1"


def human_size(num):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num < 1024.0:
            return f"{num:3.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"


def run_cmd(cmd, timeout=15, env=None):
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env or os.environ.copy(),
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except FileNotFoundError:
        return 127, "", "not found"
    except Exception as exc:
        return 1, "", str(exc)


def list_local(path):
    p = pathlib.Path(path)
    if not p.exists():
        return {"count": 0, "size": 0, "items": []}
    items = []
    total = 0
    for f in sorted(p.glob("*.tar.gz")):
        stat = f.stat()
        items.append({"name": f.name, "size": stat.st_size, "mtime": stat.st_mtime})
        total += stat.st_size
    return {"count": len(items), "size": total, "items": items}


def b2_stats(remote, bucket, prefix):
    target = f"{remote}:{bucket}/{prefix}".rstrip("/")
    code, out, err = run_cmd(
        ["rclone", "lsjson", target, "--config", "/root/.config/rclone/rclone.conf", "--files-only"]
    )
    if code != 0:
        return {"count": 0, "size": 0, "error": err or out}
    try:
        files = json.loads(out)
        total = sum(f.get("Size", 0) for f in files)
        return {"count": len(files), "size": total}
    except Exception as exc:
        return {"count": 0, "size": 0, "error": str(exc)}


def journal_tail(unit, lines):
    code, out, err = run_cmd(["journalctl", "-u", unit, "-n", str(lines), "--no-pager"])
    if code != 0:
        return err or out
    return out


def b2_list(remote, bucket, prefix, max_items=200):
    target = f"{remote}:{bucket}/{prefix}".rstrip("/")
    code, out, err = run_cmd(
        ["rclone", "lsjson", target, "--config", "/root/.config/rclone/rclone.conf", "--files-only", "--hash", "--metadata"],
        timeout=12,
    )
    if code != 0:
        return {"error": err or out, "items": []}
    try:
        files = json.loads(out)
        files_sorted = sorted(files, key=lambda f: f.get("ModTime", ""), reverse=True)[:max_items]
        items = []
        for f in files_sorted:
            items.append(
                {
                    "name": f.get("Name"),
                    "size": f.get("Size", 0),
                    "size_h": human_size(f.get("Size", 0)),
                    "time": f.get("ModTime", ""),
                }
            )
        total = sum(f.get("Size", 0) for f in files)
        return {"items": items, "total": total}
    except Exception as exc:
        return {"error": str(exc), "items": []}


def delete_b2(remote, bucket, prefix, name):
    target = f"{remote}:{bucket}/{prefix}/{name}".rstrip("/")
    return run_cmd(["rclone", "deletefile", target, "--config", "/root/.config/rclone/rclone.conf"], timeout=12)


def delete_local_file(path, name):
    p = pathlib.Path(path) / name
    try:
        if p.exists():
            p.unlink()
            return True, ""
        return False, "not found"
    except Exception as exc:
        return False, str(exc)


def list_local_recursive(paths, max_items=200):
    items = []
    for base in paths:
        p = pathlib.Path(base)
        if not p.exists():
            continue
        for f in sorted(p.rglob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True):
            if not f.is_file():
                continue
            try:
                st = f.stat()
                items.append(
                    {
                        "name": f.name,
                        "path": str(f),
                        "size": st.st_size,
                        "size_h": human_size(st.st_size),
                        "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(st.st_mtime)),
                    }
                )
                if len(items) >= max_items:
                    return items
            except Exception:
                continue
    return items


def cost_estimates(items):
    # Defaults: storage $0.005/GB-month (prorated), egress $0.01/GB
    storage_rate = 0.005  # per GB-month
    egress_rate = 0.01    # per GB downloaded
    est = []
    total_storage = 0.0
    total_egress = 0.0
    for it in items:
        size_bytes = it.get("size", 0)
        size_gb = size_bytes / (1024 * 1024 * 1024)
        storage = size_gb * storage_rate
        egress = size_gb * egress_rate
        total_storage += storage
        total_egress += egress
        it_copy = dict(it)
        it_copy["est_storage"] = storage
        it_copy["est_egress"] = egress
        est.append(it_copy)
    return est, total_storage, total_egress


def list_vms_and_cts():
    def parse_size_gb(text):
        import re
        m = re.search(r"size=([0-9.]+)([GTM])", text)
        if not m:
            return 0.0
        val = float(m.group(1))
        unit = m.group(2)
        if unit == "T":
            return val * 1024
        if unit == "M":
            return val / 1024
        return val

    def vm_disk_size(vmid):
        size = 0.0
        code, out, err = run_cmd(["qm", "config", str(vmid)], timeout=15)
        if code != 0:
            return 0.0
        for ln in out.splitlines():
            if "size=" in ln:
                size += parse_size_gb(ln)
        return size

    def ct_disk_size(vmid):
        size = 0.0
        code, out, err = run_cmd(["pct", "config", str(vmid)], timeout=15)
        if code != 0:
            return 0.0
        for ln in out.splitlines():
            if "size=" in ln:
                size += parse_size_gb(ln)
        return size

    STORAGE_RATE = 0.005  # per GB-month
    vms = []
    try:
        code, out, err = run_cmd(["qm", "list"], timeout=15)
        if code == 0:
            lines = out.splitlines()[1:]
            for ln in lines:
                parts = ln.split()
                if len(parts) >= 3:
                    vid = parts[0]
                    sz = vm_disk_size(vid)
                    vms.append({"id": vid, "name": parts[1], "status": parts[2], "type": "qemu", "size_gb": sz, "est_storage": sz * STORAGE_RATE})
    except Exception:
        pass
    try:
        code, out, err = run_cmd(["pct", "list"], timeout=15)
        if code == 0:
            lines = out.splitlines()[1:]
            for ln in lines:
                parts = ln.split()
                if len(parts) >= 3:
                    vid = parts[0]
                    sz = ct_disk_size(vid)
                    vms.append({"id": vid, "name": parts[2], "status": parts[1], "type": "lxc", "size_gb": sz, "est_storage": sz * STORAGE_RATE})
    except Exception:
        pass
    return vms


def run_vzdump_stop(vmid):
    return run_cmd(["vzdump", str(vmid), "--mode", "stop", "--dumpdir", "/var/lib/vz/dump"], timeout=15000)


def upload_local_to_b2(scope, local_path):
    cfg = CFG.get("b2", {})
    remote = cfg.get("remote", "proxmox-b2")
    bucket = cfg.get("bucket", "")
    if scope == "configs":
        prefix = cfg.get("prefix_configs", "proxmox/configs")
    else:
        prefix = cfg.get("prefix_vms", "proxmox/vms")
    target = f"{remote}:{bucket}/{prefix}"
    return run_cmd(["rclone", "copy", local_path, target, "--config", "/root/.config/rclone/rclone.conf", "--b2-hard-delete"], timeout=15)


def restore_b2_to_local(scope, name, dest_dir):
    cfg = CFG.get("b2", {})
    remote = cfg.get("remote", "proxmox-b2")
    bucket = cfg.get("bucket", "")
    if scope == "configs":
        prefix = cfg.get("prefix_configs", "proxmox/configs")
    else:
        prefix = cfg.get("prefix_vms", "proxmox/vms")
    dest_dir = pathlib.Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if dest.exists():
        return 1, "", f"destination exists: {dest}"
    staging = pathlib.Path("/tmp") / f"restore-{name}"
    code, out, err = run_cmd(
        ["rclone", "copy", f"{remote}:{bucket}/{prefix}/{name}", str(staging), "--config", "/root/.config/rclone/rclone.conf", "--b2-hard-delete"],
        timeout=15,
    )
    if code != 0:
        return code, out, err
    try:
        staging_file = next(pathlib.Path(staging).iterdir())
        staging_file.replace(dest)
        return 0, str(dest), ""
    except Exception as exc:
        return 1, "", str(exc)


def timer_status(unit):
    code, out, err = run_cmd(["systemctl", "list-timers", unit, "--no-pager"])
    if code != 0:
        return err or out
    return out


def timer_info(timer_name):
    code, out, err = run_cmd(
        ["systemctl", "show", timer_name, "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSecRealtime", "-p", "Unit"],
        timeout=8,
    )
    if code != 0:
        return {"next": "n/a", "last": "n/a", "unit": timer_name}
    data = {}
    for line in out.splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k == "NextElapseUSecRealtime":
            data["next"] = v or "n/a"
        elif k == "LastTriggerUSecRealtime":
            data["last"] = v or "n/a"
        elif k == "Unit":
            data["unit"] = v or timer_name
    return {
        "next": data.get("next", "n/a"),
        "last": data.get("last", "n/a"),
        "unit": data.get("unit", timer_name),
    }


def trigger_service(unit):
    return run_cmd(["systemctl", "start", unit])


def trigger_vm_script(vmid, script_path, env_path):
    if not pathlib.Path(script_path).exists():
        return 127, "", "vm backup script missing"
    env = os.environ.copy()
    env["ENV_FILE"] = env_path
    env["VM_IDS"] = str(vmid)
    return run_cmd([script_path], env=env)


def basic_auth():
    if not CFG.get("auth", {}).get("basic_enabled"):
        return None
    username = CFG["auth"].get("username", "")
    password = CFG["auth"].get("password", "")
    auth_header = flask.request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header.split(" ", 1)[1]).decode("utf-8")
        user, pwd = decoded.split(":", 1)
        return user == username and pwd == password
    except Exception:
        return False


app = flask.Flask(__name__)


@app.before_request
def enforce_auth():
    ok = basic_auth()
    if ok is None:
        return
    if not ok:
        return flask.Response("auth required", 401, {"WWW-Authenticate": 'Basic realm="Backup GUI"'})


def truncate(text, limit=4000):
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]..."


def recent_archives(path, max_items=8):
    p = pathlib.Path(path)
    if not p.exists():
        return []
    files = sorted(p.glob("*.tar.gz"), key=lambda f: f.stat().st_mtime, reverse=True)[:max_items]
    out = []
    for f in files:
        st = f.stat()
        out.append(
            {
                "name": f.name,
                "time": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(st.st_mtime)),
                "size_h": human_size(st.st_size),
            }
        )
    return out


def recent_runs(unit, max_items=6):
    """
    Parse recent runs from journal logs. Looks for 'Starting' and 'Done.' lines to pair.
    """
    code, out, err = run_cmd(
        ["journalctl", "-u", unit, "-n", "400", "--no-pager", "--output", "short-iso"],
        timeout=8,
    )
    if code != 0:
        return []

    entries = []
    for line in reversed(out.splitlines()):  # reverse chronological parse
        if "Starting" in line:
            ts, msg = line.split(" ", 1)
            entries.append({"time": ts, "note": "start", "line": line})
        elif "Done." in line or "Deactivated successfully" in line:
            ts, msg = line.split(" ", 1)
            entries.append({"time": ts, "note": "done", "line": line})
        elif "Failed to start" in line or "exit-code" in line:
            ts, msg = line.split(" ", 1)
            entries.append({"time": ts, "note": "fail", "line": line})

    runs = []
    start = None
    for e in entries:
        if e["note"] == "start":
            start = e
        elif e["note"] in ("done", "fail") and start:
            # pair with start
            try:
                t_start = datetime.fromisoformat(start["time"])
                t_end = datetime.fromisoformat(e["time"])
                dur = t_end - t_start
                duration = f"{dur.total_seconds():.1f}s"
            except Exception:
                duration = "n/a"
            status = "ok" if e["note"] == "done" else "fail"
            runs.append(
                {
                    "time": start["time"],
                    "status": status,
                    "duration": duration,
                    "note": e["line"].split(" ", 2)[-1][:80],
                }
            )
            start = None
        if len(runs) >= max_items:
            break
    return runs


@app.route("/")
def dashboard():
    cfg_paths = CFG.get("paths", {})
    b2_cfg = CFG.get("b2", {})
    ui_cfg = CFG.get("ui", {})
    journal_lines = int(ui_cfg.get("journal_lines", 50))

    local_cfg_raw = list_local(cfg_paths.get("local_configs", "/var/backups/proxmox-b2/configs"))
    local_vms_raw = list_local(cfg_paths.get("local_vms", "/var/backups/proxmox-b2/vms"))

    def fmt_local(raw):
        return {"count": raw["count"], "size_h": human_size(raw["size"])}

    local_cfg = fmt_local(local_cfg_raw)
    local_vms = fmt_local(local_vms_raw)

    remote_cfg_raw = b2_stats(
        b2_cfg.get("remote", "proxmox-b2"), b2_cfg.get("bucket", ""), b2_cfg.get("prefix_configs", "proxmox/configs")
    )
    remote_vms_raw = b2_stats(
        b2_cfg.get("remote", "proxmox-b2"), b2_cfg.get("bucket", ""), b2_cfg.get("prefix_vms", "proxmox/vms")
    )

    def fmt_remote(raw):
        return {
            "count": raw.get("count", 0),
            "size_h": human_size(raw.get("size", 0)),
            "error": raw.get("error"),
        }

    remote_cfg = fmt_remote(remote_cfg_raw)
    remote_vms = fmt_remote(remote_vms_raw)

    timers = {
        "configs": timer_status(cfg_paths.get("configs_service", "proxmox-config-b2.timer").replace(".service", ".timer")),
        "vms": timer_status(cfg_paths.get("vms_service", "proxmox-vms-b2.timer").replace(".service", ".timer")),
    }

    timers_struct = {
        "configs": timer_info(cfg_paths.get("configs_service", "proxmox-config-b2.timer").replace(".service", ".timer")),
        "vms": timer_info(cfg_paths.get("vms_service", "proxmox-vms-b2.timer").replace(".service", ".timer")),
    }

    logs = {
        "configs": truncate(journal_tail(cfg_paths.get("configs_service", "proxmox-config-b2.service"), journal_lines), 3000),
        "vms": truncate(journal_tail(cfg_paths.get("vms_service", "proxmox-vms-b2.service"), journal_lines), 3000),
    }

    runs_cfg_all = recent_runs(cfg_paths.get("configs_service", "proxmox-config-b2.service"), max_items=12)
    runs_vms_all = recent_runs(cfg_paths.get("vms_service", "proxmox-vms-b2.service"), max_items=12)
    runs_cfg = [r for r in runs_cfg_all if r.get("status") == "ok"][:6]
    runs_vms = [r for r in runs_vms_all if r.get("status") == "ok"][:6]

    archives_cfg = recent_archives(cfg_paths.get("local_configs", "/var/backups/proxmox-b2/configs"), max_items=8)
    archives_vms = recent_archives(cfg_paths.get("local_vms", "/var/backups/proxmox-b2/vms"), max_items=8)

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    return flask.render_template(
        "dashboard.html",
        now=now,
        local_cfg=local_cfg,
        local_vms=local_vms,
        remote_cfg=remote_cfg,
        remote_vms=remote_vms,
        timers=timers,
        timers_struct=timers_struct,
        logs=logs,
        runs_cfg=runs_cfg,
        runs_vms=runs_vms,
        archives_cfg=archives_cfg,
        archives_vms=archives_vms,
    )


@app.route("/trigger/configs", methods=["POST"])
def trigger_configs():
    unit = CFG.get("paths", {}).get("configs_service", "proxmox-config-b2.service")
    code, out, err = trigger_service(unit)
    status_code, status_out, status_err = run_cmd(["systemctl", "status", "--no-pager", unit], timeout=10)
    log_tail = truncate(journal_tail(unit, 60), 4000)
    body = (
        f"start exit={code}\nstdout:\n{out}\n\nstderr:\n{err}\n\n"
        f"status exit={status_code}\n{status_out}\n{status_err}\n\n"
        f"logs (last 60 lines):\n{log_tail}\n"
    )
    return (body, 200 if code == 0 else 500)


@app.route("/trigger/vm", methods=["POST"])
def trigger_vm():
    vmid = flask.request.form.get("vmid", "").strip()
    if not vmid.isdigit():
        return "vmid must be numeric", 400
    paths = CFG.get("paths", {})
    script = paths.get("vms_script", "/root/proxmox-backup/backup-vms.sh")
    env_path = paths.get("vms_env", "/root/proxmox-backup/backup.env")
    code, out, err = trigger_vm_script(vmid, script, env_path)
    log_tail = truncate(journal_tail(paths.get("vms_service", "proxmox-vms-b2.service"), 60), 4000)
    body = (
        f"vmid {vmid} exit {code}\nstdout:\n{out}\nstderr:\n{err}\n\n"
        f"logs (last 60 lines):\n{log_tail}\n"
    )
    return (body, 200 if code == 0 else 500)


@app.route("/api/status")
def api_status():
    cfg_paths = CFG.get("paths", {})
    b2_cfg = CFG.get("b2", {})
    data = {
        "local": {
            "configs": list_local(cfg_paths.get("local_configs", "/var/backups/proxmox-b2/configs")),
            "vms": list_local(cfg_paths.get("local_vms", "/var/backups/proxmox-b2/vms")),
        },
        "remote": {
            "configs": b2_stats(b2_cfg.get("remote", "proxmox-b2"), b2_cfg.get("bucket", ""), b2_cfg.get("prefix_configs", "proxmox/configs")),
            "vms": b2_stats(b2_cfg.get("remote", "proxmox-b2"), b2_cfg.get("bucket", ""), b2_cfg.get("prefix_vms", "proxmox/vms")),
        },
    }
    return flask.jsonify(data)


@app.route("/api/backups")
def api_backups():
    cfg_paths = CFG.get("paths", {})
    b2_cfg = CFG.get("b2", {})
    b2c = b2_list(b2_cfg.get("remote", "proxmox-b2"), b2_cfg.get("bucket", ""), b2_cfg.get("prefix_configs", "proxmox/configs"))
    b2v = b2_list(b2_cfg.get("remote", "proxmox-b2"), b2_cfg.get("bucket", ""), b2_cfg.get("prefix_vms", "proxmox/vms"))
    b2c_costed, b2c_storage, b2c_egress = cost_estimates(b2c.get("items", []))
    b2v_costed, b2v_storage, b2v_egress = cost_estimates(b2v.get("items", []))
    local_c = list_local(cfg_paths.get("local_configs", "/var/backups/proxmox-b2/configs"))
    local_v = list_local(cfg_paths.get("local_vms", "/var/backups/proxmox-b2/vms"))
    local_v_dump = list_local_recursive(["/var/lib/vz/dump", "/mnt/thinner/backups"], max_items=400)
    lc_costed, lc_storage, lc_egress = cost_estimates(local_c.get("items", []))
    lv_costed, lv_storage, lv_egress = cost_estimates(local_v.get("items", []))
    lvd_costed, lvd_storage, lvd_egress = cost_estimates(local_v_dump)
    return flask.jsonify(
        {
            "b2_configs": {**b2c, "items": b2c_costed, "total_storage": b2c_storage, "total_egress": b2c_egress},
            "b2_vms": {**b2v, "items": b2v_costed, "total_storage": b2v_storage, "total_egress": b2v_egress},
            "local_configs": {**local_c, "items": lc_costed, "total_storage": lc_storage, "total_egress": lc_egress},
            "local_vms": {**local_v, "items": lv_costed, "total_storage": lv_storage, "total_egress": lv_egress},
            "local_vms_dump": {"items": lvd_costed, "total_storage": lvd_storage, "total_egress": lvd_egress},
        }
    )


@app.route("/api/delete/b2", methods=["POST"])
def api_delete_b2():
    scope = flask.request.form.get("scope", "configs")
    name = flask.request.form.get("name", "")
    if not name:
        return "name required", 400
    b2_cfg = CFG.get("b2", {})
    remote = b2_cfg.get("remote", "proxmox-b2")
    bucket = b2_cfg.get("bucket", "")
    prefix = b2_cfg.get("prefix_configs" if scope == "configs" else "prefix_vms", "proxmox/configs")
    code, out, err = delete_b2(remote, bucket, prefix, name)
    return (f"deleted {name}\n{out}\n{err}", 200 if code == 0 else 500)


@app.route("/api/delete/local", methods=["POST"])
def api_delete_local():
    scope = flask.request.form.get("scope", "configs")
    name = flask.request.form.get("name", "")
    if not name:
        return "name required", 400
    cfg_paths = CFG.get("paths", {})
    base = cfg_paths.get("local_configs" if scope == "configs" else "local_vms", "/var/backups/proxmox-b2/configs")
    ok, msg = delete_local_file(base, name)
    return (f"deleted {name}" if ok else f"delete failed: {msg}", 200 if ok else 500)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    scope = flask.request.form.get("scope", "configs")
    local_path = flask.request.form.get("path", "")
    if not local_path:
        return "path required", 400
    code, out, err = upload_local_to_b2(scope, local_path)
    return (f"upload exit={code}\n{out}\n{err}", 200 if code == 0 else 500)


@app.route("/api/restore", methods=["POST"])
def api_restore():
    scope = flask.request.form.get("scope", "configs")
    name = flask.request.form.get("name", "")
    if not name:
        return "name required", 400
    if scope == "configs":
        dest_dir = "/var/backups/proxmox-b2/configs"
    else:
        dest_dir = "/var/lib/vz/dump"
    code, dest, err = restore_b2_to_local(scope, name, dest_dir)
    return (f"restored to {dest}" if code == 0 else f"restore failed: {err}", 200 if code == 0 else 500)


@app.route("/api/vms")
def api_vms():
    vms = list_vms_and_cts()
    return flask.jsonify({"items": vms})


@app.route("/api/vzdump", methods=["POST"])
def api_vzdump():
    vmid = flask.request.form.get("vmid", "")
    if not vmid:
        return "vmid required", 400
    code, out, err = run_vzdump_stop(vmid)
    return (f"vzdump exit={code}\n{out}\n{err}", 200 if code == 0 else 500)


def main():
    bind_host = CFG.get("bind", {}).get("host", "auto")
    bind_port = int(CFG.get("bind", {}).get("port", 8800))
    if bind_host in ("auto", "tailscale"):
        bind_host = detect_tailscale_ip()
    app.run(host=bind_host, port=bind_port)


if __name__ == "__main__":
    sys.exit(main())
