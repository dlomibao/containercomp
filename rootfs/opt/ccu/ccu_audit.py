"""Audit trail and session recording for the containercomp desktop.

Every action that goes through cu (the `ccu` CLI and the MCP server both do) is
appended to /var/log/ccu/audit/audit.jsonl, one JSON object per line, and a
screenshot is saved shortly after each state-changing action. Each entry
carries the sha256 of the previous line, so edits and deletions inside the log
break the chain. The agent has root in the container, so this is tamper-evident
at best; `ccu` also mirrors entries to the host as they are written.

Settings (environment variables, read by each process):
  CCU_AUDIT             1 (default) or 0 to turn logging off
  CCU_AUDIT_SHOTS       1 (default) or 0: save screenshots alongside entries
  CCU_AUDIT_MAX_SHOTS   keep at most this many screenshots, oldest deleted first
                        (default 500; 0 = no limit)
  CCU_AUDIT_TYPE        how `type` text and clipboard contents are logged:
                        length (default), hash, full
  CCU_AUDIT_SHOT_DELAY  seconds between an action and its screenshot (default 0.5)
  CCU_AUDIT_SOURCE      tag for entries: ccu sets cli, the MCP server sets mcp
  CCU_AUDIT_ECHO        1: also write each entry to stderr after "ccu-audit<TAB>",
                        which `ccu` strips off into the host mirror
"""
import contextlib
import datetime
import fcntl
import functools
import hashlib
import html
import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time


def _flag(name, default):
    return os.environ.get(name, default).strip().lower() not in ("0", "false", "no", "off", "")


def _num(name, default, cast=int):
    try:
        return cast(os.environ.get(name, "") or default)
    except ValueError:
        return default


AUDIT_DIR = os.environ.get("CCU_AUDIT_DIR", "/var/log/ccu/audit")
LOG = os.path.join(AUDIT_DIR, "audit.jsonl")
SHOTS = os.path.join(AUDIT_DIR, "shots")
REC_DIR = "/var/log/ccu/rec"
REC_STATE = os.path.join(REC_DIR, "current.json")

ENABLED = _flag("CCU_AUDIT", "1")
SHOTS_ON = _flag("CCU_AUDIT_SHOTS", "1")
MAX_SHOTS = _num("CCU_AUDIT_MAX_SHOTS", 500)
TYPE_MODE = os.environ.get("CCU_AUDIT_TYPE", "length").strip().lower()
SHOT_DELAY = _num("CCU_AUDIT_SHOT_DELAY", 0.5, float)
SOURCE = os.environ.get("CCU_AUDIT_SOURCE", "container")
ECHO = _flag("CCU_AUDIT_ECHO", "0")
ECHO_PREFIX = "ccu-audit\t"
TAIL_CHARS = 1000

# Set by cu.py once its functions exist (avoids a circular import).
window_title = lambda: ""  # noqa: E731
x_env = lambda: dict(os.environ)  # noqa: E731
screen_size = lambda: (1280, 800)  # noqa: E731

_state = threading.local()
_warned = []


class AuditError(Exception):
    pass


def _warn(e):
    # Logging must never break the action itself; complain once per process.
    if not _warned:
        _warned.append(e)
        print(f"cu: audit log write failed: {e}", file=sys.stderr)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sha(line):
    return hashlib.sha256(line).hexdigest()


def _own(path):
    # Files created by root (e.g. `ccu exec --root cu ...`) must stay writable
    # for the desktop user, who owns /var/log/ccu.
    if os.geteuid() == 0:
        with contextlib.suppress(OSError):
            st = os.stat(os.path.dirname(AUDIT_DIR))
            os.chown(path, st.st_uid, st.st_gid)


def _ensure_dirs():
    for d in (AUDIT_DIR, SHOTS):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
            _own(d)


def _clip(v, n=TAIL_CHARS):
    if isinstance(v, str) and len(v) > n:
        return "..." + v[-n:]
    if isinstance(v, dict):
        return {k: _clip(x, n) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clip(x, n) for x in v]
    return v


def redact_text(text):
    """How typed text appears in the log, per CCU_AUDIT_TYPE."""
    if TYPE_MODE == "full":
        return text
    out = {"redacted": "length", "len": len(text)}
    if TYPE_MODE == "hash":
        out.update(redacted="sha256", sha256=hashlib.sha256(text.encode()).hexdigest())
    return out


def _last_line(f):
    f.seek(0, os.SEEK_END)
    pos, buf = f.tell(), b""
    while pos > 0:
        step = min(65536, pos)
        pos -= step
        f.seek(pos)
        buf = f.read(step) + buf
        body = buf.rstrip(b"\n")
        if b"\n" in body or pos == 0:
            return body.rsplit(b"\n", 1)[-1]
    return b""


def _prune_shots():
    if MAX_SHOTS <= 0:
        return
    try:
        names = sorted(os.listdir(SHOTS))
    except OSError:
        return
    for n in names[: max(0, len(names) - MAX_SHOTS)]:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(SHOTS, n))


def _shot_later(path, delay):
    # Detached, so the action returns at once and `podman exec` doesn't wait.
    subprocess.Popen(
        ["sh", "-c", 'sleep "$1"; exec scrot --overwrite --pointer "$2"', "sh", str(delay), path],
        env=x_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def record(action, args=None, kind="command", ok=True, result=None, error=None,
           window=None, shot=False, image=None, started=None):
    """Append one entry. shot=True schedules a screenshot after `SHOT_DELAY`;
    `image` (PNG bytes) is saved as the entry's screenshot instead."""
    if not ENABLED:
        return None
    try:
        _ensure_dirs()
        fd = os.open(LOG, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
        with os.fdopen(fd, "r+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            if os.fstat(f.fileno()).st_size == 0:
                _own(LOG)
            last = _last_line(f)
            seq, prev = 1, None
            if last:
                prev = _sha(last)
                try:
                    seq = int(json.loads(last)["seq"]) + 1
                except (ValueError, KeyError, TypeError):
                    f.seek(0)
                    seq = sum(1 for _ in f) + 1
            shot_rel = None
            if SHOTS_ON and (shot or image):
                shot_rel = f"shots/{seq:08d}-{action}.png"
                if image:
                    path = os.path.join(AUDIT_DIR, shot_rel)
                    with open(path, "wb") as out:
                        out.write(image)
                    _own(path)
            entry = {
                "seq": seq,
                "ts": _now() if started is None else datetime.datetime.fromtimestamp(
                    started, datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "source": SOURCE,
                "kind": kind,
                "action": action,
                "args": args or {},
                "ok": ok,
                "result": _clip(result),
                "error": error,
                "window": window,
                "shot": shot_rel,
                "ms": None if started is None else int((time.time() - started) * 1000),
                "prev": prev,
            }
            line = json.dumps(entry, separators=(",", ":"), default=str).encode()
            f.write(line + b"\n")
            f.flush()
        if shot_rel and not image:
            _shot_later(os.path.join(AUDIT_DIR, shot_rel), SHOT_DELAY * (4 if action == "launch" else 1))
        if shot_rel:
            _prune_shots()
        if ECHO:
            sys.stderr.write(ECHO_PREFIX + line.decode() + "\n")
            sys.stderr.flush()
        return entry
    except Exception as e:  # noqa: BLE001
        _warn(e)
        return None


def _window():
    try:
        return window_title()
    except Exception:  # noqa: BLE001
        return ""


@contextlib.contextmanager
def event(action, args=None, kind="command", shot=True):
    """Log the block as one entry. Inside, set ev["result"] (and ev["ok"] to
    override success, ev["image"] to attach PNG bytes). Nested events (click
    calling move, zoom calling screenshot) are folded into the outer one."""
    if not ENABLED or getattr(_state, "depth", 0):
        yield {}
        return
    ev = {}
    window = _window()
    started = time.time()
    err = None
    _state.depth = 1
    try:
        yield ev
    except BaseException as e:
        err = str(e) or type(e).__name__
        raise
    finally:
        _state.depth = 0
        ok = err is None and ev.get("ok", True)
        record(action, args, kind, ok=ok, result=ev.get("result"), error=err or ev.get("error"),
               window=window, shot=shot and kind != "observation", image=ev.get("image"),
               started=started)


def audited(kind="action", name=None, redact=()):
    """Decorator: log each top-level call of a cu function with its arguments."""
    def deco(fn):
        sig = inspect.signature(fn)
        action = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*a, **kw):
            if not ENABLED or getattr(_state, "depth", 0):
                return fn(*a, **kw)
            bound = sig.bind(*a, **kw)
            bound.apply_defaults()
            args = dict(bound.arguments)
            for k in redact:
                if isinstance(args.get(k), str):
                    args[k] = redact_text(args[k])
            with event(action, args, kind) as ev:
                out = fn(*a, **kw)
                if isinstance(out, bytes):
                    ev["result"] = {"png_bytes": len(out)}
                    ev["image"] = out
                else:
                    ev["result"] = out
            return out
        return wrapper
    return deco


# --- commands that don't go through the cu library ----------------------------

def _tee(src, dst, tails, key):
    tail = b""
    for chunk in iter(lambda: src.read1(65536), b""):
        dst.write(chunk)
        dst.flush()
        tail = (tail + chunk)[-TAIL_CHARS:]
    tails[key] = tail.decode(errors="replace")


def run_command(argv, root=False, action="exec"):
    """Run argv with this process's stdio, log it with its exit code and output
    tail, and return the exit code. root=True runs it through sudo."""
    real = ["sudo", "--preserve-env=DISPLAY", "--", *argv] if root else list(argv)
    env = x_env()
    if not ENABLED:
        os.execvpe(real[0], real, env)
    with event(action, {"argv": list(argv), "root": root}) as ev:
        if sys.stdin.isatty():
            # Interactive: keep the terminal, log only the exit code.
            rc = subprocess.call(real, env=env)
            ev["result"] = {"exit": rc}
        else:
            proc = subprocess.Popen(real, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            tails = {}
            threads = [
                threading.Thread(target=_tee, args=(proc.stdout, sys.stdout.buffer, tails, "stdout")),
                threading.Thread(target=_tee, args=(proc.stderr, sys.stderr.buffer, tails, "stderr")),
            ]
            for t in threads:
                t.start()
            rc = proc.wait()
            for t in threads:
                t.join()
            ev["result"] = {"exit": rc, "stdout": tails.get("stdout", ""), "stderr": tails.get("stderr", "")}
        ev["ok"] = rc == 0
    return rc


# --- session recording -----------------------------------------------------------

def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _rec_state():
    try:
        with open(REC_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def record_start(path=None, host_path="", fps=5):
    """Start ffmpeg x11grab in the background; return its state dict."""
    os.makedirs(REC_DIR, exist_ok=True)
    cur = _rec_state()
    if cur and _alive(cur["pid"]):
        raise AuditError(f"already recording to {cur['path']} (pid {cur['pid']}); run: ccu record stop")
    path = path or os.path.join(REC_DIR, time.strftime("rec-%Y%m%d-%H%M%S.mp4"))
    w, h = screen_size()
    env = x_env()
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "warning",
        "-f", "x11grab", "-draw_mouse", "1", "-framerate", str(fps),
        "-video_size", f"{w}x{h}", "-i", env.get("DISPLAY", ":1"),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        # Mostly static screen content: low CPU and small files; 2 threads keeps RSS down.
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p", "-threads", "2",
        "-g", str(fps * 10), "-movflags", "+faststart", "-y", path,
    ]
    with event("record_start", {"path": path, "host_path": host_path, "fps": fps}, shot=False) as ev:
        with open(path + ".log", "wb") as log:
            proc = subprocess.Popen(cmd, env=env, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        time.sleep(1)
        if proc.poll() is not None:
            with open(path + ".log", errors="replace") as log:
                raise AuditError(f"ffmpeg exited ({proc.returncode}): {log.read()[-500:]}")
        cur = {"pid": proc.pid, "path": path, "host_path": host_path, "fps": fps, "started": _now()}
        with open(REC_STATE, "w") as f:
            json.dump(cur, f)
        ev["result"] = {"pid": proc.pid}
    return cur


def record_stop():
    """Stop the recording cleanly (SIGINT lets ffmpeg finish the mp4)."""
    cur = _rec_state()
    if not cur:
        raise AuditError("not recording")
    with event("record_stop", {"path": cur["path"]}, shot=False) as ev:
        pid = cur["pid"]
        if _alive(pid):
            os.kill(pid, signal.SIGINT)
            deadline = time.time() + 20
            while _alive(pid) and time.time() < deadline:
                time.sleep(0.2)
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)
        os.unlink(REC_STATE)
        size = os.path.getsize(cur["path"]) if os.path.exists(cur["path"]) else 0
        if not size:
            raise AuditError(f"recording {cur['path']} is missing or empty; see {cur['path']}.log")
        ev["result"] = {"bytes": size}
    return cur


# --- verification and review -----------------------------------------------------

def read_entries(path=LOG):
    entries = []
    with contextlib.suppress(FileNotFoundError), open(path, "rb") as f:
        for raw in f:
            with contextlib.suppress(ValueError):
                entries.append(json.loads(raw))
    return entries


def verify(path=LOG):
    """Check sequence numbers and the hash chain. Returns (count, problems, head)."""
    problems, n, prev, expected = [], 0, None, 1
    with open(path, "rb") as f:
        for i, raw in enumerate(f, 1):
            line = raw.rstrip(b"\n")
            try:
                e = json.loads(line)
            except ValueError:
                problems.append(f"line {i}: not valid JSON")
                prev = line
                continue
            if e.get("seq") != expected:
                problems.append(f"line {i}: seq {e.get('seq')}, expected {expected}")
            if e.get("prev") != (_sha(prev) if prev is not None else None):
                problems.append(f"line {i}: prev hash does not match line {i - 1}")
            expected = (e.get("seq") if isinstance(e.get("seq"), int) else expected) + 1
            prev = line
            n += 1
    return n, problems, _sha(prev) if prev is not None else None


def render_html(out=None):
    """Write a static timeline (index.html next to shots/) and return its path."""
    out = out or os.path.join(AUDIT_DIR, "index.html")
    entries = read_entries()
    try:
        n, problems, head = verify()
        chain = "intact" if not problems else f"{len(problems)} problem(s): " + "; ".join(problems[:5])
    except FileNotFoundError:
        n, chain, head = 0, "no log", None
    esc = html.escape
    rows = []
    for e in entries:
        shot = e.get("shot")
        if shot and os.path.exists(os.path.join(AUDIT_DIR, shot)):
            img = f'<a href="{esc(shot)}"><img loading="lazy" src="{esc(shot)}" alt=""></a>'
        else:
            img = '<span class="dim">rotated</span>' if shot else ""
        detail = json.dumps(e.get("args"), ensure_ascii=False)
        res = e.get("error") or e.get("result")
        rows.append(
            f'<tr class="{esc(str(e.get("kind")))}{"" if e.get("ok") else " err"}">'
            f'<td>{esc(str(e.get("seq")))}</td><td>{esc(str(e.get("ts", "")))}<br>'
            f'<span class="dim">{esc(str(e.get("source")))} · {esc(str(e.get("ms")))} ms</span></td>'
            f'<td><b>{esc(str(e.get("action")))}</b><br><code>{esc(detail)}</code></td>'
            f'<td><code>{esc(json.dumps(res, ensure_ascii=False) if res is not None else "")}</code></td>'
            f'<td>{esc(e.get("window") or "")}</td><td>{img}</td></tr>'
        )
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Audit Timeline</title>
<style>
:root {{ --bg:#fff; --fg:#1d1d1f; --dim:#6e6e73; --line:#e5e5ea; --err:#fdecea; --obs:#f5f7fb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#161618; --fg:#ececf0; --dim:#9a9aa2; --line:#2c2c30; --err:#3a1d1d; --obs:#1c1f26; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.4 system-ui, sans-serif; margin:16px; }}
table {{ border-collapse:collapse; width:100%; }}
th, td {{ border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }}
code {{ font-size:12px; word-break:break-all; }}
img {{ width:240px; border:1px solid var(--line); }}
.dim {{ color:var(--dim); font-size:12px; }}
tr.observation {{ background:var(--obs); }} tr.err {{ background:var(--err); }}
</style></head><body>
<h1>Audit timeline</h1>
<p>{n} entries · hash chain {esc(chain)} · head <code>{esc(str(head))}</code> · rendered {esc(_now())}</p>
<table><thead><tr><th>#</th><th>time</th><th>action</th><th>result</th><th>active window</th><th>screenshot</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></body></html>
"""
    with open(out, "w") as f:
        f.write(doc)
    return out


# --- CLI: cu exec | cu record | cu audit -------------------------------------------

def main(argv):
    cmd, rest = argv[0], list(argv[1:])
    try:
        if cmd == "exec":
            # cu exec [--root] [--action NAME] [--] CMD...
            root, action = False, "exec"
            while rest and rest[0].startswith("--"):
                opt = rest.pop(0)
                if opt == "--":
                    break
                if opt == "--root":
                    root = True
                elif opt == "--action" and rest:
                    action = rest.pop(0)
                else:
                    raise AuditError(f"unknown exec option {opt}")
            if not rest:
                raise AuditError("usage: cu exec [--root] [--action NAME] [--] CMD...")
            try:
                return run_command(rest, root, action)
            except FileNotFoundError as e:
                print(f"cu: {e}", file=sys.stderr)
                return 127
        if cmd == "record":
            sub = rest.pop(0) if rest else "status"
            if sub == "start":
                host, fps, path = "", _num("CCU_RECORD_FPS", 5), None
                while rest:
                    a = rest.pop(0)
                    if a == "--host-path" and rest:
                        host = rest.pop(0)
                    elif a == "--fps" and rest:
                        fps = int(rest.pop(0))
                    else:
                        path = a
                cur = record_start(path, host, fps)
                print(f"recording {cur['path']} at {fps} fps (pid {cur['pid']})")
            elif sub == "stop":
                cur = record_stop()
                print(cur["path"], cur.get("host_path", ""), sep="\t")
            elif sub == "status":
                cur = _rec_state()
                if cur and _alive(cur["pid"]):
                    print(f"recording {cur['path']} since {cur['started']} (pid {cur['pid']})")
                else:
                    print("not recording")
            else:
                raise AuditError("usage: cu record start [FILE] | stop | status")
            return 0
        if cmd == "audit":
            sub = rest.pop(0) if rest else ""
            if sub == "note" and rest:
                # Host-side events (file copies, shells): cu audit note ACTION key=value...
                args = dict(a.partition("=")[::2] for a in rest[1:])
                record(rest[0], args, kind="command", window=_window())
            elif sub == "verify":
                n, problems, head = verify()
                for p in problems:
                    print(f"  {p}")
                print(f"{'FAILED' if problems else 'ok'}: {n} entries, hash chain "
                      f"{'broken' if problems else 'intact'}, head {head}")
                return 1 if problems else 0
            elif sub == "html":
                print(render_html(rest[0] if rest else None))
            else:
                raise AuditError("usage: cu audit note ACTION [k=v...] | verify | html [OUT]")
            return 0
    except (AuditError, OSError) as e:
        print(f"cu: {e}", file=sys.stderr)
        return 1
    return 2
