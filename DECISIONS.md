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
