"""Unit tests for rootfs/opt/ccu/ccu_test.py (the `ccu test` runner).

They replace the `cu` module with a stub, so they need no X display or
container. Run them with any Python 3.8+:

    python3 -m unittest discover -s tests -v

or inside the container (where PyYAML is present, so the YAML cases run too):

    podman cp tests/test_ccu_test.py ccu:/tmp/ && ccu exec python3 /tmp/test_ccu_test.py -v
"""
import json
import os
import sys
import tempfile
import types
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
for d in (os.path.join(HERE, "..", "rootfs", "opt", "ccu"), "/opt/ccu"):
    if os.path.exists(os.path.join(d, "ccu_test.py")):
        sys.path.insert(0, os.path.abspath(d))
        break
os.environ["CCU_AUDIT"] = "0"  # no audit log writes from the tests

import ccu_test as T  # noqa: E402

try:
    import yaml  # noqa: F401
    HAVE_YAML = True
except ImportError:
    HAVE_YAML = False


def stub_cu(a11y=True, windows=("note.txt - Mousepad",), active="note.txt - Mousepad", elements=()):
    calls = []

    def rec(name):
        return lambda *a, **kw: calls.append((name, a, kw))

    cu = types.SimpleNamespace(
        BUTTONS={"left": "1", "middle": "2", "right": "3"},
        SCROLL_BUTTONS={"up": "4", "down": "5", "left": "6", "right": "7"},
        calls=calls,
        screenshot_png=lambda cursor=True: b"\x89PNG stub",
        _env=lambda: dict(os.environ),
        list_windows=lambda: [("0x1", "1", t) for t in windows],
        active_window=lambda: active,
        wait_for_window=lambda title, timeout=30: ("0x1", title),
        launch=lambda cmd: (calls.append(("launch", (cmd,), {})) or (123, "/tmp/log")),
        click=rec("click"), move=rec("move"), drag=rec("drag"), scroll=rec("scroll"),
        mouse_down=rec("mouse_down"), mouse_up=rec("mouse_up"), type_text=rec("type"),
        key=rec("key"), focus=rec("focus"),
    )
    if a11y:
        els = [dict(e) for e in elements]

        def find_element(name, role=None, app=None, timeout=0.0):
            for e in els:
                if e["name"] == name and (role is None or e["role"] == role):
                    return e
            raise LookupError(f"no element named {name!r}")

        def elements_fn(app=None, role=None, name=None, visible_only=True):
            return [e for e in els if (name is None or e["name"] == name) and (role is None or e["role"] == role)]

        def click_element(name, role=None, app=None, button="left", count=1):
            e = find_element(name, role, app)
            calls.append(("click_element", (name,), {"role": role, "button": button, "count": count}))
            return e

        cu.find_element, cu.elements, cu.click_element = find_element, elements_fn, click_element
    return cu


OK_BUTTON = {"app": "mousepad", "role": "push button", "name": "OK", "x": 10, "y": 20, "w": 80, "h": 30,
             "states": ["enabled", "focusable", "showing", "visible"]}


class ParseTests(unittest.TestCase):
    def test_shapes(self):
        spec = T.parse_spec({
            "name": "x", "from": "golden",
            "steps": [
                {"click": [10, 20]},
                {"double_click": {"x": 1, "y": 2}},
                {"key": "ctrl+s"},
                {"key": ["ctrl+a", "Delete"]},
                {"type": "hi\n", "name": "greeting"},
                {"wait_window": "Save As", "timeout": 5},
                {"check": {"file": "~/x"}},
                "screenshot",
            ],
            "checks": [{"window": {"title": "Mousepad", "active": True}}, {"text": "Saved", "timeout": 2}],
        })
        s = spec["steps"]
        self.assertEqual(s[0]["args"], {"x": 10, "y": 20})
        self.assertEqual(s[1]["action"], "double_click")
        self.assertEqual(s[2]["args"]["combos"], ["ctrl+s"])
        self.assertEqual(s[3]["args"]["combos"], ["ctrl+a", "Delete"])
        self.assertEqual(s[4]["label"], "greeting")
        self.assertEqual(s[5]["args"], {"title": "Save As", "timeout": 5.0})
        self.assertTrue(s[6]["check"])
        self.assertEqual(s[6]["args"]["path"], "~/x")
        self.assertEqual(s[7]["action"], "screenshot")
        self.assertEqual(spec["checks"][1]["args"]["timeout"], 2.0)
        self.assertEqual(spec["from"], "golden")
        self.assertEqual(spec["check_timeout"], 5.0)

    def assertSpecError(self, data, *needles):
        with self.assertRaises(T.SpecError) as cm:
            T.parse_spec(data)
        for n in needles:
            self.assertIn(n, str(cm.exception))

    def test_errors(self):
        self.assertSpecError({"steps": [{"clik": [1, 2]}]}, "steps[1]", "did you mean click")
        self.assertSpecError({"steps": [{"window": "x"}]}, "is a check", "- check:")
        self.assertSpecError({"checks": [{"launch": "x"}]}, "is a step")
        self.assertSpecError({"steps": [{"click": [1]}]}, "both x and y")
        self.assertSpecError({"steps": [{"click": ["a", 2]}]}, "click.x must be an integer")
        self.assertSpecError({"steps": [{"type": True}]}, "quote the value")
        self.assertSpecError({"steps": [{"click": [1, 2], "key": "a"}]}, "exactly one action")
        self.assertSpecError({"checks": [{"window": {"title": "a", "matches": "b"}}]}, "not both")
        self.assertSpecError({"checks": [{"file": {"path": "a", "exists": False, "contains": "x"}}]}, "exists: false")
        self.assertSpecError({"checks": [{"text": {"text": "a", "region": [0, 0, 0, 10]}}]}, "region")
        self.assertSpecError({"stepz": []}, "did you mean steps")
        self.assertSpecError({"from": "bad name", "steps": ["screenshot"]}, "snapshot name")
        self.assertSpecError({"name": "empty"}, "no setup, steps or checks")
        self.assertSpecError({"steps": [{"wait": 1, "timeout": 3}]}, "does not take a timeout")
        self.assertSpecError({"checks": [{"shell": {"run": "true", "matches": "("}}]}, "regular expression")

    @unittest.skipUnless(HAVE_YAML, "PyYAML not installed")
    def test_yaml_example(self):
        path = os.path.join(HERE, "..", "examples", "mousepad.yaml")
        spec = T.load_spec(path)
        self.assertEqual(spec["from"], "base")
        self.assertEqual(spec["setup"][0]["args"], {"path": "~/note.txt", "exists": False, "timeout": 0.0})

    def test_host_from_crosscheck(self):
        spec = T.parse_spec({"from": "golden", "steps": ["screenshot"]})
        T._check_host_from(spec, "golden")
        with self.assertRaises(T.SpecError):
            T._check_host_from(spec, "")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "run")
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        self.old_cu, self.old_home = T.cu, os.environ.get("HOME")
        os.environ["HOME"] = self.home

    def tearDown(self):
        T.cu = self.old_cu
        if self.old_home is not None:
            os.environ["HOME"] = self.old_home
        self.tmp.cleanup()

    def run_spec(self, data, cu):
        T.cu = cu
        spec = T.parse_spec(dict({"settle": 0, "check_timeout": 0}, **data))
        return T.run_spec(spec, self.out)

    def test_pass_and_evidence(self):
        with open(os.path.join(self.home, "note.txt"), "w") as f:
            f.write("Hello\n")
        cu = stub_cu()
        r = self.run_spec({
            "setup": [{"launch": "mousepad"}],
            "steps": [{"click": [5, 6]}, {"type": "Hello\n"}, {"key": "ctrl+s"}],
            "checks": [
                {"window": {"title": "note.txt", "active": True}},
                {"file": {"path": "~/note.txt", "equals": "Hello"}},
                {"shell": {"run": "printf 'a\\nb\\n'", "matches": "^b$"}},
            ],
        }, cu)
        self.assertEqual(r["status"], "passed", json.dumps(r["items"], indent=1))
        self.assertIn(("click", (5, 6), {"button": "left", "count": 1, "modifiers": None}), cu.calls)
        self.assertTrue(all(os.path.exists(os.path.join(self.out, i["shot"])) for i in r["items"]))
        self.assertTrue(os.path.exists(os.path.join(self.out, "result.json")))
        self.assertTrue(os.path.exists(os.path.join(self.out, "report.html")))

    def test_failed_check_keeps_evaluating(self):
        r = self.run_spec({"checks": [{"file": "~/missing.txt"}, {"window": "note.txt"},
                                      {"window": {"title": "Other", "exists": False}}]}, stub_cu())
        self.assertEqual(r["status"], "failed")
        self.assertEqual([i["status"] for i in r["items"]], ["failed", "passed", "passed"])
        self.assertIn("does not exist", r["items"][0]["message"])

    def test_failed_step_skips_the_rest(self):
        r = self.run_spec({"steps": [{"shell": "exit 3"}, {"click": [1, 1]}],
                           "checks": [{"window": "note.txt"}]}, stub_cu())
        self.assertEqual(r["status"], "failed")
        self.assertEqual([i["status"] for i in r["items"]], ["failed", "skipped", "skipped"])
        self.assertIn("exit 3", r["items"][0]["message"])

    def test_setup_failure_is_error(self):
        r = self.run_spec({"setup": [{"check": {"file": "~/nope"}}], "steps": [{"click": [1, 1]}]}, stub_cu())
        self.assertEqual(r["status"], "error")
        self.assertIn("setup failed", r["error"])

    def test_optional_failure_passes(self):
        r = self.run_spec({"checks": [{"file": "~/nope", "optional": True}, {"window": "note"}]}, stub_cu())
        self.assertEqual(r["status"], "passed")

    def test_accessibility_missing(self):
        r = self.run_spec({"steps": [{"click_element": {"name": "OK"}}],
                           "checks": [{"element": {"name": "OK"}}]}, stub_cu(a11y=False))
        self.assertEqual(r["status"], "failed")
        self.assertIn("accessibility support not available in this image", r["items"][0]["message"])
        r = self.run_spec({"checks": [{"element": {"name": "OK"}}]}, stub_cu(a11y=False))
        self.assertIn("accessibility support not available in this image", r["items"][0]["message"])

    def test_accessibility_present(self):
        cu = stub_cu(elements=[OK_BUTTON])
        r = self.run_spec({
            "steps": [{"wait_element": "OK"}, {"click_element": {"name": "OK", "role": "push button", "count": 2}}],
            "checks": [{"element": {"name": "OK", "role": "push button", "states": ["enabled"]}},
                       {"element": {"name": "Cancel", "exists": False}}],
        }, cu)
        self.assertEqual(r["status"], "passed", json.dumps(r["items"], indent=1))
        self.assertIn(("click_element", ("OK",), {"role": "push button", "button": "left", "count": 2}), cu.calls)
        r = self.run_spec({"checks": [{"element": {"name": "OK", "states": ["checked"]}},
                                      {"element": {"name": "Missing"}}]}, cu)
        self.assertEqual([i["status"] for i in r["items"]], ["failed", "failed"])
        self.assertIn("lacks state", r["items"][0]["message"])


class ReportTests(unittest.TestCase):
    def test_summary_and_junit(self):
        with tempfile.TemporaryDirectory() as root:
            def result(d, status, started, items=()):
                os.makedirs(os.path.join(root, d))
                T.write_json(os.path.join(root, d, "result.json"), {
                    "name": "Mousepad", "spec": "m.yaml", "run": int(d[-1]), "status": status,
                    "started": started, "seconds": 2.5, "items": list(items), "error": None})
            result("m-1", "passed", "2026-10-22T00:00:01Z")
            result("m-2", "failed", "2026-10-22T00:00:02Z", [
                {"phase": "checks", "index": 2, "action": "file", "label": None, "status": "failed",
                 "optional": False, "message": "~/x does not exist", "shot": "shots/004-checks-file.png"}])
            s = T.report(root)
            self.assertEqual((s["total"], s["passed"], s["failed"], s["status"]), (2, 1, 1, "failed"))
            self.assertEqual(s["runs"][1]["failures"], ["checks[2] file: ~/x does not exist"])
            self.assertEqual(s["runs"][1]["screenshot"], "m-2/shots/004-checks-file.png")
            suite = ET.parse(os.path.join(root, "junit.xml")).getroot().find("testsuite")
            self.assertEqual(suite.get("failures"), "1")
            failure = suite.findall("testcase")[1].find("failure")
            self.assertIn("does not exist", failure.get("message"))
            self.assertTrue(os.path.exists(os.path.join(root, "index.html")))


class DraftTests(unittest.TestCase):
    def test_draft_from_audit_entries(self):
        e = [
            {"seq": 1, "kind": "action", "action": "launch", "args": {"command": "mousepad"}, "ok": True, "window": ""},
            {"seq": 2, "kind": "observation", "action": "screenshot", "args": {}, "ok": True, "window": "Untitled 1 - Mousepad"},
            {"seq": 3, "kind": "action", "action": "click", "args": {"x": 5, "y": 6, "button": "left", "count": 2,
                                                                     "modifiers": None}, "ok": True,
             "window": "Untitled 1 - Mousepad"},
            {"seq": 4, "kind": "action", "action": "type", "args": {"text": {"redacted": "length", "len": 5}},
             "ok": True, "window": "Untitled 1 - Mousepad"},
            {"seq": 5, "kind": "action", "action": "key", "args": {"combos": ["ctrl+s"]}, "ok": True,
             "window": "*Untitled 1 - Mousepad"},
            {"seq": 6, "kind": "command", "action": "exec", "args": {"argv": ["ls", "-l", "my dir"], "root": False},
             "ok": True, "window": "note.txt - Mousepad"},
            {"seq": 7, "kind": "command", "action": "install", "args": {"argv": ["/usr/local/bin/ccu-install", "gimp"],
                                                                        "root": True}, "ok": True, "window": ""},
        ]
        text = T.draft(e, "golden")
        self.assertIn("from: golden", text)
        self.assertIn('- launch: "mousepad"', text)
        self.assertIn('- wait_window: "Mousepad"', text)  # from the next entry's window title
        self.assertIn("- double_click: [5, 6]", text)
        self.assertIn('- type: "TODO"', text)
        self.assertIn('- key: "ctrl+s"', text)
        self.assertIn("ls -l 'my dir'", text)
        self.assertIn("# install gimp", text)
        if HAVE_YAML:
            import yaml
            spec = T.parse_spec(yaml.safe_load(text))
            self.assertEqual(spec["checks"][0]["args"]["title"], "note.txt - Mousepad")


if __name__ == "__main__":
    unittest.main()
