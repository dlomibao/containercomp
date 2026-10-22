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

## E4. Secrets come only from Keychain items named `ccu-secret.<KEY>`

- **Date:** 2026-10-22
- **Decision:** `ccu secret` reads, lists and writes only login-Keychain generic passwords with service `ccu-secret.<KEY>` and account `$USER`, the same layout as the maintainer's `claude-secret.<key>` convention but with its own prefix. Key names are letters, digits, `.`, `_`, `-`.
- **Options:** (a) reuse `claude-secret.*`; (b) own prefix `ccu-secret.*`; (c) both; (d) a separate allowlist file naming permitted `claude-secret` keys.
- **Why:** `claude-secret.*` items are for Claude to use on the host (API tokens and so on); typing one into a GUI app in a container is a different decision. A separate prefix makes that decision per item and visible in Keychain Access, and `ccu secret list` can't reveal what else is stored. (d) adds a file to keep in sync for no extra protection. This is a convention, not enforcement: an agent with a host shell can run `security` itself.
- **Review:** Read the README "Secrets" threat model. Possible hardening, untested: create items whose access list doesn't trust `security`, so macOS asks before each use.

## E5. Secrets are typed with `xdotool type --file -`, logged as key and length, without an audit screenshot

- **Date:** 2026-10-22
- **Decision:** `ccu secret type KEY` pipes `security find-generic-password -g` output into `podman exec -i … cu secret --keychain type KEY`, which parses it and feeds the value to `xdotool type --file -` on stdin. The audit entry is `secret_type {"key", "len"}` with no screenshot, regardless of `CCU_AUDIT_TYPE`. Values with line breaks are refused (they would press Return). `--shot` still works because the agent has to see the result.
- **Options:** delivery: (a) xdotool from stdin; (b) set the clipboard and paste; (c) xdotool with the value as an argument. Screenshot: (a) take it as usual; (b) skip it; (c) blur the whole screenshot.
- **Why:** Delivery (b) leaves the secret on the clipboard, where `clip get` or any app can read it later; (c) puts it in `/proc/*/cmdline`. Skipping the screenshot avoids one more stored copy of a possibly unmasked field; blurring would also hide what happened, and ccu doesn't know where the field is. `-g` is used because `-w` prints non-ASCII values as bare hex, which can't be told apart from a hex password; tested with ASCII values containing quotes and backslashes and with non-ASCII ones.
- **Review:** Check `ccu audit tail` after a `secret type`. If an app rejects synthetic typing, a paste-then-clear-clipboard mode could be added later.

## E6. No MCP tool for secrets

- **Date:** 2026-10-22
- **Decision:** Secrets are only available through the host CLI (`ccu secret type`, `ccu secret env`). The MCP server gets no secret tool.
- **Options:** (a) no MCP tool; (b) an MCP tool in the container that asks the host for a secret; (c) a host-side wrapper around `ccu mcp` that intercepts a `type_secret` tool call and types the value itself.
- **Why:** The MCP server runs inside the container. (b) needs a channel from the container to the host Keychain, which lets anything running in the container (including the apps under test) request secrets. (c) is safe in principle but means parsing JSON-RPC in bash, a lot of code to maintain for a convenience. Agents using MCP still have a shell for `ccu secret type`.
- **Review:** If MCP-only clients need it, revisit (c), written in Python on the host.

## E7. `ccu secret env` for CLI tools, with the value masked in output; `ccu secret set` reads the value without echo

- **Date:** 2026-10-22
- **Decision:** `ccu secret env KEY VAR -- CMD...` runs CMD in the container with VAR set and stdin closed; stdout and stderr are streamed with the exact value replaced by `[secret KEY]` (also in the audit log's output tail). `ccu secret set KEY` prompts twice with `read -s` (or reads one line from a pipe) and feeds the value to `security add-generic-password -w` on stdin; `ccu secret rm KEY` deletes it.
- **Options:** for CLI tools: (a) env var; (b) pipe the secret to CMD's stdin; (c) nothing. For storing: (a) tell the user to run `security add-generic-password -w VALUE` (the value is briefly in `ps`); (b) a `set` command that keeps it off the command line.
- **Why:** Most CLIs that take a token read it from an env variable, and `sh -c 'printf %s "$VAR" | tool --password-stdin'` covers the stdin case. Masking catches the common accident of a tool echoing its token. `security` reads the value from stdin when it isn't given on the command line, so `set` never puts it in `ps`.
- **Review:** The interactive prompt path of `secret set` (a real terminal) was not tested unattended; the piped path was.
