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
