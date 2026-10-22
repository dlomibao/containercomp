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
