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
