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
