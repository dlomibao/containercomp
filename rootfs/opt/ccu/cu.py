#!/usr/bin/env python3
"""cu: drive the container's X display (mouse, keyboard, screenshots).

Used directly as a CLI inside the container (and by the host `ccu` script via
`podman exec`), and imported as a library by mcp_server.py.

Coordinates are screen pixels with (0, 0) at the top-left. Screenshots are
taken at the native screen resolution, so pixel positions in a screenshot are
directly clickable. Apps with an accessibility tree can also be driven by
element name (tree, find, click_element), in the same screen coordinates.
"""
import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import warnings

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


# --- accessibility tree (AT-SPI) ---------------------------------------------------
#
# Apps that support AT-SPI (GTK3, and Qt/Chromium with the env set in the
# Containerfile) publish their widgets with role, name, state and screen
# extents. These functions read that tree so an agent can act on "the Save
# button" instead of a pixel guessed from a screenshot.
#
# Matching (find_element, click_element, elements(name=...)): names are
# compared case-insensitively with whitespace collapsed and "…" read as "...".
# An exact match wins; only if nothing matches exactly do substring matches
# count. Roles are AT-SPI role names with "_" for spaces ("push_button",
# "menu_item", "check_box", "text"); a role filter matches the role itself or
# its last word(s), so "button" matches push_button and toggle_button.

TEXT_MAX = 200          # characters of an element's text to return
TREE_LIMIT = 300        # elements returned by default
TREE_MAX_NODES = 5000   # nodes visited per walk, whatever the filters
TREE_MAX_DEPTH = 40

# Controls worth listing even with no name and no text (an empty entry, a
# button with only an icon). Other unnamed, textless nodes (filler, panel,
# scroll_pane, the cell that wraps a row's cells, ...) are layout and are left
# out unless all_nodes=True.
_UNNAMED_OK = {
    "push_button", "toggle_button", "check_box", "radio_button", "menu_item",
    "check_menu_item", "radio_menu_item", "combo_box", "text", "entry",
    "password_text", "spin_button", "slider", "link", "page_tab", "terminal",
    "document_text", "document_web", "editbar",
}
# States shown in compact output; "disabled" is added when "enabled" is absent.
_NOTABLE = ("focused", "checked", "pressed", "selected", "expanded", "indeterminate",
            "active", "editable", "modal")

_atspi_mod = None


def _atspi():
    """Import and initialise libatspi on first use (it needs the session bus)."""
    global _atspi_mod
    if _atspi_mod is None:
        env = _env()
        for k in ("DISPLAY", "DBUS_SESSION_BUS_ADDRESS"):
            if env.get(k):
                os.environ[k] = env[k]
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi
        except (ImportError, ValueError) as e:
            raise CuError(f"AT-SPI support is missing ({e}); rebuild the image (ccu build) "
                          "to get python3-gi and gir1.2-atspi-2.0")
        Atspi.init()
        # Fail fast on a hung app instead of the 25s D-Bus default.
        Atspi.set_timeout(3000, 15000)
        _atspi_mod = Atspi
    # A long-lived process (the MCP server) has no main loop running, so apply
    # queued change events now and drop cached nodes before each walk.
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    for _ in range(10000):
        if not ctx.iteration(False):
            break
    return _atspi_mod


def _norm(s):
    return " ".join((s or "").replace("…", "...").split()).casefold()


def _norm_role(r):
    return "_".join((r or "").lower().replace("-", " ").split())


def _role_ok(role, want):
    want = _norm_role(want)
    return role == want or role.endswith("_" + want)


def _name_tier(name, want):
    """0 = exact match, 1 = substring match, None = no match."""
    n, w = _norm(name), _norm(want)
    if n == w:
        return 0
    if w and w in n:
        return 1
    return None


def _node_info(Atspi, node, role, origin=None, text_max=TEXT_MAX):
    """Screen extents and text of one node; keys are missing if it has none.
    With `origin` (x, y), extents are read relative to the window and moved
    by it (for toolkits that can't report screen positions, see _origin).

    Interface methods are called through their class (Atspi.Text.get_text):
    the same names on Atspi.Accessible are deprecated getters."""
    out = {}
    try:
        ifaces = node.get_interfaces()
    except Exception:  # noqa: BLE001
        return out
    if "Component" in ifaces:
        with contextlib.suppress(Exception):  # app quit or call unsupported
            if origin is None:
                r = Atspi.Component.get_extents(node, Atspi.CoordType.SCREEN)
                out.update(x=r.x, y=r.y, w=r.width, h=r.height)
            else:
                r = Atspi.Component.get_extents(node, Atspi.CoordType.WINDOW)
                out.update(x=r.x + origin[0], y=r.y + origin[1], w=r.width, h=r.height)
    if "Text" in ifaces:
        with contextlib.suppress(Exception):  # app quit or call unsupported
            n = Atspi.Text.get_character_count(node)
            if n > 0 and role == "terminal":
                # The end is the interesting part (the latest output); drop the
                # blank rows and padding a terminal reports below it.
                t = Atspi.Text.get_text(node, max(0, n - text_max * 10), n)
                t = "\n".join(line.rstrip() for line in t.splitlines()).strip()
                out["text"] = ("..." + t[-text_max:]) if len(t) > text_max else t
            elif n > 0:
                t = Atspi.Text.get_text(node, 0, min(n, text_max))
                out["text"] = t + ("..." if n > text_max else "")
        # U+FFFC stands for embedded child objects (web pages): not text.
        if "￼" in out.get("text", ""):
            out["text"] = out["text"].replace("￼", "")
            if not out["text"].strip():
                del out["text"]
    return out



def _label_of(Atspi, node):
    # An unnamed control's visible label ("Search for:" next to an entry), as
    # the toolkit links them; screen readers announce it the same way.
    with contextlib.suppress(Exception):
        for rel in node.get_relation_set():
            if rel.get_relation_type() == Atspi.RelationType.LABELLED_BY and rel.get_n_targets():
                return (rel.get_target(0).get_name() or "").strip()
    return ""


def _origin(pid, title):
    """Screen position of the X window with this pid and title, or None.

    GTK 4 reports 0,0 for every SCREEN position on X11 (it only knows
    positions inside its windows), so its elements are placed by their
    WINDOW position plus their window's origin. Popovers are separate
    surfaces and may come out misplaced."""
    for wid, wpid, wtitle in list_windows():
        if wpid == str(pid) and wtitle == title:
            vals = dict(line.split("=", 1) for line in xdotool("getwindowgeometry", "--shell", wid).split())
            return int(vals["X"]), int(vals["Y"])
    return None


def _walk(Atspi, app_filter, role, name, visible_only, limit, all_nodes, max_nodes, max_depth,
          onscreen=True):
    # visible_only: skip nodes (and their subtrees) that aren't showing, and
    # with onscreen also those whose box is off the screen.
    sw, sh = screen_size()
    results, visited = [], [0]
    desktop = Atspi.get_desktop(0)
    desktop.clear_cache()

    def visit(node, depth, app, pid, window, parent=("", ""), origin=None, gtk4=False):
        if len(results) >= limit or visited[0] >= max_nodes or depth > max_depth:
            return
        visited[0] += 1
        try:
            r = _norm_role(node.get_role_name())
            sset = node.get_state_set()
            states = [s.value_nick.replace("-", "_") for s in sset.get_states()]
        except Exception:  # noqa: BLE001 - app went away or does not answer
            return
        if visible_only and not ("showing" in states and "visible" in states):
            return
        nm = (node.get_name() or "").strip()
        if not nm and r in _UNNAMED_OK:
            # The entry inside an editable combo box goes by the combo's label.
            nm = parent[1] if parent[0] == "combo_box" else _label_of(Atspi, node)
        if depth == 1:
            window = nm
            if gtk4:
                origin = _origin(pid, nm)
        if (role is None or _role_ok(r, role)) and (name is None or _name_tier(nm, name) is not None):
            info = _node_info(Atspi, node, r, origin)
            keep = all_nodes or nm or info.get("text") or r in _UNNAMED_OK
            # A button's own caption (GTK 4, web pages) would only repeat it.
            if keep and not all_nodes and r in ("label", "static") and nm and nm == parent[1]:
                keep = False
            if keep and (not visible_only or not onscreen or (
                    info.get("w", 0) > 0 and info.get("h", 0) > 0
                    and info["x"] < sw and info["y"] < sh
                    and info["x"] + info["w"] > 0 and info["y"] + info["h"] > 0)):
                el = {"app": app, "pid": pid, "window": window, "role": r, "name": nm,
                      "x": info.get("x", 0), "y": info.get("y", 0), "w": info.get("w", 0),
                      "h": info.get("h", 0), "states": states, "depth": depth}
                if "text" in info:
                    el["text"] = info["text"]
                results.append((el, node))
        try:
            n = node.get_child_count()
        except Exception:  # noqa: BLE001
            return
        for i in range(n):
            if len(results) >= limit or visited[0] >= max_nodes:
                return
            try:
                child = node.get_child_at_index(i)
            except Exception:  # noqa: BLE001
                continue
            if child is not None:
                visit(child, depth + 1, app, pid, window, (r, nm), origin)

    for i in range(desktop.get_child_count()):
        try:
            app = desktop.get_child_at_index(i)
            app_name = app.get_name() or ""
        except Exception:  # noqa: BLE001
            continue
        if app is None or (app_filter and _norm(app_filter) not in _norm(app_name)):
            continue
        try:
            pid = app.get_process_id()
        except Exception:  # noqa: BLE001
            pid = 0
        gtk4 = False
        with contextlib.suppress(Exception):
            gtk4 = app.get_toolkit_name() == "GTK" and app.get_toolkit_version().startswith("4.")
        for j in range(app.get_child_count()):
            try:
                top = app.get_child_at_index(j)
            except Exception:  # noqa: BLE001
                continue
            if top is not None:
                visit(top, 1, app_name, pid, "", gtk4=gtk4)
    return results, visited[0]


def accessible_apps():
    """Names (and pids) of the apps registered on the AT-SPI bus."""
    Atspi = _atspi()
    desktop = Atspi.get_desktop(0)
    desktop.clear_cache()
    out = []
    for i in range(desktop.get_child_count()):
        try:
            app = desktop.get_child_at_index(i)
            out.append((app.get_name() or "", app.get_process_id(), app.get_child_count()))
        except Exception:  # noqa: BLE001
            continue
    return out


def elements(app=None, role=None, name=None, visible_only=True, limit=TREE_LIMIT,
             all_nodes=False, max_nodes=TREE_MAX_NODES, max_depth=TREE_MAX_DEPTH):
    """List UI elements from the accessibility tree, in tree order.

    Each is a dict: app, pid, window (its top-level window's title), role,
    name, x, y, w, h (screen pixels, the same as screenshot pixels), states
    (e.g. ["enabled", "focusable", "focused", "showing", "visible"]), depth,
    and text (first TEXT_MAX characters, or the last ones for a terminal) when
    the element exposes text. `app` is a case-insensitive substring of the
    app's AT-SPI name (usually its program name: "mousepad", "xfce4-panel").
    `name` keeps elements whose name contains it (case-insensitive). Unnamed
    layout containers are skipped unless all_nodes=True. visible_only drops
    elements that aren't showing on screen.
    """
    return [el for el, _ in _elements(app, role, name, visible_only, limit, all_nodes,
                                      max_nodes, max_depth)]


def _elements(app=None, role=None, name=None, visible_only=True, limit=TREE_LIMIT,
              all_nodes=False, max_nodes=TREE_MAX_NODES, max_depth=TREE_MAX_DEPTH, onscreen=True):
    # [(element dict, live Atspi node)], logged as one ui_tree observation.
    args = {"app": app, "role": role, "name": name, "visible_only": visible_only,
            "limit": limit, "all_nodes": all_nodes}
    with audit.event("ui_tree", args, kind="observation") as ev:
        Atspi = _atspi()
        started = time.time()
        out, visited = _walk(Atspi, app, role, name, visible_only, limit, all_nodes,
                             max_nodes, max_depth, onscreen)
        ev["result"] = {"count": len(out), "visited": visited,
                        "ms": int((time.time() - started) * 1000)}
    return out


def _describe(els, n=5):
    return "; ".join(format_element(e, with_app=True) for e in els[:n]) + (" ..." if len(els) > n else "")


@audit.audited("observation")
def find_element(name, role=None, app=None, timeout=0.0, index=None):
    """Return the one visible element matching `name` (see the matching rules
    above), waiting up to `timeout` seconds for it to appear. Raises CuError if
    nothing matches, or if several do and `index` (0-based, in tree order)
    doesn't pick one."""
    return _find(name, role, app, timeout, index)[0]


def _find(name, role, app, timeout, index, onscreen=True):
    deadline = time.time() + max(0.0, timeout or 0.0)
    while True:
        found = _elements(app=app, role=role, name=name, limit=TREE_MAX_NODES, onscreen=onscreen)
        tiers = [(_name_tier(e["name"], name), e, node) for e, node in found]
        best = min((t for t, _, _ in tiers), default=None)
        cands = [(e, node) for t, e, node in tiers if t == best] if best is not None else []
        if len(cands) == 1 and index in (None, 0):
            return cands[0]
        if cands and index is not None and 0 <= index < len(cands):
            return cands[index]
        if cands and index is None:
            raise CuError(f"{len(cands)} elements match {name!r} "
                          f"({'exactly' if best == 0 else 'as a substring'}); narrow it with "
                          f"role/app or pick one with index (0-based): {_describe([e for e, _ in cands])}")
        if time.time() >= deadline:
            what = f"{name!r}" + (f" role={role}" if role else "") + (f" app={app}" if app else "")
            if cands:
                raise CuError(f"index {index} is out of range: {len(cands)} elements match {what}")
            apps = sorted({a for a, _, _ in accessible_apps() if a})
            hint = ""
            if not apps:
                hint = " (no apps are on the accessibility bus; is the desktop up?)"
            elif app and not any(_norm(app) in _norm(a) for a in apps):
                hint = f" (no accessible app matches app={app!r}; apps: {', '.join(apps)})"
            elif onscreen and _elements(app=app, role=role, name=name, limit=1, onscreen=False):
                hint = (" on screen (one is showing but off the screen: scroll to it, or use "
                        "the AT-SPI action: click_element --action)")
            raise CuError(f"no visible element matches {what}"
                          + (f" after {timeout:g}s" if timeout else "") + hint)
        time.sleep(0.5)


_ACTIONS = ("click", "press", "activate", "toggle", "jump", "open")


@audit.audited()
def click_element(name, role=None, app=None, button="left", count=1, index=None,
                  timeout=0.0, action=False):
    """Find an element (as find_element) and click the centre of its on-screen
    box with the real mouse, as a user would. Nothing is raised or focused
    first, so the element must not be covered by another window.
    action=True instead invokes the element's AT-SPI action (click, press,
    activate, toggle...), which works even when it is covered or scrolled
    away, but sends no pointer events; button and count are ignored then.
    Returns the element as it was before the click."""
    # An action also reaches elements that are showing but off the screen
    # (e.g. the buttons of a dialog taller than the screen).
    el, node = _find(name, role, app, timeout, index, onscreen=not action)
    if action:
        _do_action(node, el)
        return el
    if el["w"] <= 0 or el["h"] <= 0:
        raise CuError(f"element has no on-screen box: {format_element(el, with_app=True)}")
    cx, cy = el["x"] + el["w"] // 2, el["y"] + el["h"] // 2
    move(cx, cy)
    _uncover(el)
    click(cx, cy, button, count)
    return el


def _pointer_window():
    """(window id, pid or None, title) of the top-level window under the pointer."""
    vals = dict(line.split("=", 1) for line in xdotool("getmouselocation", "--shell").split())
    wid = vals.get("WINDOW", "")
    try:
        pid = int(xdotool("getwindowpid", wid).strip())
    except (CuError, ValueError):
        pid = None
    try:
        title = xdotool("getwindowname", wid).strip()
    except CuError:
        title = ""
    return wid, pid, title


def _uncover(el):
    # The pointer is on the element's centre. If a window of another process
    # is on top there, raise the element's window (as a user would click it
    # first) and check again. Windows without _NET_WM_PID are trusted.
    wid, pid, title = _pointer_window()
    if pid is None or not el.get("pid") or pid == el["pid"]:
        return
    rows = [r for r in list_windows() if r[1] == str(el["pid"])]
    pick = next((r for r in rows if r[2] == el["window"]), rows[0] if rows else None)
    if pick:
        _run("wmctrl", "-i", "-a", pick[0], check=False)
        time.sleep(0.3)
        wid, pid, title = _pointer_window()
        if pid is None or pid == el["pid"]:
            return
    raise CuError(f"element is covered by window {title!r} (pid {pid}) and its own window could not "
                  f"be raised; use focus, or click_element --action: {format_element(el, with_app=True)}")


def _do_action(node, el):
    Atspi = _atspi()
    try:
        n = Atspi.Action.get_n_actions(node) if "Action" in node.get_interfaces() else 0
        # Its replacement, atspi_action_get_name, is not in the typelib.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            names = [(Atspi.Action.get_action_name(node, i) or "").lower() for i in range(n)]
    except Exception as e:  # noqa: BLE001
        raise CuError(f"element went away before its action could run ({e}): "
                      f"{format_element(el, with_app=True)}")
    if not n:
        raise CuError(f"element has no AT-SPI action; click it without --action: "
                      f"{format_element(el, with_app=True)}")
    i = next((names.index(a) for a in _ACTIONS if a in names), 0)
    if not Atspi.Action.do_action(node, i):
        raise CuError(f"AT-SPI action {names[i]!r} failed on {format_element(el, with_app=True)}")


def format_element(e, with_app=False):
    """One compact line: role "name" @cx,cy WxH [states] text="..."."""
    parts = []
    if with_app:
        parts.append(f"{e['app']}:")
    parts.append(e["role"])
    parts.append(json.dumps(e["name"], ensure_ascii=False))
    if e["w"] <= 1 and e["h"] <= 1 and e["x"] < -10000:
        parts.append("@none")  # GTK's extents for a widget that isn't mapped
    else:
        parts.append(f"@{e['x'] + e['w'] // 2},{e['y'] + e['h'] // 2} {e['w']}x{e['h']}")
    st = [s for s in _NOTABLE if s in e["states"]]
    if "enabled" not in e["states"] and "sensitive" not in e["states"]:
        st.append("disabled")
    if "expandable" in e["states"] and "expanded" not in e["states"]:
        st.append("collapsed")
    if st:
        parts.append("[" + ",".join(st) + "]")
    t = e.get("text")
    if t and _norm(t) != _norm(e["name"]):
        parts.append("text=" + json.dumps(t, ensure_ascii=False))
    return " ".join(parts)


def format_tree(els, limit=None):
    """Elements as compact lines grouped by app, for the CLI and MCP tools."""
    lines, last = [], None
    for e in els:
        if (e["app"], e["pid"]) != last:
            last = (e["app"], e["pid"])
            lines.append(f"# app {e['app'] or '?'} pid {e['pid']}")
        lines.append(format_element(e))
    if not els:
        lines.append("(no matching elements)")
    elif limit and len(els) >= limit:
        lines.append(f"(stopped at {limit} elements; narrow with app/role/name or raise the limit)")
    return "\n".join(lines)


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

    s = sub.add_parser("tree", help="list UI elements from the accessibility tree")
    s.add_argument("--app", help="app name contains this (case-insensitive), e.g. mousepad")
    s.add_argument("--role", help="role, e.g. push_button, menu_item, text, button")
    s.add_argument("--name", help="element name contains this (case-insensitive)")
    s.add_argument("--all", action="store_true", help="include unnamed layout containers")
    s.add_argument("--hidden", action="store_true", help="include elements not showing on screen")
    s.add_argument("--limit", type=int, default=TREE_LIMIT)
    s.add_argument("--json", action="store_true", help="one JSON object per line")
    s.add_argument("--apps", action="store_true", help="list the apps on the accessibility bus")

    for name, hlp in (("find", "print the one element matching NAME"),
                      ("click_element", "click the centre of the element matching NAME")):
        s = sub.add_parser(name, help=hlp)
        s.add_argument("name")
        s.add_argument("--role")
        s.add_argument("--app")
        s.add_argument("--index", type=int, help="pick the Nth (0-based) of several matches")
        s.add_argument("--timeout", type=float, default=0.0, help="wait up to S seconds for it")
        if name == "find":
            s.add_argument("--json", action="store_true")
        else:
            s.add_argument("--button", default="left", choices=BUTTONS)
            s.add_argument("--count", type=int, default=1, help="2 = double-click")
            s.add_argument("--action", action="store_true",
                           help="invoke its AT-SPI action instead of clicking with the mouse")

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
        elif a.cmd == "tree":
            if a.apps:
                for app, pid, n in accessible_apps():
                    print(app, pid, f"{n} window(s)", sep="\t")
                return 0
            els = elements(a.app, a.role, a.name, not a.hidden, a.limit, a.all)
            if a.json:
                for e in els:
                    print(json.dumps(e, ensure_ascii=False))
            else:
                print(format_tree(els, a.limit))
        elif a.cmd == "find":
            e = find_element(a.name, a.role, a.app, a.timeout, a.index)
            print(json.dumps(e, ensure_ascii=False) if a.json else format_element(e, with_app=True))
        elif a.cmd == "click_element":
            e = click_element(a.name, a.role, a.app, a.button, a.count, a.index, a.timeout, a.action)
            print(("invoked " if a.action else "clicked ") + format_element(e, with_app=True))
    except CuError as e:
        print(f"cu: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
