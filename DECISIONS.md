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

## H. Gap testing

## H1. Chromium on arm64 comes from the xtradeb PPA, installed by `ccu install chromium`

- **Date:** 2026-10-22
- **Decision:** `ccu install chromium` adds the xtradeb PPA (key fetched by fingerprint and checked) and pins it so only the `chromium*` packages come from it. `firefox` uses Mozilla's apt repository, and `google-chrome` uses Google's repository on x86 and refuses on arm64.
- **Options:** (a) the xtradeb PPA deb; (b) Playwright's Chromium build (a zip from Microsoft's CDN, no apt updates, dependencies installed separately); (c) Debian's chromium deb (built for another distribution's libraries); (d) Brave or Vivaldi (vendor arm64 repos, but not Chromium itself); (e) only document a recipe.
- **Why:** (a) is a current Chromium (155) built for Ubuntu 24.04 on arm64 and amd64, updates through apt, and was tested here: the sandbox works and `ccu certs` CAs are trusted. Without built-in names, `ccu install chromium` failed ("no installation candidate") and `ccu install firefox` pulled in snapd. The cost is trusting a third-party PPA, limited by the pin.
- **Review:** If the PPA should not be trusted by default, change the `chromium` case in `rootfs/usr/local/bin/ccu-install` to print the recipe instead. If the PPA stops publishing for noble/arm64, fall back to (b).

## H2. AppImages are unpacked at install time

- **Date:** 2026-10-22
- **Decision:** `ccu install` unpacks an AppImage's squashfs with `unsquashfs` into `/opt/appimages/<cmd>` and the command runs `AppRun`. The old `APPIMAGE_EXTRACT_AND_RUN=1` wrapper remains as the fallback when unpacking fails.
- **Options:** (a) unpack once with unsquashfs; (b) keep extract-and-run and add `libz.so` (zlib1g-dev or a symlink) for runtimes that link it; (c) `--appimage-extract` at install time, which runs the AppImage's own runtime.
- **Why:** The Obsidian arm64 AppImage (electron-builder) didn't start at all with (b)'s current form: its runtime needs `libz.so`. (a) also doesn't execute the downloaded runtime, starts instantly instead of unpacking 350 MB on every launch, and worked for Obsidian (arm64, GUI), Neovim (arm64, new static runtime) and DB Browser for SQLite (x86_64, on the x86 target). (c) has the same runtime problem as (b). The cost is disk space: the unpacked copy sits next to the original file in `~/apps`.
- **Review:** `squashfs-tools` is in the image now; older containers get it from apt on first use.

## H3. Bake certutil (libnss3-tools) into the image

- **Date:** 2026-10-22
- **Decision:** The image installs `libnss3-tools`, so `ccu certs` always fills `~/.pki/nssdb`, and `ccu-install` runs a new `netsetup browsers` step after installing a browser.
- **Options:** (a) bake it in (about 7 MB); (b) keep telling users to `ccu install libnss3-tools && ccu certs apply`; (c) have netsetup install it on demand.
- **Why:** Chromium ignores the system store (tested: CA in `/etc/ssl/certs` only gives `ERR_CERT_AUTHORITY_INVALID`), so without certutil the order of "add CA" and "install browser" decided whether Chrome worked. (c) would make `ccu up` run apt, which fails offline or in egress mode.
- **Review:** None needed beyond the image size.

## H4. `VNC_PASSWORD` stays an environment variable; the password file moves to tmpfs

- **Date:** 2026-10-22
- **Decision:** Fix the root-owned password file that locked everyone out of the viewer by writing it to `/dev/shm` owned by the desktop user. Keep passing the password as a container env variable, but by name (not on the `podman run` command line), unset it before supervisord, and drop it from the environment `cu` gives to launched apps.
- **Options:** (a) env variable, reduced exposure; (b) a Podman secret mounted as a file, so `podman inspect` shows only its name; (c) `podman create`, copy the file in, then `podman start`.
- **Why:** Whoever can run `podman inspect` can also `podman exec` into the container and read the file, so (b) and (c) hide little, and (b) adds secret lifecycle to `up`/`rm`/`reset`. Worse, with (b) a snapshot or image whose entrypoint predates it would silently start without a password. Snapshots were the real leak: the old `/etc/ccu-vncpass` was committed into them; tmpfs is not.
- **Review:** README "Notes and limits" states what the password protects against.

## H5. `ccu shell` without a terminal reads commands from stdin

- **Date:** 2026-10-22
- **Decision:** When stdin isn't a TTY, `ccu shell` runs `podman exec -i` (no `-t`).
- **Options:** (a) drop `-t` without a TTY; (b) refuse with an error pointing to `ccu exec`; (c) leave it.
- **Why:** (c) hung forever with stdin at EOF and left an orphaned bash in the container. (a) makes `ccu shell < script.sh` useful and is what users expect from `bash`.
- **Review:** None.
