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

## G. Upstream proxy for egress mode

### G1. Where the upstream proxy comes from

- **Date:** 2026-10-22
- **Decision:** `--upstream URL|none`, then `CCU_UPSTREAM_PROXY`, then the host's `https_proxy`/`HTTPS_PROXY`/`http_proxy`/`HTTP_PROXY` (HTTPS first, since most traffic is CONNECT), with `localhost` mapped to `host.containers.internal`. `CCU_PROXY=0` skips the host variables and NO_PROXY, but an explicit `--upstream`/`CCU_UPSTREAM_PROXY` still applies.
- **Options:** (a) host env only; (b) explicit setting only; (c) both, explicit first.
- **Why:** (c). An unfiltered desktop already uses the host proxy, so a filtered one doing the same means egress mode works on a corporate network without any extra step. The explicit setting covers a proxy that differs from the shell's or a shell without one, and `none` gives a way out when the host proxy is wrong for this purpose.
- **Review:** Someone whose `HTTPS_PROXY` points at a local debugging proxy (mitmproxy, Charles) now sends filtered traffic through it too, as unfiltered desktops already do. Fallback: make host env opt-in (`CCU_UPSTREAM_PROXY=host`).

### G2. NO_PROXY hosts go direct

- **Date:** 2026-10-22
- **Decision:** Entries from the host's `NO_PROXY` become squid `always_direct` rules: host names as `.domain` (the domain and its subdomains, as in curl), IPv4 addresses, and IPv4 CIDR ranges (matched only for IP-literal requests). IPv6 entries and ports are ignored, and `*` turns off a host-derived upstream. `host.containers.internal` and loopback names always go direct, so the loopback guard still applies to them. The allowlist is checked before any of this.
- **Options:** (a) ignore NO_PROXY and send everything to the upstream; (b) always_direct for NO_PROXY entries; (c) a separate ccu setting for direct hosts.
- **Why:** (b). Intranet hosts are usually in NO_PROXY because the corporate proxy can't reach them. A CIDR rule written as a plain `dst` ACL would make squid resolve every request's name, so it's combined with an IP-literal check.
- **Review:** Tested with an intranet server reachable only on the corporate network (`HIER_DIRECT`, never in the upstream's log).

### G3. Proxy passwords: supported, kept inside the egress container

- **Date:** 2026-10-22
- **Decision:** `http://user:pass@host:port` becomes `cache_peer ... login=user:pass` (Basic). The URL's percent-encoding is passed through, since squid URL-unescapes `login=`. The file is created 0600 in a private temp dir on the Mac, `podman cp`'d into the egress container as `/etc/squid/upstream.conf` (0640 root:squid), and the temp dir is removed by an EXIT trap. Status and reset read a separate credential-free `upstream.info`. Nothing goes in env vars, labels or command-line arguments of child processes, error messages never echo the URL, and the desktop container never receives it. `https://` and SOCKS upstreams are refused with a message. NTLM/Kerberos aren't supported: point at a local cntlm/px on `localhost`.
- **Options:** (a) support it as above; (b) refuse authenticated URLs; (c) pass it as an env var to the egress container.
- **Why:** (a) is what corporate users need, and the tests found no leak (see Review). (c) would show the password in `podman inspect`. The desktop's root user can't read another container's files, and the cache manager stays denied (`http_access deny manager`, checked: 403).
- **Review:** Grepped for the test password in everything `ccu` printed (up, status, egress status/log/set, audit tail, snapshot save/list, reset), `podman inspect` of both containers, snapshot and egress image inspect/history, the desktop's and the snapshot's filesystems, the squid logs, the VM journal (container logs, podman events), the host audit mirror and every regular file in `$TMPDIR`: no match. It does show up on the host in `ps` and shell history when given with `--upstream` on the command line, so the README recommends the env var for passwords.

### G4. `reset` keeps an explicit upstream and re-reads a host-derived one

- **Date:** 2026-10-22
- **Decision:** `ccu reset` copies the old proxy's upstream config, credentials included, only if it was set with `--upstream` or `CCU_UPSTREAM_PROXY` (unless a new one is given). One that came from `HTTPS_PROXY`/`HTTP_PROXY` is looked up again from the current shell. The new upstream is resolved and validated before the old desktop is removed.
- **Options:** (a) always carry; (b) always re-derive; (c) carry explicit, re-derive implicit.
- **Why:** (c). Explicit settings behave like the allowlist, which reset already carries. Host-derived ones behave like the unfiltered passthrough, which also follows the shell when a container is recreated (office vs. home).
- **Review:** If it surprises users, switch to (a); `--upstream none` / `--upstream URL` override either way.

### G5. Changing the upstream restarts the egress proxy; old images are rebuilt

- **Date:** 2026-10-22
- **Decision:** `ccu up --upstream ...` on an existing filtered desktop rewrites the config and restarts the egress proxy (only if the config changed) instead of sending HUP. When an upstream is needed and `CCU_EGRESS_IMAGE` lacks the `containercomp.egress.upstream` label, `up` rebuilds it. An existing proxy from an old image refuses `--upstream` with instructions. `egress build` now passes the host proxy and `ccu certs` CAs as build args, like `ccu build`.
- **Options:** HUP vs restart; auto-rebuild vs refuse.
- **Why:** After a HUP, squid reused a connection opened under the previous upstream config: the first plain-HTTP request after switching from a wrong to a right password got a 400 from the upstream. With a restart this didn't happen. An old image silently ignores `upstream.conf` and would connect directly, which is exactly what fails on these networks. Proxy build args weren't recorded in the image history or config (checked).
- **Review:** The restart interrupts any downloads in progress through the proxy.

### G6. Egress log gains a routing field

- **Date:** 2026-10-22
- **Decision:** The squid log format appends `%Sh` (`FIRSTUP_PARENT`, `HIER_DIRECT`, `HIER_NONE`), and the `to_localhost` rule moved into the include so that, with an upstream, it only applies to connections squid makes itself.
- **Options:** keep the log format unchanged; add the field at the end.
- **Why:** Without the field there's no way to tell from `ccu egress log --all` whether a request used the upstream. Appended at the end, it doesn't change the `_DENIED/` matching that `egress log` relies on. Evaluating `to_localhost` for every request would make squid resolve every name, which may not work, or may be slow, where only the proxy resolves external names.
- **Review:** Anything else that parses `access.log` by column count needs to allow the extra field.
