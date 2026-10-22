"""MCP server exposing the container desktop as computer-use tools.

Runs inside the container over stdio. From the host:

    claude mcp add containercomp -- ccu mcp
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, "/opt/ccu")
# Tag audit log entries from this server (and from commands its shell tool runs).
os.environ["CCU_AUDIT_SOURCE"] = "mcp"

import ccu_audit as audit  # noqa: E402
import cu  # noqa: E402
from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

mcp = FastMCP(
    "containercomp",
    instructions=(
        "Controls a Linux desktop (Ubuntu 24.04, XFCE) running in a container. "
        "Call screenshot first, then act using pixel coordinates from that screenshot. "
        "Take another screenshot after actions to verify the result. Prefer keyboard "
        "shortcuts when reliable. Use zoom to read small text. For GTK, Qt and "
        "Chromium/Electron apps, ui_tree (scoped by app/role/name) lists buttons, "
        "fields and menu items with their positions and states for far fewer tokens, "
        "and click_element clicks one by name; fall back to pixels when it lists nothing."
    ),
    log_level="WARNING",
)

SETTLE_SECONDS = 0.6


def _shot():
    return Image(data=cu.screenshot_png(), format="png")


def _done(msg, screenshot):
    if not screenshot:
        return msg
    time.sleep(SETTLE_SECONDS)
    return [msg, _shot()]


@mcp.tool()
def screenshot() -> Image:
    """Capture the full screen. Pixel coordinates in this image are clickable as-is."""
    return _shot()


@mcp.tool()
def zoom(x0: int, y0: int, x1: int, y1: int, scale: float = 2.0) -> Image:
    """Capture the rectangle (x0,y0)-(x1,y1) enlarged by `scale` to read small text.
    Coordinates in the returned image are NOT screen coordinates."""
    return Image(data=cu.zoom_png(x0, y0, x1, y1, scale), format="png")


@mcp.tool()
def screen_info() -> str:
    """Screen size, mouse position, active window and open windows."""
    w, h = cu.screen_size()
    x, y = cu.cursor_position()
    wins = "\n".join(f"  {wid} pid={pid} {title}" for wid, pid, title in cu.list_windows())
    return f"screen {w}x{h}\ncursor {x},{y}\nactive: {cu.active_window()}\nwindows:\n{wins or '  (none)'}"


@mcp.tool()
def click(
    x: int,
    y: int,
    button: str = "left",
    count: int = 1,
    modifiers: str = "",
    screenshot: bool = False,
):
    """Click at (x, y). button: left|middle|right. count: 2 for double-click, 3 for triple.
    modifiers: keys held during the click, e.g. "ctrl" or "ctrl+shift"."""
    cu.click(x, y, button, count, modifiers)
    return _done(f"clicked {button} x{count} at {x},{y}", screenshot)


@mcp.tool()
def move_mouse(x: int, y: int, screenshot: bool = False):
    """Move the mouse to (x, y), e.g. to reveal a hover menu or tooltip."""
    cu.move(x, y)
    return _done(f"moved to {x},{y}", screenshot)


@mcp.tool()
def drag(x0: int, y0: int, x1: int, y1: int, button: str = "left", screenshot: bool = False):
    """Press at (x0, y0), drag to (x1, y1), release."""
    cu.drag(x0, y0, x1, y1, button)
    return _done(f"dragged {x0},{y0} -> {x1},{y1}", screenshot)


@mcp.tool()
def scroll(x: int, y: int, direction: str = "down", amount: int = 3, screenshot: bool = False):
    """Scroll at (x, y). direction: up|down|left|right. amount: wheel clicks."""
    cu.scroll(x, y, direction, amount)
    return _done(f"scrolled {direction} {amount} at {x},{y}", screenshot)


@mcp.tool()
def type_text(text: str, screenshot: bool = False):
    """Type text into the focused window. Newlines press Return."""
    cu.type_text(text)
    return _done(f"typed {len(text)} chars", screenshot)


@mcp.tool()
def key(combos: list[str], screenshot: bool = False):
    """Press key combos in order, xdotool syntax: ["ctrl+s"], ["Return"], ["alt+F4"],
    ["ctrl+shift+t"], ["Tab", "Tab", "space"]. Names: Return, Escape, Tab, BackSpace,
    Delete, Up, Down, Left, Right, Home, End, Page_Up, Page_Down, F1..F12, super."""
    cu.key(*combos)
    return _done(f"pressed {' '.join(combos)}", screenshot)


@mcp.tool()
def launch(command: str, wait_for_window: str = "", timeout: float = 30.0, screenshot: bool = True):
    """Start a GUI app in the background (shell command, e.g. "mousepad" or
    "wine ~/apps/app.exe"). Optionally wait until a window title contains
    `wait_for_window`."""
    pid, log = cu.launch(command)
    msg = f"launched pid {pid} (log {log})"
    if wait_for_window:
        wid, title = cu.wait_for_window(wait_for_window, timeout)
        msg += f"; window {wid} '{title}' is open"
    else:
        time.sleep(2)
    return _done(msg, screenshot)


@mcp.tool()
def focus_window(title_contains: str, screenshot: bool = False):
    """Bring the first window whose title contains the given text to the front."""
    cu.focus(title_contains)
    return _done(f"focused '{title_contains}'", screenshot)


@mcp.tool()
def install(targets: list[str]) -> str:
    """Install apps inside the container (root via sudo). Each target is an apt
    package name, a URL, or a path inside the container to a .deb, .AppImage,
    .exe/.msi (Wine, x86 image only), or archive. Files on the Mac host must be
    copied in first with `ccu install <host path>` instead."""
    with audit.event("install", {"targets": targets}) as ev:
        proc = subprocess.run(
            ["sudo", "/usr/local/bin/ccu-install", *targets],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        tail = (proc.stdout + proc.stderr)[-4000:]
        ev.update(ok=proc.returncode == 0, result={"exit": proc.returncode, "output": tail})
    return f"exit {proc.returncode}\n{tail}"


@mcp.tool()
def shell(command: str, timeout: float = 120.0) -> str:
    """Run a non-GUI shell command as the desktop user (use sudo for root). Use
    `launch` for GUI apps instead, since this waits for the command to exit."""
    with audit.event("shell", {"command": command}) as ev:
        proc = subprocess.run(
            ["bash", "-lc", command], capture_output=True, text=True, timeout=timeout, env=cu._env()
        )
        out = (proc.stdout + proc.stderr)[-8000:]
        ev.update(ok=proc.returncode == 0, result={"exit": proc.returncode, "output": out})
    return f"exit {proc.returncode}\n{out}"


@mcp.tool()
def ui_tree(
    app: str = "",
    role: str = "",
    name: str = "",
    limit: int = 100,
    include_hidden: bool = False,
    all_nodes: bool = False,
) -> str:
    """List UI elements from the accessibility tree, one compact line each:
    role "name" @CX,CY WxH [states] text="...". @CX,CY is the element's centre in
    screen pixels (the same as screenshot pixels). Far cheaper than a screenshot
    when scoped: pass app (e.g. "mousepad"; see screen_info for windows), role
    (push_button, menu_item, check_box, text, ...; "button" matches every
    *_button) and/or name (case-insensitive substring). Works for GTK and Qt
    apps, and Chromium/Electron apps launched with --force-renderer-accessibility;
    plain X11 apps (and most likely Wine apps) show nothing, so use screenshots
    for those."""
    els = cu.elements(app or None, role or None, name or None, not include_hidden, limit, all_nodes)
    return cu.format_tree(els, limit)


@mcp.tool()
def find_element(name: str, role: str = "", app: str = "", timeout: float = 0.0,
                 index: int | None = None) -> str:
    """Return the one element whose name matches (case-insensitive; an exact
    name wins over substrings), waiting up to `timeout` seconds for it to
    appear. Errors list the candidates when several match; narrow with
    role/app or pick one with index (0-based)."""
    return cu.format_element(cu.find_element(name, role or None, app or None, timeout, index), with_app=True)


@mcp.tool()
def click_element(
    name: str,
    role: str = "",
    app: str = "",
    button: str = "left",
    count: int = 1,
    index: int | None = None,
    timeout: float = 0.0,
    use_action: bool = False,
    screenshot: bool = False,
):
    """Click the centre of the element whose name matches (as find_element),
    with the real mouse. Prefer this to pixel clicks when ui_tree lists the
    element. count: 2 for a double-click. use_action=True invokes the
    element's accessibility action instead (works when it is covered or off
    the screen, but sends no mouse events)."""
    el = cu.click_element(name, role or None, app or None, button, count, index, timeout, use_action)
    verb = "invoked" if use_action else f"clicked {button} x{count}"
    return _done(f"{verb} {cu.format_element(el, with_app=True)}", screenshot)


@mcp.tool()
def wait(seconds: float = 1.0, screenshot: bool = True):
    """Pause, e.g. while an app loads, then optionally screenshot."""
    time.sleep(seconds)
    return _done(f"waited {seconds}s", screenshot)


if __name__ == "__main__":
    mcp.run()
