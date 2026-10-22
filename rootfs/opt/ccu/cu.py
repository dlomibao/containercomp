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


def wait_for_window(pattern, timeout=30.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        for wid, pid, title in list_windows():
            if pattern.lower() in title.lower():
                return wid, title
        time.sleep(0.5)
    raise CuError(f"timed out after {timeout:g}s waiting for window matching {pattern!r}")


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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("exec", "record", "audit"):
        return audit.main(argv)  # see ccu_audit.py
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
    except CuError as e:
        print(f"cu: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
