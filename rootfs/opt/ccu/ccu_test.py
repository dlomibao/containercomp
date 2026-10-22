#!/usr/bin/env python3
"""ccu test: run a desktop test spec in the container and record evidence.

The host command `ccu test run` resets the container from a snapshot, copies
the spec in, runs `ccu_test.py run`, and copies the run directory back out.
Each run directory holds result.json, report.html, a screenshot per step,
the audit log segment of the run (audit/) and, with --record, recording.mp4.

  ccu_test.py run SPEC --out DIR [--run N] [--reset DESC] [--host-from NAME] [--record]
  ccu_test.py validate SPEC...           (SPEC may be - for stdin)
  ccu_test.py report DIR                 DIR/*/result.json -> summary.json, junit.xml, index.html
  ccu_test.py draft [--since SEQ] [--from NAME]   audit log -> spec draft (YAML on stdout)

A spec is YAML (or JSON) with optional `setup`, `steps` and `checks` lists.
Each item is a mapping with one action key, e.g. `- click: [640, 400]` or
`- file: {path: ~/note.txt, contains: hello}`; see SKILL.md / README.md.
"""
import argparse
import datetime
import difflib
import html
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from xml.sax.saxutils import escape as xml_escape
from xml.sax.saxutils import quoteattr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ccu_audit as audit  # noqa: E402
import cu  # noqa: E402

DEFAULTS = {"timeout": 600.0, "settle": 0.5, "check_timeout": 5.0}
TOP_KEYS = ("name", "description", "from", "timeout", "settle", "check_timeout", "setup", "steps", "checks")
COMMON_KEYS = ("name", "optional", "shot")
SNAP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
POLL = 0.5


class SpecError(Exception):
    pass


class Unavailable(Exception):
    """A feature the image lacks (OCR, accessibility); not worth retrying."""


class Failed(Exception):
    pass


# --- spec schema ------------------------------------------------------------------
# Each action: (fields in positional order, required fields, defaults, types).
# A list value fills fields in order, a mapping names them, and a scalar is the
# first field. Types: "num", "int", "str", "bool", "region", "strs".

_POINT = (["x", "y", "button", "count", "mods"], [], {}, {
    "x": "int", "y": "int", "button": "str", "count": "int", "mods": "str"})

STEPS = {
    "launch": (["command", "wait_window", "timeout"], ["command"], {"timeout": 30.0},
               {"command": "str", "wait_window": "str", "timeout": "num"}),
    "wait_window": (["title", "timeout"], ["title"], {"timeout": 30.0}, {"title": "str", "timeout": "num"}),
    "focus": (["title"], ["title"], {}, {"title": "str"}),
    "click": _POINT,
    "double_click": _POINT,
    "triple_click": _POINT,
    "right_click": _POINT,
    "middle_click": _POINT,
    "move": (["x", "y"], ["x", "y"], {}, {"x": "int", "y": "int"}),
    "drag": (["x0", "y0", "x1", "y1", "button"], ["x0", "y0", "x1", "y1"], {"button": "left"},
             {"x0": "int", "y0": "int", "x1": "int", "y1": "int", "button": "str"}),
    "scroll": (["x", "y", "direction", "amount"], ["x", "y"], {"direction": "down", "amount": 3},
               {"x": "int", "y": "int", "direction": "str", "amount": "int"}),
    "mouse_down": (["button"], [], {"button": "left"}, {"button": "str"}),
    "mouse_up": (["button"], [], {"button": "left"}, {"button": "str"}),
    "type": (["text", "delay_ms"], ["text"], {"delay_ms": 12}, {"text": "str", "delay_ms": "int"}),
    "key": (["combos"], ["combos"], {}, {"combos": "strs"}),
    "wait": (["seconds"], ["seconds"], {}, {"seconds": "num"}),
    "shell": (["run", "timeout", "root"], ["run"], {"timeout": 60.0, "root": False},
              {"run": "str", "timeout": "num", "root": "bool"}),
    "screenshot": (["label"], [], {}, {"label": "str"}),
    "click_element": (["name", "role", "app", "button", "count", "index", "action", "timeout"], ["name"],
                      {"button": "left", "count": 1, "action": False, "timeout": 10.0},
                      {"name": "str", "role": "str", "app": "str", "button": "str", "count": "int",
                       "index": "int", "action": "bool", "timeout": "num"}),
    "wait_element": (["name", "role", "app", "index", "timeout"], ["name"], {"timeout": 10.0},
                     {"name": "str", "role": "str", "app": "str", "index": "int", "timeout": "num"}),
    "check": None,  # an inline check: - check: {file: {path: ..., contains: ...}}
}

CHECKS = {
    "window": (["title", "matches", "active", "exists", "timeout"], [], {"active": False, "exists": True},
               {"title": "str", "matches": "str", "active": "bool", "exists": "bool", "timeout": "num"}),
    "file": (["path", "exists", "contains", "matches", "equals", "timeout"], ["path"], {"exists": True},
             {"path": "str", "exists": "bool", "contains": "str", "matches": "str", "equals": "str", "timeout": "num"}),
    "shell": (["run", "exit", "contains", "matches", "root", "timeout"], ["run"], {"exit": 0, "root": False},
              {"run": "str", "exit": "int", "contains": "str", "matches": "str", "root": "bool", "timeout": "num"}),
    "text": (["text", "matches", "region", "absent", "timeout"], [], {"absent": False},
             {"text": "str", "matches": "str", "region": "region", "absent": "bool", "timeout": "num"}),
    "element": (["name", "role", "app", "index", "states", "exists", "timeout"], ["name"], {"exists": True},
                {"name": "str", "role": "str", "app": "str", "index": "int", "states": "strs", "exists": "bool",
                 "timeout": "num"}),
}


def _coerce(where, field, kind, v):
    bad = SpecError(f"{where}: {field} must be {kind_names[kind]}, got {v!r}")
    if kind == "str":
        if isinstance(v, bool):
            raise SpecError(f"{where}: {field} must be a string, got {str(v).lower()}; quote the value "
                            "(YAML reads unquoted yes/no/on/off/true/false as booleans)")
        if isinstance(v, (dict, list)) or v is None:
            raise bad
        return str(v)
    if kind == "bool":
        if not isinstance(v, bool):
            raise bad
        return v
    if kind in ("num", "int"):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise bad
        if kind == "int" and v != int(v):
            raise bad
        return int(v) if kind == "int" else float(v)
    if kind == "strs":
        if isinstance(v, str):
            return [v]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) for x in v):
            raise bad
        return list(v)
    if kind == "region":
        if (not isinstance(v, list) or len(v) != 4
                or not all(isinstance(x, int) and not isinstance(x, bool) for x in v)
                or v[2] <= v[0] or v[3] <= v[1]):
            raise bad
        return list(v)
    raise AssertionError(kind)


kind_names = {"str": "a string", "bool": "true or false", "num": "a number", "int": "an integer",
              "strs": "a string or a list of strings", "region": "[x0, y0, x1, y1] with x1 > x0 and y1 > y0"}


def _shape(where, action, value, schema):
    fields, required, defaults, types = schema
    if isinstance(value, dict):
        unknown = [k for k in value if k not in fields]
        if unknown:
            raise SpecError(f"{where}: {action} has unknown key(s) {', '.join(map(str, unknown))} "
                            f"(allowed: {', '.join(fields)})")
        args = dict(value)
    elif isinstance(value, list) and types.get(fields[0]) not in ("strs", "region"):
        if len(value) > len(fields):
            raise SpecError(f"{where}: {action} takes at most {len(fields)} values ({', '.join(fields)})")
        args = dict(zip(fields, value))
    elif value is None:
        args = {}
    else:
        args = {fields[0]: value}
    args = {k: v for k, v in args.items() if v is not None}
    for k in required:
        if k not in args:
            raise SpecError(f"{where}: {action} needs {k} (fields: {', '.join(fields)})")
    for k, v in list(args.items()):
        args[k] = _coerce(where, f"{action}.{k}", types[k], v)
    for k, v in defaults.items():
        args.setdefault(k, v)
    return args


def _validate_args(where, action, args, is_check):
    if action in _POINT_ACTIONS and ("x" in args) != ("y" in args):
        raise SpecError(f"{where}: {action} needs both x and y, or neither (click at the current position)")
    if args.get("button", "left") not in cu.BUTTONS:
        raise SpecError(f"{where}: button must be left, middle or right")
    if action == "scroll" and args["direction"] not in cu.SCROLL_BUTTONS:
        raise SpecError(f"{where}: scroll direction must be up, down, left or right")
    for k in ("matches",):
        if k in args:
            try:
                re.compile(args[k])
            except re.error as e:
                raise SpecError(f"{where}: {action}.{k} is not a valid regular expression: {e}") from None
    if is_check:
        if action == "window":
            if ("title" in args) == ("matches" in args):
                raise SpecError(f"{where}: window needs title (substring) or matches (regex), not both")
            if args["active"] and not args["exists"]:
                raise SpecError(f"{where}: window cannot be both active and exists: false")
        if action == "text" and ("text" in args) == ("matches" in args):
            raise SpecError(f"{where}: text needs text (substring) or matches (regex), not both")
        if action == "file" and not args["exists"] and any(k in args for k in ("contains", "matches", "equals")):
            raise SpecError(f"{where}: file with exists: false cannot also check its contents")
        if action == "element" and not args["exists"] and "states" in args:
            raise SpecError(f"{where}: element with exists: false cannot also check states")


_POINT_ACTIONS = ("click", "double_click", "triple_click", "right_click", "middle_click")


def parse_item(raw, where, is_check):
    registry = CHECKS if is_check else STEPS
    if isinstance(raw, str):
        raw = {raw: None}  # a bare action name, e.g. "- screenshot"
    if not isinstance(raw, dict):
        raise SpecError(f"{where}: expected a mapping with one action, e.g. "
                        f"{'{window: Mousepad}' if is_check else '{click: [640, 400]}'}, got {raw!r}")
    keys = [k for k in raw if k not in COMMON_KEYS and k != "timeout"]
    if len(keys) != 1:
        found = ", ".join(map(str, keys)) or "none"
        raise SpecError(f"{where}: each item needs exactly one action key (found: {found}); "
                        f"start a new '- ' list item for each action")
    action = keys[0]
    if action not in registry:
        hint = difflib.get_close_matches(str(action), list(registry), n=1)
        other = STEPS if is_check else CHECKS
        if action in other:
            msg = (f"'{action}' is a step; checks are: {', '.join(CHECKS)}" if is_check else
                   f"'{action}' is a check; inside steps write it as - check: {{{action}: ...}}")
            msg += f"; or did you mean {hint[0]}?" if hint else ""
        elif hint:
            msg = f"did you mean {hint[0]}?"
        else:
            msg = f"known: {', '.join(registry)}"
        raise SpecError(f"{where}: unknown {'check' if is_check else 'action'} '{action}' ({msg})")
    item = {"action": action, "raw": raw[action], "label": None, "optional": False, "shot": True}
    for k in COMMON_KEYS:
        if k in raw:
            item[k if k != "name" else "label"] = _coerce(where, k, "str" if k == "name" else "bool", raw[k])
    if action == "check":
        inner = parse_item(raw[action], f"{where}.check", True)
        item.update(inner, optional=item["optional"] or inner["optional"],
                    label=item["label"] or inner["label"], shot=item["shot"] and inner["shot"])
        item["check"] = True
        return item
    value = raw[action]
    schema = registry[action]
    if "timeout" in raw:
        if "timeout" not in schema[0]:
            raise SpecError(f"{where}: {action} does not take a timeout")
        value = dict(value) if isinstance(value, dict) else _shape(where, action, value, (schema[0], [], {}, schema[3]))
        value["timeout"] = raw["timeout"]
    args = _shape(where, action, value, schema)
    _validate_args(where, action, args, is_check)
    item.update(args=args, check=is_check)
    return item


def load_text(text, source="spec"):
    try:
        import yaml
    except ImportError:
        try:
            return json.loads(text)
        except ValueError:
            raise SpecError(f"{source}: PyYAML is not installed in this image, so the spec must be JSON; "
                            "rebuild the image (ccu build) or run: ccu install python3-yaml") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise SpecError(f"{source}: not valid YAML: {e}") from None


def parse_spec(data, source="spec"):
    if not isinstance(data, dict):
        raise SpecError(f"{source}: the spec must be a mapping with setup/steps/checks lists")
    unknown = [k for k in data if k not in TOP_KEYS]
    if unknown:
        hints = [f"{k} (did you mean {m[0]}?)" if (m := difflib.get_close_matches(str(k), TOP_KEYS, n=1)) else str(k)
                 for k in unknown]
        raise SpecError(f"{source}: unknown top-level key(s): {', '.join(hints)} (allowed: {', '.join(TOP_KEYS)})")
    spec = {"name": str(data.get("name") or os.path.splitext(os.path.basename(source))[0]),
            "description": str(data.get("description") or "")}
    frm = data.get("from")
    if frm is not None and not (isinstance(frm, str) and SNAP_RE.match(frm)):
        raise SpecError(f"{source}: from must be a snapshot name or 'base', got {frm!r}")
    spec["from"] = frm or ""
    for k, v in DEFAULTS.items():
        spec[k] = _coerce(source, k, "num", data[k]) if data.get(k) is not None else v
    total, errors = 0, []
    for phase in ("setup", "steps", "checks"):
        items = data.get(phase) or []
        if not isinstance(items, list):
            raise SpecError(f"{source}: {phase} must be a list (each item starts with '- ')")
        spec[phase] = []
        for i, raw in enumerate(items, 1):
            try:
                spec[phase].append(parse_item(raw, f"{phase}[{i}]", phase == "checks"))
            except SpecError as e:
                errors.append(str(e))  # report every bad item at once
        total += len(items)
    if errors:
        raise SpecError(f"{source}: " + f"\n{source}: ".join(errors))
    if not total:
        raise SpecError(f"{source}: the spec has no setup, steps or checks")
    return spec


def load_spec(path, label=None):
    text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
    label = label or path
    return parse_spec(load_text(text, label), label)


# --- running items ------------------------------------------------------------------

def _grab():
    """Screenshot PNG bytes without an audit entry (the run saves its own)."""
    fn = getattr(cu.screenshot_png, "__wrapped__", cu.screenshot_png)
    return fn(cursor=True)


def _a11y(fn_name):
    fn = getattr(cu, fn_name, None)
    if fn is None:
        raise Unavailable(f"accessibility support not available in this image (cu.{fn_name} is missing); "
                          "rebuild the image with a version of ccu that has it, or use click/text steps instead")
    return fn


def _sh(cmd, timeout, root=False):
    argv = ["sudo", "--preserve-env=DISPLAY", "--", "bash", "-lc", cmd] if root else ["bash", "-lc", cmd]
    with audit.event("shell", {"command": cmd, "root": root}) as ev:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=cu._env(),
                                  cwd=os.path.expanduser("~"))
        except subprocess.TimeoutExpired as e:
            ev.update(ok=False, result={"timeout": timeout})
            out = (e.stdout or b"")
            out = out.decode(errors="replace") if isinstance(out, bytes) else out
            raise Failed(f"timed out after {timeout:g}s: {cmd}") from None
        ev.update(ok=proc.returncode == 0,
                  result={"exit": proc.returncode, "stdout": proc.stdout[-1000:], "stderr": proc.stderr[-1000:]})
    return proc.returncode, proc.stdout, proc.stderr


def _tail(s, n=600):
    s = s.strip()
    return s if len(s) <= n else "..." + s[-n:]


def step_launch(a):
    pid, log = cu.launch(a["command"])
    if a.get("wait_window"):
        _, title = cu.wait_for_window(a["wait_window"], a["timeout"])
        return f"pid {pid}; window '{title}'"
    return f"pid {pid} (log {log})"


def step_wait_window(a):
    _, title = cu.wait_for_window(a["title"], a["timeout"])
    return f"window '{title}'"


def step_click(action):
    count = {"double_click": 2, "triple_click": 3}.get(action, 1)
    button = {"right_click": "right", "middle_click": "middle"}.get(action, "left")

    def run(a):
        cu.click(a.get("x"), a.get("y"), button=a.get("button", button), count=a.get("count", count),
                 modifiers=a.get("mods"))
        where = f"at {a['x']},{a['y']}" if "x" in a else "at the pointer"
        return f"{a.get('button', button)} x{a.get('count', count)} {where}"
    return run


def step_shell(a):
    rc, out, err = _sh(a["run"], a["timeout"], a["root"])
    if rc != 0:
        raise Failed(f"exit {rc}: {_tail(err or out, 400)}")
    return _tail(out) or "exit 0"


def step_click_element(a):
    el = _a11y("click_element")(a["name"], role=a.get("role"), app=a.get("app"), button=a["button"],
                                count=a["count"], index=a.get("index"), timeout=a["timeout"],
                                action=a["action"])
    return f"{'invoked' if a['action'] else 'clicked'} {_el_desc(el) if isinstance(el, dict) else repr(a['name'])}"


def step_wait_element(a):
    el = _a11y("find_element")(a["name"], role=a.get("role"), app=a.get("app"), timeout=a["timeout"],
                               index=a.get("index"))
    return f"found {_el_desc(el)}"


def _el_desc(el):
    if not isinstance(el, dict):
        return repr(el)
    return (f"{el.get('role', '?')} '{el.get('name', '')}' in {el.get('app', '?')} "
            f"at {el.get('x')},{el.get('y')} {el.get('w')}x{el.get('h')}")


STEP_RUN = {
    "launch": step_launch,
    "wait_window": step_wait_window,
    "focus": lambda a: cu.focus(a["title"]),
    "move": lambda a: cu.move(a["x"], a["y"]),
    "drag": lambda a: cu.drag(a["x0"], a["y0"], a["x1"], a["y1"], a["button"]),
    "scroll": lambda a: cu.scroll(a["x"], a["y"], a["direction"], a["amount"]),
    "mouse_down": lambda a: cu.mouse_down(a["button"]),
    "mouse_up": lambda a: cu.mouse_up(a["button"]),
    "type": lambda a: cu.type_text(a["text"], a["delay_ms"]) or f"{len(a['text'])} characters",
    "key": lambda a: cu.key(*a["combos"]),
    "wait": lambda a: time.sleep(a["seconds"]),
    "shell": step_shell,
    "screenshot": lambda a: None,
    "click_element": step_click_element,
    "wait_element": step_wait_element,
}
for _name in _POINT_ACTIONS:
    STEP_RUN[_name] = step_click(_name)


# Checks return (ok, message, detail); they are polled until ok or the timeout.

def _title_match(a, title):
    if "matches" in a:
        return re.search(a["matches"], title) is not None
    return a["title"].lower() in title.lower()


def check_window(a):
    titles = [t for _, _, t in cu.list_windows()]
    want = a.get("title") or f"/{a.get('matches')}/"
    if a["active"]:
        act = cu.active_window()
        ok = bool(act) and _title_match(a, act)
        return ok, f"active window is '{act}'" + ("" if ok else f", expected one matching {want!r}"), \
            {"active": act, "windows": titles}
    found = [t for t in titles if _title_match(a, t)]
    if a["exists"]:
        return bool(found), (f"window '{found[0]}'" if found else
                             f"no window matching {want!r}; open windows: {titles}"), {"windows": titles}
    return not found, ("no window matching" if not found else f"window '{found[0]}' is still open"), \
        {"windows": titles}


def check_file(a):
    path = os.path.expanduser(a["path"])
    if not a["exists"]:
        return not os.path.exists(path), f"{path} {'exists' if os.path.exists(path) else 'does not exist'}", {}
    if not os.path.exists(path):
        return False, f"{path} does not exist", {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            content = f.read(1 << 20)
    except OSError as e:
        return False, f"cannot read {path}: {e}", {}
    detail = {"content": _tail(content, 1000)}
    if "contains" in a and a["contains"] not in content:
        return False, f"{path} does not contain {a['contains']!r}", detail
    if "matches" in a and not re.search(a["matches"], content, re.M):
        return False, f"{path} does not match /{a['matches']}/", detail
    if "equals" in a and content.rstrip("\n") != a["equals"].rstrip("\n"):
        return False, f"{path} is not equal to the expected text", detail
    return True, f"{path} ok ({len(content)} characters)", detail


def check_shell(a):
    rc, out, err = _sh(a["run"], max(a.get("timeout") or 0, 30), a["root"])
    detail = {"exit": rc, "stdout": _tail(out, 1000), "stderr": _tail(err, 400)}
    if rc != a["exit"]:
        return False, f"exit {rc}, expected {a['exit']}: {_tail(err or out, 300)}", detail
    if "contains" in a and a["contains"] not in out:
        return False, f"output does not contain {a['contains']!r}: {_tail(out, 300)}", detail
    if "matches" in a and not re.search(a["matches"], out, re.M):
        return False, f"output does not match /{a['matches']}/: {_tail(out, 300)}", detail
    return True, f"exit {rc}" + (f": {_tail(out, 200)}" if out.strip() else ""), detail


def _norm(s):
    return " ".join(s.lower().split())


def ocr(region=None):
    """Text on screen (or in region [x0, y0, x1, y1]) read by tesseract."""
    if not shutil.which("tesseract"):
        raise Unavailable("tesseract is not installed in this image; rebuild it (ccu build) "
                          "or run: ccu install tesseract-ocr")
    from PIL import Image, ImageOps, ImageStat

    img = Image.open(io.BytesIO(_grab())).convert("L")
    if region:
        img = img.crop(tuple(region))
    if ImageStat.Stat(img).mean[0] < 110:
        img = ImageOps.invert(img)  # light text on a dark background
    # Screen fonts are small; tesseract reads them far better at 2x.
    img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    texts = []
    with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
        img.save(tmp.name)
        # psm 11 (sparse text) finds scattered UI labels; psm 3 keeps paragraphs in order.
        for psm in ("11", "3"):
            proc = subprocess.run(["tesseract", tmp.name, "stdout", "--psm", psm],
                                  capture_output=True, text=True, timeout=60)
            texts.append(proc.stdout)
    return texts


def check_text(a):
    texts = ocr(a.get("region"))
    if "matches" in a:
        found = any(re.search(a["matches"], t, re.I | re.M) for t in texts)
        want = f"/{a['matches']}/"
    else:
        found = any(_norm(a["text"]) in _norm(t) for t in texts)
        want = repr(a["text"])
    seen = _tail(" ".join(texts[0].split()), 1500)
    where = f" in {a['region']}" if a.get("region") else ""
    detail = {"ocr": seen}
    if a["absent"]:
        return not found, f"{want} {'is' if found else 'is not'} on screen{where}", detail
    return found, (f"{want} is on screen{where}" if found else f"{want} not found on screen{where}; "
                   f"OCR read: {_tail(seen, 300)}"), detail


def check_element(a):
    if a["exists"]:
        find = _a11y("find_element")
        try:
            el = find(a["name"], role=a.get("role"), app=a.get("app"), timeout=0.0, index=a.get("index"))
        except Unavailable:
            raise
        except Exception as e:  # noqa: BLE001 - find_element raises when nothing matches
            return False, f"no element: {e}", {}
        states = [str(s).lower() for s in (el.get("states") or [])] if isinstance(el, dict) else []
        missing = [s for s in a.get("states", []) if s.lower() not in states]
        if missing:
            return False, f"{_el_desc(el)} lacks state(s) {missing}; has {states}", {"element": el}
        return True, f"found {_el_desc(el)}", {"element": el}
    els = _a11y("elements")(app=a.get("app"), role=a.get("role"), name=a["name"])
    if els:
        return False, f"{len(els)} matching element(s) still present: {_el_desc(els[0])}", {"elements": els[:5]}
    return True, "no matching element", {}


CHECK_RUN = {"window": check_window, "file": check_file, "shell": check_shell,
             "text": check_text, "element": check_element}


def describe(item):
    raw = item["raw"]
    s = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, default=str)
    s = s.replace("\n", "\\n")
    return s if len(s) <= 70 else s[:67] + "..."


def run_check(item, spec):
    a = item["args"]
    timeout = a.get("timeout", spec["check_timeout"])
    deadline = time.time() + timeout
    with audit.event("check", {"check": item["action"], **a}, kind="observation") as ev:
        while True:
            try:
                ok, msg, detail = CHECK_RUN[item["action"]](a)
            except (Unavailable, Failed):
                raise
            except Exception as e:  # noqa: BLE001
                ok, msg, detail = False, f"{type(e).__name__}: {e}", {}
            if ok or time.time() >= deadline:
                break
            time.sleep(POLL)
        ev.update(ok=ok, result=msg)
    if not ok:
        raise Failed(msg + (f" (after {timeout:g}s)" if timeout else ""), detail)
    return msg, detail


# --- one run -------------------------------------------------------------------------

def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _slug(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-")[:40] or "item"


def run_spec(spec, out, run_no=1, reset="", record=False, spec_file=""):
    os.makedirs(os.path.join(out, "shots"), exist_ok=True)
    t_run = time.time()
    result = {"name": spec["name"], "description": spec["description"], "spec": spec_file, "run": run_no,
              "from": spec["from"], "reset": reset, "status": "running", "error": None,
              "started": _now(), "seconds": None, "items": [], "audit": None, "recording": None,
              "host": os.uname().nodename}
    start = audit.record("test_start", {"spec": spec["name"], "run": run_no, "reset": reset}, kind="command")
    if record:
        try:
            audit.record_start(os.path.join(out, "recording.mp4"), fps=audit._num("CCU_RECORD_FPS", 5))
            result["recording"] = "recording.mp4"
        except Exception as e:  # noqa: BLE001
            result["recording_error"] = str(e)
            print(f"  recording not started: {e}", flush=True)
    n = 0
    stop = None  # why the remaining steps are skipped
    try:
        for phase in ("setup", "steps", "checks"):
            items = spec[phase]
            for i, item in enumerate(items, 1):
                n += 1
                rec = {"phase": phase, "index": i, "action": item["action"], "check": item["check"],
                       "label": item["label"], "args": item.get("args"), "optional": item["optional"],
                       "status": "skipped", "message": None, "detail": None, "ms": 0, "shot": None}
                result["items"].append(rec)
                head = f"  [{phase} {i}/{len(items)}] {item['label'] or item['action']}: {describe(item)}"
                if stop:
                    rec["message"] = stop
                    continue
                if time.time() - t_run > spec["timeout"]:
                    stop = f"run timed out after {spec['timeout']:g}s"
                    rec.update(status="failed", message=stop)
                    print(f"{head}  FAILED: {stop}", flush=True)
                    continue
                t0 = time.time()
                try:
                    if item["check"]:
                        msg, detail = run_check(item, spec)
                    else:
                        msg, detail = STEP_RUN[item["action"]](item["args"]), None
                    rec.update(status="passed", message=None if msg is None else str(msg), detail=detail)
                except Failed as e:
                    rec.update(status="failed", message=str(e.args[0]), detail=e.args[1] if len(e.args) > 1 else None)
                except Exception as e:  # noqa: BLE001 - CuError, Unavailable, a11y errors
                    rec.update(status="failed", message=str(e) or type(e).__name__)
                rec["ms"] = int((time.time() - t0) * 1000)
                if item["shot"] or rec["status"] == "failed":
                    if not item["check"]:
                        time.sleep(spec["settle"])
                    name = f"{n:03d}-{phase}-{_slug(item['label'] or item['args'].get('label') or item['action'])}.png"
                    try:
                        with open(os.path.join(out, "shots", name), "wb") as f:
                            f.write(_grab())
                        rec["shot"] = f"shots/{name}"
                    except Exception as e:  # noqa: BLE001
                        print(f"  screenshot failed: {e}", file=sys.stderr)
                state = "ok" if rec["status"] == "passed" else ("FAILED (optional)" if item["optional"] else "FAILED")
                print(f"{head}  {state} {rec['ms'] / 1000:.1f}s" +
                      (f": {rec['message']}" if rec["status"] == "failed" else ""), flush=True)
                if rec["status"] == "failed" and not item["optional"] and phase != "checks":
                    stop = f"skipped: {phase}[{i}] {item['action']} failed"
    finally:
        if result["recording"]:
            try:
                audit.record_stop()
            except Exception as e:  # noqa: BLE001
                result["recording_error"] = str(e)
    hard = [r for r in result["items"] if r["status"] == "failed" and not r["optional"]]
    if any(r["phase"] == "setup" for r in hard):
        result["status"] = "error"
        result["error"] = "setup failed: " + next(r["message"] for r in hard if r["phase"] == "setup")
    else:
        result["status"] = "failed" if hard else "passed"
    result["seconds"] = round(time.time() - t_run, 2)
    end = audit.record("test_end", {"spec": spec["name"], "run": run_no, "status": result["status"]},
                       kind="command")
    if start and end:
        time.sleep(1.0)  # let the last audit screenshots land
        try:
            result["audit"] = export_audit_segment(start["seq"], end["seq"], os.path.join(out, "audit"))
        except Exception as e:  # noqa: BLE001 - evidence is best effort; the verdict stands
            result["audit_error"] = f"{type(e).__name__}: {e}"
    write_json(os.path.join(out, "result.json"), result)
    try:
        render_run_html(result, os.path.join(out, "report.html"))
    except Exception as e:  # noqa: BLE001
        print(f"  report.html failed: {type(e).__name__}: {e}", file=sys.stderr)
    return result


def export_audit_segment(first, last, dest):
    """Copy log entries first..last and their screenshots to dest, with a timeline."""
    os.makedirs(os.path.join(dest, "shots"), exist_ok=True)
    entries = []
    with open(audit.LOG, "rb") as f, open(os.path.join(dest, "audit.jsonl"), "wb") as out:
        for raw in f:
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if isinstance(e.get("seq"), int) and first <= e["seq"] <= last:
                out.write(raw)
                entries.append(e)
                if e.get("shot"):
                    src = os.path.join(audit.AUDIT_DIR, e["shot"])
                    if os.path.exists(src):
                        shutil.copy2(src, os.path.join(dest, e["shot"]))
    info = {"first_seq": first, "last_seq": last, "entries": len(entries), "timeline": "audit/index.html"}
    try:
        audit.render_html(os.path.join(dest, "index.html"), entries=entries)
    except TypeError:  # an image from before render_html took a segment
        info["timeline"] = "audit/audit.jsonl"
    return info


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")


# --- reports ---------------------------------------------------------------------------

CSS = """
:root { --bg:#fff; --fg:#1d1d1f; --dim:#6e6e73; --line:#e5e5ea; --pass:#1a7f37; --fail:#c62828;
        --warn:#9a6700; --failbg:#fdecea; --warnbg:#fff8e1; --code:#f5f5f7; --link:#0a58ca; }
@media (prefers-color-scheme: dark) { :root { --bg:#161618; --fg:#ececf0; --dim:#9a9aa2; --line:#2c2c30;
        --pass:#4ac26b; --fail:#ff6b6b; --warn:#e3b341; --failbg:#3a1d1d; --warnbg:#33290f; --code:#222226; --link:#6cb6ff; } }
body { background:var(--bg); color:var(--fg); font:14px/1.45 system-ui, sans-serif; margin:16px; }
a { color:var(--link); }
table { border-collapse:collapse; width:100%; }
th, td { border-bottom:1px solid var(--line); padding:6px 8px; text-align:left; vertical-align:top; }
code, pre { font:12px ui-monospace, monospace; background:var(--code); border-radius:4px; }
code { padding:1px 4px; word-break:break-all; } pre { padding:6px; white-space:pre-wrap; margin:4px 0 0; }
img { width:280px; border:1px solid var(--line); }
.dim { color:var(--dim); font-size:12px; }
.passed { color:var(--pass); font-weight:600; } .failed, .error { color:var(--fail); font-weight:600; }
.skipped { color:var(--dim); } .optional { color:var(--warn); font-weight:600; }
tr.r-failed, tr.r-error { background:var(--failbg); } tr.r-optional { background:var(--warnbg); }
.big { font-size:20px; }
"""


def _page(title, body):
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>\n{body}\n</body></html>\n")


def _status_word(r):
    if r["status"] == "failed" and r.get("optional"):
        return "optional"
    return r["status"]


def render_run_html(result, path):
    esc = html.escape
    rows = []
    for r in result["items"]:
        st = _status_word(r)
        img = (f'<a href="{esc(r["shot"])}"><img loading="lazy" src="{esc(r["shot"])}" alt=""></a>'
               if r.get("shot") else "")
        msg = f"<pre>{esc(r['message'])}</pre>" if r.get("message") else ""
        if r.get("detail"):
            msg += f'<details><summary class="dim">detail</summary><pre>' \
                   f'{esc(json.dumps(r["detail"], indent=1, ensure_ascii=False, default=str))}</pre></details>'
        rows.append(
            f'<tr class="r-{esc(st)}"><td>{esc(r["phase"])} {r["index"]}</td>'
            f'<td><b>{esc(r["label"] or r["action"])}</b>{" (check)" if r["check"] else ""}<br>'
            f'<code>{esc(json.dumps(r["args"], ensure_ascii=False, default=str))}</code></td>'
            f'<td class="{esc(st)}">{esc("failed (optional)" if st == "optional" else st)}<br>'
            f'<span class="dim">{r["ms"] / 1000:.1f}s</span></td><td>{msg}</td><td>{img}</td></tr>')
    links = ['<a href="result.json">result.json</a>']
    if result.get("audit"):
        links.append(f'<a href="{esc(result["audit"]["timeline"])}">audit timeline</a> '
                     f'(entries {result["audit"]["first_seq"]}..{result["audit"]["last_seq"]})')
    if result.get("recording"):
        links.append(f'<a href="{esc(result["recording"])}">screen recording</a>')
    if result.get("spec"):
        links.append(f'<a href="{esc(result["spec"])}">spec</a>')
    err = f'<p class="error">{esc(result["error"])}</p>' if result.get("error") else ""
    body = (f'<h1>{esc(result["name"])} <span class="dim">run {result["run"]}</span></h1>'
            f'<p class="big {esc(result["status"])}">{esc(result["status"].upper())}</p>{err}'
            f'<p>{esc(result.get("description") or "")}</p>'
            f'<p class="dim">started {esc(result["started"])} · {result["seconds"]}s · '
            f'reset: {esc(result.get("reset") or "none")} · {" · ".join(links)}</p>'
            f'<table><thead><tr><th>#</th><th>action</th><th>status</th><th>message</th>'
            f'<th>screenshot</th></tr></thead><tbody>\n{chr(10).join(rows)}\n</tbody></table>')
    with open(path, "w", encoding="utf-8") as f:
        f.write(_page(f"{result['name']} run {result['run']}", body))


def _failures(result):
    out = []
    if result.get("error") and not result.get("items"):
        out.append(result["error"])
    for r in result.get("items", []):
        if r["status"] == "failed" and not r.get("optional"):
            out.append(f"{r['phase']}[{r['index']}] {r['label'] or r['action']}: {r['message']}")
    return out


def report(root):
    """Aggregate root/*/result.json into summary.json, junit.xml and index.html."""
    runs = []
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d, "result.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    r = json.load(f)
            except ValueError as e:
                r = {"name": d, "run": 0, "status": "error", "error": f"unreadable result.json: {e}",
                     "started": "", "seconds": 0, "items": []}
            r["id"] = d
            runs.append(r)
    runs.sort(key=lambda r: (r.get("started") or "", r["id"]))
    count = {s: sum(1 for r in runs if r["status"] == s) for s in ("passed", "failed", "error")}
    summary = {
        "status": "passed" if runs and count["passed"] == len(runs) else "failed",
        "total": len(runs), **count,
        "seconds": round(sum(r.get("seconds") or 0 for r in runs), 2),
        "runs": [{"id": r["id"], "name": r.get("name"), "spec": r.get("spec"), "run": r.get("run"),
                  "status": r["status"], "seconds": r.get("seconds"), "reset": r.get("reset"),
                  "failures": _failures(r), "report": f"{r['id']}/report.html",
                  "screenshot": next((f"{r['id']}/{i['shot']}" for i in r.get("items", [])
                                      if i["status"] == "failed" and not i.get("optional") and i.get("shot")),
                                     None)}
                 for r in runs],
    }
    write_json(os.path.join(root, "summary.json"), summary)
    write_junit(runs, os.path.join(root, "junit.xml"))
    render_index(summary, os.path.join(root, "index.html"))
    return summary


def write_junit(runs, path):
    suites = {}
    for r in runs:
        suites.setdefault(r.get("name") or r["id"], []).append(r)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>']
    tot = {k: sum(1 for r in runs if r["status"] == k) for k in ("failed", "error")}
    lines.append(f'<testsuites name="ccu test" tests="{len(runs)}" failures="{tot["failed"]}" '
                 f'errors="{tot["error"]}" time="{sum(r.get("seconds") or 0 for r in runs):.2f}">')
    for name, rs in suites.items():
        f = sum(1 for r in rs if r["status"] == "failed")
        e = sum(1 for r in rs if r["status"] == "error")
        lines.append(f'  <testsuite name={quoteattr(name)} tests="{len(rs)}" failures="{f}" errors="{e}" '
                     f'time="{sum(r.get("seconds") or 0 for r in rs):.2f}">')
        for r in rs:
            case = f"{name} [run {r.get('run')}]"
            lines.append(f'    <testcase classname={quoteattr("ccu." + _slug(name))} '
                         f'name={quoteattr(case)} time="{r.get("seconds") or 0:.2f}">')
            fails = _failures(r)
            if r["status"] in ("failed", "error"):
                tag = "failure" if r["status"] == "failed" else "error"
                first = (fails[0] if fails else r.get("error") or r["status"])
                lines.append(f'      <{tag} message={quoteattr(first[:500])}>{xml_escape(chr(10).join(fails))}</{tag}>')
            shots = [f"{r['id']}/{i['shot']}" for i in r.get("items", []) if i["status"] == "failed" and i.get("shot")]
            out = [f"report: {r['id']}/report.html"] + [f"[[ATTACHMENT|{s}]]" for s in shots]
            lines.append(f"      <system-out>{xml_escape(chr(10).join(out))}</system-out>")
            lines.append("    </testcase>")
        lines.append("  </testsuite>")
    lines.append("</testsuites>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def render_index(summary, path):
    esc = html.escape
    rows = []
    for r in summary["runs"]:
        img = (f'<a href="{esc(r["screenshot"])}"><img loading="lazy" src="{esc(r["screenshot"])}" alt=""></a>'
               if r.get("screenshot") else "")
        fails = "".join(f"<pre>{esc(x)}</pre>" for x in r["failures"])
        rows.append(f'<tr class="r-{esc(r["status"])}">'
                    f'<td><a href="{esc(r["report"])}">{esc(str(r["name"]))}</a><br>'
                    f'<span class="dim">{esc(r["id"])} · reset: {esc(str(r.get("reset") or "none"))}</span></td>'
                    f'<td>{r.get("run")}</td><td class="{esc(r["status"])}">{esc(r["status"])}</td>'
                    f'<td>{r.get("seconds")}s</td><td>{fails}</td><td>{img}</td></tr>')
    body = (f'<h1>ccu test results</h1><p class="big {esc(summary["status"])}">{esc(summary["status"].upper())}: '
            f'{summary["passed"]} passed, {summary["failed"]} failed, {summary["error"]} errors '
            f'<span class="dim">of {summary["total"]} runs · {summary["seconds"]}s</span></p>'
            f'<p class="dim"><a href="summary.json">summary.json</a> · <a href="junit.xml">junit.xml</a></p>'
            f'<table><thead><tr><th>spec</th><th>run</th><th>status</th><th>time</th><th>failures</th>'
            f'<th>failing screenshot</th></tr></thead><tbody>\n{chr(10).join(rows)}\n</tbody></table>')
    with open(path, "w", encoding="utf-8") as f:
        f.write(_page("ccu test results", body))


# --- draft a spec from the audit log -------------------------------------------------

def _q(s):
    return json.dumps(s, ensure_ascii=False)


def _app_title(title):
    """The stable part of a window title: 'Untitled 1 - Mousepad' -> 'Mousepad'."""
    title = title.lstrip("*")
    return title.rsplit(" - ", 1)[-1].strip() if " - " in title else title


def draft(entries, frm=""):
    """A spec draft (YAML text) that replays the actions in audit entries."""
    out = ["# Draft generated by `ccu test draft` from the audit log. Review it before use:",
           "# replace coordinates with click_element where you can, fill in typed text,",
           "# and add checks for what the session was meant to achieve.",
           f"name: {_q('draft')}"]
    out.append(f"from: {frm}" if frm else "# from: SNAPSHOT     # snapshot to start from (or: base)")
    out.append("steps:")
    acts = [e for e in entries if e.get("kind") in ("action", "command")
            and e.get("action") not in ("test_start", "test_end", "record_start", "record_stop")]
    waited = ""
    for i, e in enumerate(acts):
        a, args = e.get("action"), e.get("args") or {}
        nxt = acts[i + 1] if i + 1 < len(acts) else None
        note = "" if e.get("ok", True) else "  # failed in the session"
        # `window` is the active window when the action started; when it changes
        # (a dialog opened), wait for it so the replay doesn't race the app.
        title = _app_title(e.get("window") or "")
        if title and title != waited and a in ("click", "double_click", "type", "key", "scroll", "drag", "move"):
            out.append(f"  - wait_window: {_q(title)}   # active in the session: {_q(e.get('window'))}")
            waited = title
        if a == "launch":
            out.append(f"  - launch: {_q(args.get('command', ''))}{note}")
            title = _app_title((nxt or {}).get("window") or "")
            if title:
                out.append(f"  - wait_window: {_q(title)}")
                waited = title
        elif a == "click":
            count, button, mods = args.get("count", 1), args.get("button", "left"), args.get("modifiers")
            name = {(2, "left"): "double_click", (3, "left"): "triple_click", (1, "right"): "right_click",
                    (1, "middle"): "middle_click"}.get((count, button), "click")
            if args.get("x") is None:
                out.append(f"  - {name}:{note}")
            elif mods or (name == "click" and (count != 1 or button != "left")):
                extra = (f", button: {button}, count: {count}" if name == "click" else "") + \
                        (f", mods: {_q(mods)}" if mods else "")
                out.append(f"  - {name}: {{x: {args['x']}, y: {args['y']}{extra}}}{note}")
            else:
                out.append(f"  - {name}: [{args['x']}, {args['y']}]{note}")
        elif a == "type":
            t = args.get("text")
            if isinstance(t, str):
                out.append(f"  - type: {_q(t)}{note}")
            else:
                n = (t or {}).get("len", "?")
                out.append(f'  - type: "TODO"   # {n} characters; the audit log keeps only the length '
                           f"(CCU_AUDIT_TYPE=full logs the text){note}")
        elif a == "key":
            combos = args.get("combos") or []
            out.append(f"  - key: {_q(combos[0]) if len(combos) == 1 else _q(combos)}{note}")
        elif a in ("move", "mouse_down", "mouse_up"):
            v = f"[{args.get('x')}, {args.get('y')}]" if a == "move" else _q(args.get("button", "left"))
            out.append(f"  - {a}: {v}{note}")
        elif a == "drag":
            out.append(f"  - drag: [{args.get('x0')}, {args.get('y0')}, {args.get('x1')}, {args.get('y1')}]{note}")
        elif a == "scroll":
            out.append(f"  - scroll: [{args.get('x')}, {args.get('y')}, {_q(args.get('direction', 'down'))}, "
                       f"{args.get('amount', 3)}]{note}")
        elif a == "focus":
            out.append(f"  - focus: {_q(args.get('pattern', ''))}{note}")
        elif a in ("exec", "shell"):
            cmd = args.get("command") or " ".join(_shq(x) for x in args.get("argv") or [])
            if args.get("root"):
                out.append(f"  - shell: {{run: {_q(cmd)}, root: true}}{note}")
            else:
                out.append(f"  - shell: {_q(cmd)}{note}")
        elif a == "install":
            what = " ".join(map(str, (args.get("argv") or [])[1:] or args.get("targets") or []))
            out.append(f"  # install {what}: install once, `ccu snapshot save NAME`, and start the spec from it")
        else:
            out.append(f"  # {a} {json.dumps(args, ensure_ascii=False, default=str)}")
    if not acts:
        out.append("  # (no actions in the audit log range)")
    out.append("checks:")
    final = next((e.get("window") for e in reversed(entries) if e.get("window")), "")
    if final:
        out.append(f"  - window: {{title: {_q(final.lstrip('*'))}, active: true}}   # TODO: check what matters")
    out.append("  # - file: {path: ~/result.txt, contains: \"expected text\"}")
    out.append("  # - text: {text: \"Saved\", region: [0, 0, 1280, 800]}")
    return "\n".join(out) + "\n"


def _shq(s):
    import shlex
    return shlex.quote(str(s))


# --- CLI -------------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(prog="ccu_test.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("run")
    s.add_argument("spec")
    s.add_argument("--out", required=True)
    s.add_argument("--run", type=int, default=1)
    s.add_argument("--reset", default="", help="how the desktop was reset (for the report)")
    s.add_argument("--host-from", default=None, help="the `from` the host read from the spec, to cross-check")
    s.add_argument("--record", action="store_true")
    s = sub.add_parser("validate")
    s.add_argument("specs", nargs="+")
    s.add_argument("--host-from", default=None)
    s.add_argument("--as", dest="label", default=None, help="name to show for a spec read from stdin")
    s = sub.add_parser("report")
    s.add_argument("dir")
    s = sub.add_parser("draft")
    s.add_argument("--since", type=int, default=0, help="first audit seq to include")
    s.add_argument("--last", type=int, default=0, help="only the last N actions")
    s.add_argument("--from", dest="frm", default="")
    a = p.parse_args(argv)

    if a.cmd == "run":
        spec_file = os.path.basename(a.spec)
        try:
            spec = load_spec(a.spec)
            _check_host_from(spec, a.host_from)
        except (SpecError, OSError) as e:
            os.makedirs(a.out, exist_ok=True)
            result = {"name": os.path.splitext(spec_file)[0], "spec": spec_file, "run": a.run, "reset": a.reset,
                      "status": "error", "error": f"invalid spec: {e}", "started": _now(), "seconds": 0, "items": []}
            write_json(os.path.join(a.out, "result.json"), result)
            render_run_html(result, os.path.join(a.out, "report.html"))
            print(f"ERROR invalid spec: {e}", file=sys.stderr)
            return 2
        print(f"{spec['name']} (run {a.run})", flush=True)
        r = run_spec(spec, a.out, a.run, a.reset, a.record, spec_file)
        print(f"{r['status'].upper()} {spec['name']} run {a.run} in {r['seconds']}s", flush=True)
        return 0 if r["status"] == "passed" else 1
    if a.cmd == "validate":
        rc = 0
        for path in a.specs:
            try:
                spec = load_spec(path, a.label if path == "-" else None)
                path = a.label if path == "-" and a.label else path
                _check_host_from(spec, a.host_from)
                n = {k: len(spec[k]) for k in ("setup", "steps", "checks")}
                print(f"ok: {path}: {spec['name']!r}, from {spec['from'] or '(the current snapshot)'}, "
                      f"{n['setup']} setup, {n['steps']} steps, {n['checks']} checks")
            except (SpecError, OSError) as e:
                print(f"invalid: {e}", file=sys.stderr)
                rc = 1
        return rc
    if a.cmd == "report":
        s = report(a.dir)
        print(f"{s['status'].upper()}: {s['passed']} passed, {s['failed']} failed, {s['error']} errors "
              f"of {s['total']} runs")
        return 0 if s["status"] == "passed" else 1
    if a.cmd == "draft":
        entries = [e for e in audit.read_entries() if isinstance(e.get("seq"), int) and e["seq"] >= a.since]
        if a.last:
            acts = [e for e in entries if e.get("kind") != "observation"]
            entries = [e for e in entries if e["seq"] >= acts[-a.last]["seq"]] if len(acts) > a.last else entries
        sys.stdout.write(draft(entries, a.frm))
        return 0
    return 2


def _check_host_from(spec, host_from):
    # The host reads `from:` with a simple pattern to know which snapshot to
    # reset to before the spec is parsed here; make sure both agree.
    if host_from is not None and spec["from"] != host_from:
        raise SpecError(f"the host read from: {host_from or '(nothing)'} but the spec says "
                        f"{spec['from'] or '(nothing)'}; write it as a plain top-level line `from: NAME`")


if __name__ == "__main__":
    sys.exit(main())
