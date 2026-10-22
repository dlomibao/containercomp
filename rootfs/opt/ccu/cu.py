#!/usr/bin/env python3
"""cu: drive the container's X display (mouse, keyboard, screenshots).

Used directly as a CLI inside the container (and by the host `ccu` script via
`podman exec`), and imported as a library by mcp_server.py.

Coordinates are screen pixels with (0, 0) at the top-left. Screenshots are
taken at the native screen resolution, so pixel positions in a screenshot are
directly clickable.
"""
import argparse
import io
import os
import shlex
import subprocess
import sys
import tempfile
import time

import ccu_audit as audit

DISPLAY = os.environ.get("DISPLAY", ":1")
SESSION_ENV = os.path.expanduser("~/.cache/ccu/session.env")
LAUNCH_LOG_DIR = os.path.expanduser("~/.cache/ccu/launch")

BUTTONS = {"left": "1", "middle": "2", "right": "3"}
SCROLL_BUTTONS = {"up": "4", "down": "5", "left": "6", "right": "7"}


class CuError(Exception):
    pass


def _env():
    env = dict(os.environ, DISPLAY=DISPLAY)
    try:
        with open(SESSION_ENV) as f:
            for line in f:
                line = line.strip()
                if line.startswith("export ") and "=" in line:
                    key, _, val = line[len("export "):].partition("=")
                    env[key] = shlex.split(val)[0] if val else ""
    except FileNotFoundError:
        pass
    return env


def _run(*args, check=True):
    proc = subprocess.run(args, env=_env(), capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise CuError(f"{args[0]} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def xdotool(*args):
    return _run("xdotool", *[str(a) for a in args])


# --- observation -----------------------------------------------------------

@audit.audited("observation", name="screenshot")
def screenshot_png(cursor=True):
    """Return the full screen as PNG bytes."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        path = tmp.name
    try:
        args = ["scrot", "--overwrite"]
        if cursor:
            args.append("--pointer")
        _run(*args, path)
        with open(path, "rb") as f:
            return f.read()
    finally:
        os.unlink(path)


@audit.audited("observation", name="zoom")
def zoom_png(x0, y0, x1, y1, scale=2.0):
    """Crop a region of the screen and enlarge it, for reading small text."""
    from PIL import Image

    img = Image.open(io.BytesIO(screenshot_png(cursor=False)))
    region = img.crop((x0, y0, x1, y1))
    if scale != 1:
        size = (max(1, int(region.width * scale)), max(1, int(region.height * scale)))
        region = region.resize(size, Image.LANCZOS)
    out = io.BytesIO()
    region.save(out, format="PNG")
    return out.getvalue()


def screen_size():
    w, h = xdotool("getdisplaygeometry").split()
    return int(w), int(h)


def cursor_position():
    out = xdotool("getmouselocation", "--shell")
    vals = dict(line.split("=", 1) for line in out.split())
    return int(vals["X"]), int(vals["Y"])


def list_windows():
    """Return [(window_id, pid, title)] for managed top-level windows."""
    rows = []
    for line in _run("wmctrl", "-lp").splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 4:
            title = parts[4] if len(parts) == 5 else ""
            rows.append((parts[0], parts[2], title))
    return rows


def active_window():
    try:
        return xdotool("getactivewindow", "getwindowname").strip()
    except CuError:
        return ""


# --- actions -----------------------------------------------------------------

@audit.audited()
def move(x, y):
    # --sync waits for a motion event, which never comes if the pointer is
    # already there (xdotool then stalls ~16s).
    if cursor_position() == (int(x), int(y)):
        return
    xdotool("mousemove", "--sync", x, y)


@audit.audited()
def click(x=None, y=None, button="left", count=1, modifiers=None):
    if button not in BUTTONS:
        raise CuError(f"unknown button {button!r}; use left, middle or right")
    if x is not None and y is not None:
        move(x, y)
    mods = [m for m in (modifiers or "").split("+") if m]
    for m in mods:
        xdotool("keydown", m)
    try:
        xdotool("click", "--repeat", count, "--delay", 80, BUTTONS[button])
    finally:
        for m in reversed(mods):
            xdotool("keyup", m)


@audit.audited()
def mouse_down(button="left"):
    xdotool("mousedown", BUTTONS[button])


@audit.audited()
def mouse_up(button="left"):
    xdotool("mouseup", BUTTONS[button])


@audit.audited()
def drag(x0, y0, x1, y1, button="left", steps=10):
    move(x0, y0)
    mouse_down(button)
    try:
        for i in range(1, steps + 1):
            xdotool("mousemove", x0 + (x1 - x0) * i // steps, y0 + (y1 - y0) * i // steps)
            time.sleep(0.02)
    finally:
        mouse_up(button)


@audit.audited()
def scroll(x, y, direction="down", amount=3):
    if direction not in SCROLL_BUTTONS:
        raise CuError(f"unknown direction {direction!r}; use up, down, left or right")
    move(x, y)
    xdotool("click", "--repeat", amount, "--delay", 30, SCROLL_BUTTONS[direction])


@audit.audited(name="type", redact=("text",))
def type_text(text, delay_ms=12):
    # Type line by line so newlines become real Return presses in every app.
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            xdotool("type", "--delay", delay_ms, "--", line)
        if i < len(lines) - 1:
            xdotool("key", "Return")


@audit.audited()
def key(*combos):
    """Press key combos in xdotool syntax, e.g. "ctrl+s", "Return", "alt+F4"."""
    xdotool("key", "--delay", 50, "--", *combos)


@audit.audited()
def launch(command):
    """Start a GUI command detached from this process; return (pid, log path)."""
    os.makedirs(LAUNCH_LOG_DIR, exist_ok=True)
    name = os.path.basename(shlex.split(command)[0]) if command.strip() else "cmd"
    log = os.path.join(LAUNCH_LOG_DIR, f"{name}-{int(time.time())}.log")
    with open(log, "wb") as out:
        proc = subprocess.Popen(
            ["bash", "-lc", command],
            env=_env(),
            cwd=os.path.expanduser("~"),
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return proc.pid, log


@audit.audited()
def focus(pattern):
    """Activate the first window whose title contains `pattern`."""
    proc = subprocess.run(["wmctrl", "-a", pattern], env=_env(), capture_output=True, text=True)
    if proc.returncode != 0:
        raise CuError(f"no window matching {pattern!r}")


# --- clipboard -----------------------------------------------------------------
# X selections belong to a process: xclip forks into the background and serves
# the value until something else takes the selection over. CLIPBOARD is what
# ctrl+c / ctrl+v use; PRIMARY is the last highlighted text (middle-click paste).

SELECTIONS = ("clipboard", "primary")
TEXT_TARGETS = ("UTF8_STRING", "text/plain;charset=utf-8", "STRING", "TEXT", "text/plain")
META_TARGETS = ("TARGETS", "TIMESTAMP", "MULTIPLE", "SAVE_TARGETS", "DELETE", "INCR")


def _selection(selection):
    if selection not in SELECTIONS:
        raise CuError(f"unknown selection {selection!r}; use clipboard or primary")
    return selection


def _label(selection):
    return "clipboard" if selection == "clipboard" else "PRIMARY selection"


def _audit_data(data):
    # Clipboard contents are logged like typed text (CCU_AUDIT_TYPE); binary data by size only.
    if isinstance(data, str):
        return audit.redact_text(data)
    return {"redacted": "bytes", "len": len(data)}


def clipboard_targets(selection="clipboard"):
    """Data types the selection's owner offers, e.g. ["UTF8_STRING"] or
    ["image/png"]; [] when nothing owns it (empty clipboard)."""
    proc = subprocess.run(["xclip", "-selection", _selection(selection), "-t", "TARGETS", "-o"],
                          env=_env(), capture_output=True, text=True, timeout=10)
    if proc.returncode != 0:
        return []
    return [t for t in proc.stdout.split() if t not in META_TARGETS]


def clipboard_get(selection="clipboard", mime=None, image_ok=False):
    """Return (data, type): str for text, bytes for other types (mime="image/png").
    With no mime, text is preferred, then image/png if image_ok."""
    with audit.event("clipboard_get", {"selection": selection, "type": mime}, kind="observation") as ev:
        targets = clipboard_targets(selection)
        if not targets:
            raise CuError(f"the {_label(selection)} is empty")
        if mime:
            if mime not in targets:
                raise CuError(f"the {_label(selection)} has no {mime}; it holds: {' '.join(targets)}")
            target = mime
        else:
            target = next((t for t in TEXT_TARGETS if t in targets), None)
            if target is None and image_ok and "image/png" in targets:
                target = "image/png"
            if target is None:
                hint = " (save it with: ccu clip get --type image/png > FILE.png)" if "image/png" in targets else ""
                raise CuError(f"the {_label(selection)} holds no text, only: {' '.join(targets)}{hint}")
        proc = subprocess.run(["xclip", "-selection", selection, "-t", target, "-o"],
                              env=_env(), capture_output=True, timeout=30)
        if proc.returncode != 0:
            raise CuError(f"xclip failed ({proc.returncode}): {proc.stderr.decode(errors='replace').strip()}")
        data = proc.stdout
        if target in TEXT_TARGETS:
            data, target = data.decode("utf-8", errors="replace"), "text"
        ev["result"] = {"type": target, "data": _audit_data(data)}
        return data, target


def clipboard_set(data, selection="clipboard", mime=None):
    """Own the selection with `data` (str, or bytes with a mime type such as
    image/png). Stays set after this process exits."""
    if isinstance(data, str):
        data, mime = data.encode(), mime or "UTF8_STRING"
    elif not mime:
        raise CuError("binary clipboard data needs a type, e.g. image/png")
    if mime == "image/png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise CuError("data is not a PNG image")
    args = {"selection": selection, "type": mime,
            "data": _audit_data(data.decode(errors="replace") if mime == "UTF8_STRING" else data)}
    with audit.event("clipboard_set", args, kind="action", shot=False):
        # xclip reads all input, takes the selection, then forks a server; the
        # parent exits. The server must not hold our stdout/stderr (podman exec
        # would wait for it) and gets its own session so it outlives us.
        proc = subprocess.Popen(["xclip", "-selection", _selection(selection), "-t", mime, "-i"],
                                env=_env(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
        proc.communicate(data, timeout=10)
        if proc.returncode != 0:
            raise CuError(f"xclip failed ({proc.returncode})")


def wait_for_window(pattern, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for wid, pid, title in list_windows():
            if pattern.lower() in title.lower():
                return wid, title
        time.sleep(0.5)
    raise CuError(f"timed out after {timeout:g}s waiting for window matching {pattern!r}")


# --- secrets -------------------------------------------------------------------
# `ccu secret` on the host reads a Keychain item and pipes it to `cu secret` on
# stdin. The value never goes on a command line, into the audit log (only its
# key and length), into a file, or onto the clipboard.

def type_secret(name, secret, delay_ms=12):
    """Type `secret` into the focused window. Logged as key name and length only,
    whatever CCU_AUDIT_TYPE says, and without an audit screenshot."""
    if not secret:
        raise CuError(f"secret {name!r} is empty")
    if "\n" in secret or "\r" in secret:
        raise CuError(f"secret {name!r} contains a line break, which would press Return; not typing it")
    with audit.event("secret_type", {"key": name, "len": len(secret)}, kind="action", shot=False):
        # --file - reads the text from stdin, so it isn't in xdotool's argv.
        proc = subprocess.run(["xdotool", "type", "--delay", str(delay_ms), "--file", "-"],
                              input=secret.encode(), env=_env(), capture_output=True)
        if proc.returncode != 0:
            raise CuError(f"xdotool type failed ({proc.returncode})")


def _scrub(src, dst, secret, mark, tails, key):
    # Copy src to dst with every occurrence of secret replaced by mark. Keeps
    # len(secret)-1 bytes back so a value split across reads is still caught.
    keep, carry, tail = len(secret) - 1, b"", b""
    for chunk in iter(lambda: src.read1(65536), b""):
        buf = (carry + chunk).replace(secret, mark)
        cut = max(0, len(buf) - keep)
        out, carry = buf[:cut], buf[cut:]
        dst.write(out)
        dst.flush()
        tail = (tail + out)[-audit.TAIL_CHARS:]
    dst.write(carry)
    dst.flush()
    tails[key] = (tail + carry)[-audit.TAIL_CHARS:].decode(errors="replace")


def secret_env(name, var, secret, argv):
    """Run argv with the secret in environment variable `var` and stdin closed.
    The value is replaced by "[secret NAME]" in its output and in the log."""
    import threading

    if not secret:
        raise CuError(f"secret {name!r} is empty")
    env = dict(_env(), **{var: secret})
    mark, raw = f"[secret {name}]".encode(), secret.encode()
    with audit.event("secret_env", {"key": name, "var": var, "len": len(secret), "argv": list(argv)},
                     shot=False) as ev:
        try:
            proc = subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            raise CuError(f"cannot run {argv[0]}: {e.strerror}")
        tails = {}
        threads = [threading.Thread(target=_scrub, args=(proc.stdout, sys.stdout.buffer, raw, mark, tails, "stdout")),
                   threading.Thread(target=_scrub, args=(proc.stderr, sys.stderr.buffer, raw, mark, tails, "stderr"))]
        for t in threads:
            t.start()
        rc = proc.wait()
        for t in threads:
            t.join()
        ev.update(ok=rc == 0, result={"exit": rc, **tails})
    return rc


def _keychain_value(data):
    # `security find-generic-password -g` prints the value on stderr as
    # `password: "TEXT"` for printable ASCII and `password: 0xHEX  "..."` for
    # anything else (where -w would print bare hex, indistinguishable from text).
    for line in data.split(b"\n"):
        if line.startswith(b"password: "):
            v = line[len(b"password: "):]
            if v.startswith(b"0x"):
                try:
                    return bytes.fromhex(v[2:].split(b" ", 1)[0].decode())
                except ValueError:
                    break
            if len(v) >= 2 and v[:1] == v[-1:] == b'"':
                return v[1:-1]
            break
    raise CuError("could not read the value from the Keychain output")


def _secret_main(argv):
    """cu secret [--keychain] type KEY | cu secret [--keychain] env KEY VAR -- CMD...
    The value comes on stdin: raw (one trailing newline is dropped) or, with
    --keychain, as the stderr of `security find-generic-password -g`."""
    usage = "usage: cu secret [--keychain] type KEY | env KEY VAR -- CMD...  (the value on stdin)"
    keychain = bool(argv) and argv[0] == "--keychain"
    if keychain:
        argv = argv[1:]
    if len(argv) < 2 or argv[0] not in ("type", "env"):
        print(usage, file=sys.stderr)
        return 2
    if sys.stdin.isatty():
        print("cu: secret reads the value from stdin; use ccu secret on the host", file=sys.stderr)
        return 2
    data = sys.stdin.buffer.read()
    try:
        if keychain:
            data = _keychain_value(data)
        elif data.endswith(b"\n"):
            data = data[:-1]
        try:
            secret = data.decode("utf-8")
        except UnicodeDecodeError:
            raise CuError("the secret is not UTF-8 text")
        if argv[0] == "type":
            if len(argv) != 2:
                raise CuError(usage)
            type_secret(argv[1], secret)
            return 0
        if len(argv) < 5 or argv[3] != "--" or not argv[2].isidentifier():
            raise CuError(usage)
        return secret_env(argv[1], argv[2], secret, argv[4:])
    except CuError as e:
        print(f"cu: {e}", file=sys.stderr)
        return 1


# Let the audit log read the active window and reach the display.
audit.window_title, audit.x_env, audit.screen_size = active_window, _env, screen_size


# --- CLI -----------------------------------------------------------------------

def _write_png(data, out):
    if out in (None, "-"):
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    else:
        with open(out, "wb") as f:
            f.write(data)
        print(out)


def _clip_cli(a):
    sel = "primary" if a.primary else "clipboard"
    if a.action == "get":
        data, _ = clipboard_get(sel, a.mime)
        sys.stdout.buffer.write(data.encode() if isinstance(data, str) else data)
        sys.stdout.buffer.flush()
        return
    if a.action == "targets":
        for t in clipboard_targets(sel):
            print(t)
        return
    data = a.text
    if a.text == "-":
        data = sys.stdin.buffer.read()
        if not a.mime:
            try:
                data = data.decode("utf-8")
            except UnicodeDecodeError:
                raise CuError("stdin is not UTF-8 text; for binary data pass --type, e.g. --type image/png")
    if a.action == "paste" and a.primary:
        raise CuError("paste uses the clipboard; for PRIMARY, set it and middle-click instead")
    clipboard_set(data, sel, a.mime)
    if a.action == "paste":
        key(a.key)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("exec", "record", "audit"):
        return audit.main(argv)  # see ccu_audit.py
    if argv and argv[0] == "secret":
        return _secret_main(argv[1:])
    p = argparse.ArgumentParser(prog="cu", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("screenshot", help="capture the screen as PNG")
    s.add_argument("-o", "--out", help="output file (default: PNG on stdout)")
    s.add_argument("--no-cursor", action="store_true")

    s = sub.add_parser("zoom", help="capture and enlarge a region x0 y0 x1 y1")
    for a in ("x0", "y0", "x1", "y1"):
        s.add_argument(a, type=int)
    s.add_argument("--scale", type=float, default=2.0)
    s.add_argument("-o", "--out")

    sub.add_parser("size", help="print screen width and height")
    sub.add_parser("cursor", help="print mouse x y")
    sub.add_parser("windows", help="list windows: id pid title")
    sub.add_parser("active", help="print the active window title")

    s = sub.add_parser("move", help="move the mouse")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)

    for name, default_count, default_button in (
        ("click", 1, "left"),
        ("double_click", 2, "left"),
        ("triple_click", 3, "left"),
        ("right_click", 1, "right"),
        ("middle_click", 1, "middle"),
    ):
        s = sub.add_parser(name, help=f"{name.replace('_', ' ')} at x y (or current position)")
        s.add_argument("x", type=int, nargs="?")
        s.add_argument("y", type=int, nargs="?")
        s.add_argument("--button", default=default_button, choices=BUTTONS)
        s.add_argument("--count", type=int, default=default_count)
        s.add_argument("--mods", help='modifiers held during the click, e.g. "ctrl" or "ctrl+shift"')

    s = sub.add_parser("mouse_down", help="press and hold a mouse button")
    s.add_argument("--button", default="left", choices=BUTTONS)
    s = sub.add_parser("mouse_up", help="release a mouse button")
    s.add_argument("--button", default="left", choices=BUTTONS)

    s = sub.add_parser("drag", help="drag from x0 y0 to x1 y1")
    for a in ("x0", "y0", "x1", "y1"):
        s.add_argument(a, type=int)
    s.add_argument("--button", default="left", choices=BUTTONS)

    s = sub.add_parser("scroll", help="scroll at x y")
    s.add_argument("x", type=int)
    s.add_argument("y", type=int)
    s.add_argument("direction", choices=SCROLL_BUTTONS)
    s.add_argument("amount", type=int, nargs="?", default=3)

    s = sub.add_parser("type", help="type text (newlines press Return)")
    s.add_argument("text")
    s.add_argument("--delay", type=int, default=12, help="ms between keystrokes")

    s = sub.add_parser("key", help='press key combos, e.g. ctrl+s Return "ctrl+shift+t"')
    s.add_argument("combos", nargs="+")

    s = sub.add_parser("launch", help="start a GUI app in the background")
    s.add_argument("command", nargs=argparse.REMAINDER)

    s = sub.add_parser("focus", help="activate the window whose title contains PATTERN")
    s.add_argument("pattern")

    s = sub.add_parser("wait_window", help="wait until a window title contains PATTERN")
    s.add_argument("pattern")
    s.add_argument("--timeout", type=float, default=30.0)

    s = sub.add_parser("wait", help="sleep for N seconds")
    s.add_argument("seconds", type=float)

    s = sub.add_parser("clip", help="clipboard: get | set TEXT|- | paste TEXT|- | targets")
    clip = s.add_subparsers(dest="action", required=True)
    for name, hlp in (("get", "print the clipboard"), ("set", "set the clipboard to TEXT (- reads stdin)"),
                      ("paste", "set the clipboard, then press ctrl+v"), ("targets", "list the data types it holds")):
        c = clip.add_parser(name, help=hlp)
        c.add_argument("--primary", action="store_true", help="use the PRIMARY selection (middle-click paste)")
        if name != "targets":
            c.add_argument("--type", dest="mime", help="data type such as image/png (raw bytes on stdin/stdout)")
        if name in ("set", "paste"):
            c.add_argument("text", help="the text, or - to read it from stdin")
        if name == "paste":
            c.add_argument("--key", default="ctrl+v", help="key combo that pastes (ctrl+shift+v in terminals)")

    a = p.parse_args(argv)
    try:
        if a.cmd == "screenshot":
            _write_png(screenshot_png(cursor=not a.no_cursor), a.out)
        elif a.cmd == "zoom":
            _write_png(zoom_png(a.x0, a.y0, a.x1, a.y1, a.scale), a.out)
        elif a.cmd == "size":
            print(*screen_size())
        elif a.cmd == "cursor":
            print(*cursor_position())
        elif a.cmd == "windows":
            for row in list_windows():
                print(*row, sep="\t")
        elif a.cmd == "active":
            print(active_window())
        elif a.cmd == "move":
            move(a.x, a.y)
        elif a.cmd in ("click", "double_click", "triple_click", "right_click", "middle_click"):
            if (a.x is None) != (a.y is None):
                raise CuError("give both x and y, or neither")
            click(a.x, a.y, a.button, a.count, a.mods)
        elif a.cmd == "mouse_down":
            mouse_down(a.button)
        elif a.cmd == "mouse_up":
            mouse_up(a.button)
        elif a.cmd == "drag":
            drag(a.x0, a.y0, a.x1, a.y1, a.button)
        elif a.cmd == "scroll":
            scroll(a.x, a.y, a.direction, a.amount)
        elif a.cmd == "type":
            type_text(a.text, a.delay)
        elif a.cmd == "key":
            key(*a.combos)
        elif a.cmd == "launch":
            if not a.command:
                raise CuError("launch needs a command")
            pid, log = launch(shlex.join(a.command) if len(a.command) > 1 else a.command[0])
            print(f"pid {pid}, log {log}")
        elif a.cmd == "focus":
            focus(a.pattern)
        elif a.cmd == "wait_window":
            wid, title = wait_for_window(a.pattern, a.timeout)
            print(wid, title, sep="\t")
        elif a.cmd == "wait":
            time.sleep(a.seconds)
        elif a.cmd == "clip":
            _clip_cli(a)
    except CuError as e:
        print(f"cu: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
