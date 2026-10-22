# Decisions

Judgment calls made without the maintainer present, so they can be reviewed afterwards. Each entry says what to check and what the fallback is.

IDs: `Dn` are cross-cutting decisions. Per-feature decisions use the feature's letter: `A` accessibility tree, `B` test runner, `C` multiple desktops, `D` prebuilt images and CI, `E` clipboard and secrets, `G` upstream proxy and gap testing.

## D1. Build the six features in parallel worktrees and merge to main

- **Date:** 2026-10-22
- **Decision:** One agent per feature, each in its own git worktree, then merged into `main` one at a time with a combined test after each merge.
- **Options:** (a) parallel worktrees, merged by hand; (b) one feature after another on main; (c) one PR per feature, left open for review.
- **Why:** (a) matches how the previous round went and finishes overnight. (b) is too slow. (c) leaves the features untested against each other, and that is where the last round found its worst bugs.
- **Review:** `git log --first-parent main` lists one merge per feature, so a single feature can be reverted on its own. Fallback: revert a merge and reopen that work as a PR.

## D2. Secrets support goes to a PR, not main

- **Date:** 2026-10-22
- **Decision:** Clipboard sharing is merged to `main`. The Keychain secrets feature is pushed to a branch with an open PR and is not merged.
- **Options:** (a) merge both; (b) PR for secrets only; (c) leave secrets unbuilt.
- **Why:** How secrets reach the container (and what can leak into logs, history or the agent's context) is a security design the maintainer should approve. (b) still gets the work done and tested.
- **Review:** Read the PR's threat model section before merging.

## D3. Agents may push a CI test branch; images publish only from main

- **Date:** 2026-10-22
- **Decision:** The CI agent may push a `ci-*` branch to GitHub so the workflow actually runs, and deletes it afterwards. Pushes to `main` build, smoke test and publish images to `ghcr.io/dlomibao/containercomp`; other branches and PRs only build and test.
- **Options:** (a) test the workflow on a branch; (b) merge an untested workflow; (c) skip CI.
- **Why:** A workflow that has never run usually fails. Public repositories run Actions for free.
- **Review:** New GHCR packages may start out private. If `ccu up` can't pull without logging in, make the package public in its GitHub settings; `ccu` falls back to a local build either way.

## D4. `ccu certs sync --machine` is tested on a throwaway Podman machine, if at all

- **Date:** 2026-10-22
- **Decision:** Don't run it against `podman-machine-default`. If time allows after everything is merged, test it on a temporary second machine and delete that machine afterwards.
- **Options:** (a) run it on the default machine with a test CA and remove it afterwards; (b) a throwaway machine; (c) skip it.
- **Why:** (a) briefly makes the VM trust a CA whose private key sits in a temp directory. (b) has no such window, but only one machine can run at a time, so it has to wait until nothing else is running.
- **Review:** The final summary says whether this happened.

## D5. The CI and prebuilt-image work waits on a local branch until the token can push workflows

- **Date:** 2026-10-22
- **Decision:** The `gh` token on this Mac has the scopes `gist`, `read:org` and `repo` but not `workflow`, and GitHub rejects any push that adds a file under `.github/workflows/`. That includes pushing `main` once the CI work is merged. So the CI work (feature D) is merged into a local branch, `ci-prebuilt`, on top of everything else, and tested locally. `main` gets every other feature and is pushed as usual.
- **Options:** (a) local `ci-prebuilt` branch, pushed after the maintainer grants the scope; (b) merge it into local `main` and stop pushing `main`; (c) merge everything except the workflow file; (d) push some other way, e.g. a different credential.
- **Why:** (b) would hold up every other feature. (c) ships `ccu pull` with nothing ever published, so every first `up` would try GHCR and fail before building. (d) works around a permission the maintainer hasn't granted.
- **Review:** Run `gh auth refresh -h github.com -s workflow`, then push `ci-prebuilt` to `main` (a fast-forward) and watch the first run. Fallback: merge only `.github/workflows/ci.yml` and the `ci/` scripts, leaving out pulling.

## C. Multiple desktops

## C1. A desktop remembers its target and base image; a conflicting `-t` is an error

- **Date:** 2026-10-22
- **Decision:** `ccu up` labels new desktops with `containercomp.target` (already there) and `containercomp.image`. Every command reads them back, so `ccu -n foo screenshot` works on an x86 desktop without `-t x86`, and snapshot commands use the desktop's own base image. An explicit `-t` or `CCU_TARGET` that contradicts an existing desktop's label stops with an error. `CCU_IMAGE` still overrides the label. Without `-n`, the name still follows the target (`ccu` / `ccu-x86`).
- **Options:** (a) labels decide, a conflicting explicit target is an error; (b) labels decide and silently override `-t`; (c) no memory: `-t` must be repeated on every command, as before; (d) encode the target in the name (`foo-x86`).
- **Why:** (a) makes `-n` alone enough, which is what parallel agents will type, and still catches a mistaken `-t` instead of building the wrong image or listing the wrong snapshots. (b) hides mistakes. (c) is error-prone with many desktops. (d) constrains names.
- **Review:** `CCU_TARGET` exported in a shell profile now refuses desktops of the other target; if that bites, treat only the `-t` flag as explicit. Older containers without `containercomp.image` fall back to the target's default image, as before.

## C2. Automatic viewer ports: scan up from the default, read the port back from the container

- **Date:** 2026-10-22
- **Decision:** Without `CCU_PORT`, a new desktop takes its target's default (6080/6081) or the next port above it that no Podman container claims (running or stopped, from `podman ps -a`) and nothing on the host answers on 127.0.0.1 (bash `/dev/tcp` probe). If `podman run` still fails with "address already in use" / "proxy already running" (a race with another `up`, or a socket bound but not listening), the half-created container is removed and the next port is tried, up to 5 times. Existing desktops' ports are read from the container's port binding (`HostConfig.PortBindings`, which also works when it's stopped, unlike `podman port`). `reset` keeps the port. An explicit `CCU_PORT` wins and fails cleanly if taken.
- **Options:** (a) scan + retry, port from the binding; (b) hash the name into a port range; (c) let Podman pick a random port (`-p 127.0.0.1::6080`); (d) a host-side registry file.
- **Why:** (a) keeps the familiar 6080/6081 for the defaults and small predictable numbers for others, and survives parallel `up`s. (b) collides and can't avoid foreign listeners. (c) gives ports that change on every `reset` and are hard to remember. (d) goes stale when containers are removed outside `ccu`.
- **Review:** Tested with two `up`s started at the same moment (6082/6083) and with 6084 bound-but-not-listening (got 6085 via the retry). The scan covers 100 ports above the default.

## C3. `ccu ls` lists labeled desktops plus the old default names

- **Date:** 2026-10-22
- **Decision:** New desktops carry `containercomp.desktop=1` (cleared in snapshots, so containers that other tools start from a snapshot image aren't listed). `ccu ls` shows labeled containers plus containers named `ccu`/`ccu-x86` whose image is `localhost/containercomp:*` (desktops from before this change). Desktops created under other names by an older `ccu` still work with `-n` but aren't listed until recreated.
- **Options:** (a) dedicated label + default names; (b) any container with a `containercomp.target` label; (c) any container whose image is `localhost/containercomp:*`.
- **Why:** (b) and (c) also match foreign containers started from snapshot images (the snapshot carries the target label). (a) never lists something `ccu` didn't create, apart from the two well-known names.
- **Review:** If old custom-named desktops should be listed, add (b) as a fallback for unlabeled containers.

## C4. Desktop names: same characters as snapshots, at most 48, not ending in `-egress`

- **Date:** 2026-10-22
- **Decision:** `-n`/`CCU_NAME` must match `[A-Za-z0-9][A-Za-z0-9_.-]*`, be at most 48 characters, and not end in `-egress`.
- **Options:** (a) as above; (b) only lowercase letters, digits, `-` (strict host names); (c) no validation, let Podman fail.
- **Why:** (a) is what Podman accepts for container and network names, and matches the snapshot name rule. 48 keeps `NAME-internal` and the host name under 63 characters. `-egress` would collide with another desktop's proxy container. Podman accepted `_` and `.` in host and network names in testing. (c) gives confusing errors halfway through `up`.
- **Review:** Names are not checked against existing non-desktop containers; `podman run` fails if the name is taken.

## C5. No `--all` for down/rm

- **Date:** 2026-10-22
- **Decision:** `down`, `rm` and `reset` act on one desktop. There's no `--all`.
- **Options:** (a) none; (b) `down --all` only; (c) `down --all` and `rm --all` behind a confirmation.
- **Why:** The point of the feature is several agents with their own desktops; one agent running `down --all` would stop everyone else's. Stopping several by name is a one-line loop over `ccu ls`. (b) could not be tested here without stopping other agents' desktops.
- **Review:** If wanted, add `down --all` for humans only (not mentioned in the skill).

## E. Clipboard and secrets

## E1. `ccu clip` uses CLIPBOARD by default, PRIMARY only with `--primary`

- **Date:** 2026-10-22
- **Decision:** Every `clip` command works on the X `CLIPBOARD` selection. `--primary` (CLI) or `primary: true` (MCP) switches to `PRIMARY`. `clip paste` and MCP `paste` refuse `PRIMARY`.
- **Options:** (a) CLIPBOARD only; (b) set both selections on every `set`; (c) CLIPBOARD by default, PRIMARY on request.
- **Why:** ctrl+c/ctrl+v, "Copy" buttons and the Mac clipboard all map to CLIPBOARD. PRIMARY changes whenever text is highlighted, so (b) would be overwritten by the next mouse selection and surprise apps that paste on middle click. (c) keeps PRIMARY reachable for the few apps that only expose a selection.
- **Review:** If an app's copy action turns out to land only in PRIMARY, `ccu clip get --primary` reads it. Fallback: none needed.

## E2. Text and PNG images; other types only with an explicit `--type`

- **Date:** 2026-10-22
- **Decision:** Text (UTF-8) is the default. PNG images work in both directions: `--type image/png` on get/set, automatic in `push`/`pull`, and MCP `clipboard_get` returns an image. `clip get` without `--type` fails with a hint when the clipboard holds only an image, instead of writing binary data to the terminal. Mac images go through `osascript` (`«class PNGf»`).
- **Options:** (a) text only, fail clearly on images; (b) text plus image/png; (c) arbitrary MIME types everywhere, including the Mac bridge.
- **Why:** (b) was cheap: xclip serves any target, and AppleScript reads and writes PNG pasteboard data without extra tools. Screenshots and diagrams are the common non-text case. (c) would need a native helper for the Mac pasteboard.
- **Review:** Tested: Mac PNG -> desktop -> byte-identical PNG back out, and desktop PNG -> Mac. Rich text from the Mac arrives as plain text only.

## E3. Clipboard contents are audited like typed text, without screenshots; text travels on stdin

- **Date:** 2026-10-22
- **Decision:** `clipboard_get` (observation) and `clipboard_set` (action) entries log the selection, type and the data per `CCU_AUDIT_TYPE` (length by default, sha256 with `hash`, the text with `full`); binary data only by size. Neither takes an audit screenshot. `clip paste` logs a separate `key` entry, which does get one. The host CLI always pipes the text over stdin, even for `ccu clip set TEXT`, so the value is not in the container's process listing.
- **Options:** (a) log clipboard text in full; (b) reuse the `type` redaction setting; (c) never log it, not even a hash.
- **Why:** The clipboard regularly carries the same things `type` does (and sometimes more: tokens copied out of apps), so it should follow the same user-chosen setting. Setting or reading the clipboard doesn't change the screen, so a screenshot would only cost disk and context.
- **Review:** `ccu audit tail` after a `clip set` shows `{"redacted":"length",...}`. Fallback: add a separate `CCU_AUDIT_CLIP` setting if someone wants the two to differ.

## A. Accessibility tree

## A1. Read the tree with gi.repository.Atspi directly

- **Date:** 2026-10-22
- **Decision:** `cu.py` imports libatspi through GObject introspection (`python3-gi`, `gir1.2-atspi-2.0`, about 1 MB) and calls interface methods through their class (`Atspi.Text.get_text(node, ...)`).
- **Options:** (a) `gi.repository.Atspi`; (b) `python3-pyatspi`, a thin wrapper over (a); (c) dogtail.
- **Why:** (a) is what (b) and (c) are built on, with fewer packages and nothing hidden. Calling through the class matters: `node.get_text` resolves to a deprecated `Atspi.Accessible` getter with a different signature.
- **Review:** If a later libatspi changes the GI API, the failures surface as `cu: ...` errors from `ccu tree`. Fallback: switch to pyatspi.

## A2. Name matching: case-insensitive, exact match first, then substring

- **Date:** 2026-10-22
- **Decision:** Names are compared case-insensitively with whitespace collapsed and `…` read as `...`. If any element matches exactly, only exact matches count; otherwise substring matches do. More than one candidate is an error that lists up to five; `index` picks one. `elements(name=...)` / `tree --name` filter by substring.
- **Options:** (a) exact, case-sensitive; (b) case-insensitive substring only; (c) exact first, then substring; (d) regular expressions.
- **Why:** (a) breaks on "Save As..." vs "Save As…" and trailing padding (Mousepad's menu items end in spaces). (b) makes "Save" ambiguous with "Save As...". (c) does what a person means in both cases and never silently picks one of several. (d) costs quoting trouble on the command line.
- **Review:** Try `ccu click_element save` in a Mousepad File menu: it picks "Save", not "Save As...".

## A3. click_element moves the real mouse; the AT-SPI action is opt-in

- **Date:** 2026-10-22
- **Decision:** `click_element` clicks the element's centre with xdotool, as a user would, after checking which window is under the pointer: if it belongs to another process, it raises the element's own window (matched by pid and title) and checks again, else fails. `action=True` (`--action`) runs the element's AT-SPI action (click/press/activate/toggle) instead, and may target elements that are showing but off the screen.
- **Options:** (a) mouse by default, action opt-in; (b) action by default; (c) action when available, mouse otherwise.
- **Why:** The mouse path exercises the app the way a user does (focus, pointer events, button/count, double-clicks), and the result is visible in screenshots and the recording. Actions skip all of that and can "click" things a user couldn't, which hides bugs in a test run. The action is still useful for dialogs taller than the screen (Mousepad's Save As dialog puts Save/Cancel at y=889 on an 800px screen).
- **Review:** Auto-raise changes stacking order on its own; if that surprises anyone, make it an error instead (the check is in `_uncover`).

## A4. Roles use AT-SPI names with underscores; a filter matches the last word

- **Date:** 2026-10-22
- **Decision:** Roles are reported as `push_button`, `menu_item`, `check_box`, ... and `role="button"` matches any role ending in `_button`.
- **Options:** (a) AT-SPI names with spaces ("push button"); (b) underscores; (c) our own short vocabulary (button, field, ...).
- **Why:** (b) is one shell word and one token per role, and stays a 1:1 mapping to AT-SPI so documentation for it applies. (c) would lose distinctions (toggle vs push) and need upkeep.
- **Review:** Feature B's test runner may depend on these strings; change them only together.

## A5. What the tree lists by default

- **Date:** 2026-10-22
- **Decision:** Default: only elements that are showing and on the screen; unnamed, textless layout nodes (filler, panel, scroll_pane, a row's wrapper cell) are skipped unless `all_nodes`/`--all`; a label whose name equals its parent's (GTK 4 and web button captions) is skipped; unnamed fields take their label's name (LABELLED_BY, or the editable combo box they sit in). Limits: 300 elements (MCP `ui_tree`: 100), 5000 nodes visited, depth 40.
- **Options:** (a) every node; (b) the filtered list above; (c) only interactive roles.
- **Why:** (a) is 318 lines for an idle Mousepad, mostly noise, which defeats the token savings. (c) drops labels and status text the agent needs to read. (b) lists 8 lines for idle Mousepad and 28 for a FeatherPad window. Covered windows are still listed (deciding occlusion per element would need a pointer probe each); click_element handles it at click time.
- **Review:** `--all --hidden` shows everything when the filtered view hides something needed.

## A6. GTK 4: window-relative positions plus the window's origin

- **Date:** 2026-10-22
- **Decision:** For apps whose toolkit is GTK 4.x, extents are read in WINDOW coordinates and offset by the origin of the X window with the same pid and title.
- **Options:** (a) offset as above; (b) report GTK 4 positions as unknown and require `--action`; (c) leave them as reported.
- **Why:** GTK 4.14 returns 0,0 for every SCREEN position on X11 (tested with GNOME Calculator), so (c) clicks the top-left corner. (a) clicked 7 × 6 = and got 42. GTK 4 popovers are separate surfaces, so their contents may come out misplaced; that is documented.
- **Review:** If a newer GTK 4 starts reporting real screen positions, the offset would double them; check `ccu tree --app calculator` against a screenshot after GTK upgrades.

## A7. Environment: only QT_LINUX_ACCESSIBILITY_ALWAYS_ON; Chromium needs its flag

- **Date:** 2026-10-22
- **Decision:** The image sets `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`. Nothing is injected for Chromium/Electron; the docs say to launch them with `--force-renderer-accessibility`.
- **Options:** (a) set only what was shown to help; (b) also set `QT_ACCESSIBILITY=1` and `ACCESSIBILITY_ENABLED=1` "just in case"; (c) have `ccu launch` add the Chromium flag automatically.
- **Why:** Tested: Qt 5 (FeatherPad) publishes its tree with neither Qt variable; Qt 6 (PyQt6) needs `QT_LINUX_ACCESSIBILITY_ALWAYS_ON` and `QT_ACCESSIBILITY=1` alone does nothing. Electron 32 published only its frame with `ACCESSIBILITY_ENABLED=1` and with `org.a11y.Status` IsEnabled/ScreenReaderEnabled set to true; only the flag worked. (c) would need to recognise every Chromium-based binary and could break apps that reject unknown flags.
- **Review:** If a Chromium-based app is a common target, consider a wrapper for it at install time.

## A8. Long-lived callers: drain events and clear the cache before each walk

- **Date:** 2026-10-22
- **Decision:** Before each walk, `cu` runs pending GLib main-loop events and calls `clear_cache()` on the desktop, so the MCP server (one process for the whole session) sees new apps and closed dialogs.
- **Options:** (a) drain and clear in-process; (b) run each tree query in a fresh `cu` subprocess; (c) run a GLib main loop in a thread.
- **Why:** (a) tested correct in a single MCP session (a dialog closed by `click_element` was gone from the next `ui_tree`; a newly launched terminal appeared) with no per-call process start. (c) would make libatspi calls cross threads.
- **Review:** If the MCP server ever shows stale trees, fall back to (b).

## B. Test runner

## B1. Specs are YAML (JSON also accepted), parsed inside the container

- **Date:** 2026-10-22
- **Decision:** `ccu test` specs are YAML with one-key items (`- click: [x, y]`, `- file: {path: ..., contains: ...}`). The runner in the container parses them with `python3-yaml`, now in the image. JSON works too, since it is valid YAML (and it is the fallback on images without PyYAML). The host reads only the top-level `from:` line, with a regex, to know which snapshot to reset to; the runner re-reads it and fails the run if the two disagree.
- **Options:** (a) YAML parsed in the container; (b) JSON only; (c) YAML parsed on the host (macOS has no guaranteed PyYAML, or Python); (d) a line-based format that bash can parse.
- **Why:** YAML is what agents and people write most easily, and the container already has Python. (b) is noisy to write by hand, (c) adds a host dependency, (d) is one more format to learn.
- **Review:** A `from:` written in an unusual way (flow style, anchors) is caught by the cross-check and reported. Fallback: pass `--from`.

## B2. "Text on screen" checks use OCR; accessibility gets its own `element` check

- **Date:** 2026-10-22
- **Decision:** `text` checks screenshot the screen (or a `region`), scale it 2x, and run tesseract (page modes 11 and 3), then match case- and whitespace-insensitively. `element` checks and `click_element`/`wait_element` steps use agent A's accessibility functions, looked up with `getattr` so they fail with "accessibility support not available in this image" when absent. `tesseract-ocr` adds 26 MB to the image.
- **Options:** (a) OCR only; (b) accessibility tree only; (c) both, as separate checks; (d) one `text` check that tries the accessibility tree first and falls back to OCR.
- **Why:** OCR works for any pixels, including Wine, canvas and terminal UIs, and needs nothing from the app. The accessibility tree is exact but only covers apps that expose one. (d) hides which method decided a result, which makes failures hard to read. In testing, OCR read Mousepad's text and title bar correctly in about 0.3 s.
- **Review:** OCR can misread small or styled text; the failure message includes what OCR saw. If that is a problem in practice, add a `fuzzy:` option rather than loosening the default.

## B3. Steps run in one runner process in the container; the host only resets and collects

- **Date:** 2026-10-22
- **Decision:** For each run, `ccu test` resets the container, copies the spec in and runs `/opt/ccu/ccu_test.py run`, which executes every item through the `cu` library, polls checks, saves a screenshot after each item, and writes `result.json` and `report.html`. The host copies the run directory back out.
- **Options:** (a) one runner process in the container; (b) the host issues one `ccu` command per step.
- **Why:** (b) costs a `podman exec` per step and per poll and puts the spec logic in bash 3.2. (a) keeps `bin/ccu` changes small.
- **Review:** `ccu test` copies the runner from the checkout into the container on every run (like `netsetup`), so older containers work and the runner always matches the CLI. `cu.py` and `ccu_audit.py` still come from the image.

## B4. Resets go through a child `ccu reset`

- **Date:** 2026-10-22
- **Decision:** `test_one` runs `"$REPO/bin/ccu" reset ...` as a child process, with its output in the run's `reset.log`, instead of calling `cmd_reset` in the same shell.
- **Options:** (a) child process; (b) call `cmd_reset` directly; (c) call it in a subshell.
- **Why:** `cmd_reset` ends the script with `die` on failure, which would abort all remaining runs, and `set -e` is ignored inside a function called from an `if`. A child process keeps the reset logic exactly as it is and turns a failed reset into one errored run. (c) has the same `set -e` problem as (b).
- **Review:** None needed; the reset behavior is `ccu reset`'s.

## B5. Failure semantics and what "base" means

- **Date:** 2026-10-22
- **Decision:** A failing step skips the rest of that run (checks are marked skipped); all checks are evaluated even after one fails; a failure in `setup` makes the run an `error` rather than `failed` (JUnit `<error>` vs `<failure>`). Checks retry for up to `check_timeout` (5 s). Without `--keep-going`, `ccu test run` stops after the first failed run. The reset target is `--from`/`--base` if given, else the spec's `from:`, else the snapshot the container came from. `base` (in `from:` or `--from base`) means the base image, so a snapshot named `base` can't be used by `ccu test`.
- **Options:** for steps: stop, or continue; for `base`: a reserved word, a separate `base: true` key, or `from: ""`.
- **Why:** Steps after a failure act on an unknown screen and can do damage; checks are independent and the whole picture helps. `from: base` is the most readable, and naming a snapshot "base" is unlikely.
- **Review:** If someone needs a snapshot named `base`, rename it.

## B6. Evidence is the run's audit segment; reports are built in the container

- **Date:** 2026-10-22
- **Decision:** The runner logs `test_start`/`test_end` entries (source `test`) and exports the entries between them, with their screenshots, to the run's `audit/`, rendered by the existing timeline (`render_html` gained an optional `entries` argument). The aggregate `summary.json`, `junit.xml` and `index.html` are built by copying the runs' `result.json` files into the container and running the report there; if the container isn't running at the end, only a plain `summary.txt` is written.
- **Options:** (a) segment export; (b) `ccu audit export` of the whole log per run; (c) reports on the host in bash or host Python.
- **Why:** With a reset the whole log is the run anyway, but with `--no-reset` (b) would mix runs and copy up to 500 screenshots each time. (c) needs Python on the host or JSON handling in bash.
- **Review:** On images without the `render_html` change, the run links `audit/audit.jsonl` instead of a timeline.

## B7. No MCP `run_test` tool

- **Date:** 2026-10-22
- **Decision:** The MCP server gets no test tool.
- **Options:** (a) none; (b) a tool that runs a spec on the current desktop without a reset; (c) a tool that asks the host to reset.
- **Why:** The reset that makes runs repeatable happens on the host, and the MCP server runs inside the container. (b) would produce results that look repeatable but aren't, and (c) needs a host channel that doesn't exist. Agents that use MCP still have Bash for `ccu test run`.
- **Review:** If MCP-only clients need it, (b) could be added as `try_spec`, labeled as not resetting.
