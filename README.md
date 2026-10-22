# containercomp

A Linux desktop in a Podman container that [Claude Code](https://claude.com/claude-code) can drive with mouse, keyboard and screenshots. You can install and run GUI apps there, including installers that need root, without giving anything admin rights on your Mac.

- **Ubuntu 24.04 + XFCE** on Xvfb, with `xdotool` for input and `scrot` for screenshots
- **noVNC viewer** at `http://localhost:6080/`, so you can watch the agent or take over
- **Two targets**: `native` (arm64, fast) and `x86` (amd64 under emulation, with **Wine** for Windows apps)
- **`ccu` CLI** on the host that wraps everything through `podman exec`
- **Claude Code skill** (`skill/containercomp`) that teaches Claude the screenshot → act → verify loop
- **MCP server** (optional) inside the container that exposes the same controls as tools

```
 Mac host                                   Podman VM / container (Ubuntu 24.04)
┌──────────────────────┐   podman exec   ┌──────────────────────────────────────┐
│ Claude Code          │ ──────────────▶ │ cu (xdotool / scrot / wmctrl)        │
│  ├ skill: ccu CLI    │                 │ mcp_server.py (stdio, FastMCP)       │
│  └ MCP: ccu mcp      │                 │ Xvfb :1 ─ XFCE ─ your apps (sudo ok) │
│ Browser :6080 ◀──────┼── noVNC ◀────── │ x11vnc ─ websockify                  │
└──────────────────────┘                 └──────────────────────────────────────┘
```

## Requirements

- macOS (Apple Silicon or Intel) or Linux
- [Podman](https://podman.io/) 5+ (`brew install podman`). On macOS, `ccu` starts the Podman machine for you. Give it enough memory for GUI apps, for example: `podman machine init --cpus 6 --memory 12288 --now`
- Claude Code, for the skill and MCP parts
- For the `x86` target on Apple Silicon, **turn on Rosetta in the Podman machine**. Podman 5.6+ disables it by default because of a kernel bug that Apple fixed in macOS 26, and without it x86 falls back to slow qemu. With qemu, Wine can't load real `.exe` files. `ccu -t x86 up` warns you when Rosetta is off.
  ```bash
  podman machine ssh "sudo touch /etc/containers/enable-rosetta"
  podman machine stop && podman machine start
  ```

## Install

```bash
git clone https://github.com/dlomibao/containercomp.git
cd containercomp
./install.sh          # links ccu into ~/.local/bin and the skill into ~/.claude/skills
./install.sh --mcp    # optional: also register the MCP server with Claude Code
ccu up                # first run builds the image (~5 min native)
ccu open              # watch in your browser
```

Then ask Claude Code something like *"Install inkscape in the container and draw a red circle"*, or *"Use the x86 container to install ~/Downloads/setup.exe and tell me what version it installs"*.

## CLI

```text
ccu [-t native|x86] <command>

Lifecycle   build | up [--from SNAP] | down | rm | reset [--from SNAP | --base] | status | open
            logs [svc] | shell [--root] | exec [--root] CMD | mcp
Snapshots   snapshot save NAME [-m DESC] [--force] | list | rm NAME... | export NAME FILE | import FILE [NAME]
Apps        install <apt-pkg|url|host-file>...   cp-in HOST [DEST]   cp-out SRC [HOST]
Screen      screenshot [FILE]   zoom X0 Y0 X1 Y1 [--scale N]
            click X Y [--button B] [--mods ctrl]   double_click | triple_click | right_click | middle_click
            move X Y   drag X0 Y0 X1 Y1   scroll X Y up|down|left|right [N]   mouse_down | mouse_up
            type TEXT   key COMBO...   launch CMD...   windows | active | size | cursor
            focus TITLE   wait_window TITLE [--timeout S]   wait S
            (append --shot to any screen command to screenshot afterwards)
Audit       audit tail [N] | verify | export [DIR]   record start [FILE] | stop | status
Tests       test run SPEC... [--from SNAP|--base|--no-reset] [--repeat N] [--keep-going] [--record] [--out DIR]
            test validate SPEC...   test draft [--since SEQ | --last N]
Network     up --egress FILE | up --offline   egress [status] | egress log [-f] [--all] | egress set FILE|offline
```

`ccu screenshot` saves a PNG and prints its path, which Claude then reads. Screenshots are at native resolution (default 1280x800), so a pixel in the screenshot is the pixel you click.

### What `ccu install` handles

| Input | What happens |
|---|---|
| `gimp inkscape` | `apt-get install` (lists are refreshed if stale) |
| `~/Downloads/foo.deb` | copied in, `apt-get install ./foo.deb` |
| `~/Downloads/Foo-1.2.AppImage` | copied in, wrapped as `/usr/local/bin/foo` (runs with `APPIMAGE_EXTRACT_AND_RUN=1`, so no FUSE is needed) |
| `setup.exe` / `setup.msi` | x86 target only: starts the installer under Wine on the desktop, for Claude (or you) to click through |
| `.tar.gz` `.zip` `.7z` ... | extracted to `~/apps/<name>/` |
| `https://...` | downloaded inside the container, then handled as above |

### Settings

| Variable | Default | Meaning |
|---|---|---|
| `CCU_TARGET` | `native` | `native` (arm64) or `x86` (amd64 + Wine) |
| `CCU_SCREEN` | `1280x800` | screen size, applied when the container is created |
| `CCU_PORT` | `6080` / `6081` | host port for noVNC (always bound to 127.0.0.1) |
| `CCU_NAME` | `ccu` / `ccu-x86` | container name |
| `CCU_IMAGE` | `localhost/containercomp:<target>` | base image; snapshots are named `<image>-snap-NAME` |
| `VNC_PASSWORD` | *(none)* | require a password in the viewer |
| `CCU_SHOT_DIR` | `$TMPDIR/ccu` | where screenshots are saved |
| `CCU_EGRESS` | *(none)* | allowlist file or `offline`, same as `ccu up --egress` (see [Restricting network access](#restricting-network-access)) |
| `CCU_EGRESS_IMAGE` | `localhost/containercomp-egress:latest` | image for the egress proxy, built from `egress/` |

Keep the screen around 1280x800 or smaller. Larger screenshots get downscaled before the model sees them, which hurts click accuracy.

## MCP server

```bash
claude mcp add -s user containercomp -- ccu mcp
```

Tools: `screenshot`, `zoom`, `screen_info`, `click`, `move_mouse`, `drag`, `scroll`, `type_text`, `key`, `launch`, `focus_window`, `install`, `shell`, `wait`. Action tools accept `screenshot: true` to return a screenshot right after the action. `ccu mcp` does not start the container itself, because Claude Code launches MCP servers at every session start. Run `ccu up` first, then `/mcp` → reconnect if Claude Code started before the container did. For the x86 desktop, register a second server: `claude mcp add -s user containercomp-x86 -- ccu -t x86 mcp`.

## Audit and recording

Every action is logged, whether it comes from the CLI or the MCP server: clicks, moves, drags, scrolls, typing, keys, launches, focus changes, `install`, `exec`, MCP `shell`, file copies and recordings. Screenshots and zooms are logged as observations. The log is JSONL at `/var/log/ccu/audit/audit.jsonl` in the container, one entry per line:

```json
{"seq":8,"ts":"2026-10-22T04:15:55.509Z","source":"cli","kind":"action","action":"type",
 "args":{"text":{"redacted":"length","len":31},"delay_ms":12},"ok":true,"result":null,"error":null,
 "window":"Untitled 1 - Mousepad","shot":"shots/00000008-type.png","ms":237,"prev":"a5a88ec2…"}
```

- `source` is `cli` (through `ccu`), `mcp`, `test` (a `ccu test` run), or `container` (`cu` run directly inside the container). `kind` is `action`, `observation` or `command`. `ts` is when the action started, `ms` how long it took, and `window` the active window title at that moment.
- `shot` is a screenshot taken about 0.5 s after each state-changing action (2 s after `launch`), in the background so the action isn't slowed down. For screenshots and zooms it is a copy of the image the agent saw. Only the newest 500 screenshots are kept (`CCU_AUDIT_MAX_SHOTS`); older ones are deleted, and their entries stay in the log. In testing, screenshots were 20 to 100 KB; busier screens are larger.
- **Typed text is redacted by default**: `type` entries record only the length. `CCU_AUDIT_TYPE=hash` adds a sha256 of the text, so you can check whether a known value was typed, but short secrets can be brute-forced from it. `CCU_AUDIT_TYPE=full` logs the text itself. Screenshots still show whatever text appears on screen, so turn them off (`CCU_AUDIT_SHOTS=0`) for sessions that handle secrets. `exec`, `shell` and `launch` commands are logged in full, with the last 1000 characters of `exec` and `shell` output.

```bash
ccu audit tail 20             # last entries
ccu audit verify              # check the hash chain and compare with the host mirror
ccu audit export [DIR]        # copy the log + screenshots to the host, with index.html (a timeline)
ccu record start [FILE.mp4]   # screen video with ffmpeg x11grab (5 fps, H.264)
ccu record stop               # finish it and copy it to the host; prints the host path
```

`ccu audit export` writes `audit.jsonl`, `shots/`, a static `index.html` timeline (actions next to their screenshots), the host mirror and the verification result to `DIR` (default `$CCU_SHOT_DIR/audit-<name>-<time>`). A recording costs about 4% of a CPU core and around 150 MB of RAM. An idle desktop compresses to roughly 100 KB per minute. Recordings go to `$CCU_SHOT_DIR` unless you give a file, and a copy stays in `/var/log/ccu/rec/` in the container. Stopping the container mid-recording loses that recording. ffmpeg adds about 130 MB to the native image.

| Variable | Default | Meaning |
|---|---|---|
| `CCU_AUDIT` | `1` | `0` turns off logging (and the host mirror) |
| `CCU_AUDIT_SHOTS` | `1` | `0` turns off audit screenshots |
| `CCU_AUDIT_MAX_SHOTS` | `500` | screenshots to keep, oldest deleted first (`0` = no limit) |
| `CCU_AUDIT_TYPE` | `length` | how `type` text is logged: `length`, `hash` or `full` |
| `CCU_AUDIT_MIRROR` | `~/.local/state/ccu/audit/<name>.jsonl` | host mirror file, or `off` |
| `CCU_RECORD_FPS` | `5` | recording frame rate |

`ccu` reads these on every command and passes them into the container. For the MCP server, set them when you register it, for example `claude mcp add -s user containercomp -e CCU_AUDIT_TYPE=hash -- ccu mcp`.

**This is tamper-evident at best, not tamper-proof.** The agent has root inside the container and can rewrite or delete the log, and it can set `CCU_AUDIT=0` on its own commands. Two measures make that visible:
- Each entry carries the sha256 of the previous line (`prev`), so editing or deleting an entry in the middle breaks the chain.
- `ccu` mirrors each entry to a host file as it is written (it travels on the `podman exec` stderr stream), and `ccu audit verify` reports mirrored entries that are missing or changed in the container. Anything that bypasses `ccu`, such as a plain `podman exec` or `cu` run from a shell inside the container, is not mirrored and may not be logged.

An agent that can also run arbitrary commands on the host can edit the mirror too. For real assurance, ship the mirror somewhere the agent can't write.

## Snapshots and clean-state resets

`ccu down` stops the container and `ccu up` resumes it with everything you installed. `ccu rm` deletes the container. For repeatable testing, set up the app once, snapshot that state, and start every run from it:

```bash
ccu up
ccu install galculator            # install and configure the app once
ccu snapshot save golden -m "galculator, first-run dialogs dismissed"

ccu reset --from golden           # fresh container from the snapshot
# ... test run 1 ...
ccu reset                         # back to golden again (it remembers where it came from)
# ... test run 2 ...
ccu reset --base                  # back to the clean base image
```

| Command | What it does |
|---|---|
| `ccu snapshot save NAME [-m DESC]` | commits the container to the image `<image>-snap-NAME` (for example `localhost/containercomp:native-snap-golden`), with labels for the target, base image, creation time and description. A running container is paused for the commit. An existing name needs `--force`. |
| `ccu snapshot list` | snapshots of the current base image (`CCU_IMAGE`, per target); `*` marks the one the container was created from |
| `ccu snapshot rm NAME...` | deletes snapshots. One that a container still uses can't be removed until you `reset` or `rm` that container. |
| `ccu up --from NAME` | creates the container from a snapshot (if no container exists yet) |
| `ccu reset [--from NAME \| --base]` | deletes the container and creates a new one from the snapshot it came from, another snapshot, or the base image, then starts it. The snapshot is checked before anything is deleted. |
| `ccu snapshot export NAME FILE.tar` / `import FILE.tar [NAME]` | share a snapshot with a teammate (`podman save`/`load`). The archive contains the base image layers too, so expect about 1 GB or more. Import retags it under your own base image. |

`ccu status` shows which snapshot the container came from, and whether that snapshot has since been replaced or deleted.

Things to know:
- A snapshot holds the container's filesystem only: installed packages, files, app settings. It doesn't hold running processes, so apps that were open at save time aren't running after a restore. Volumes and bind mounts aren't included.
- On every boot, `/opt/ccu/bin/clean-runtime` deletes the stale X lock, sockets, D-Bus and session files that a snapshot of a live desktop carries, so a restored container boots like a fresh one. Pausing makes the snapshot a single point in time, but an app that was in the middle of saving can still leave a half-written file. Close or save apps before you snapshot when that matters.
- Snapshots share the base image's layers, so each one only costs the space of its changes. A snapshot depends on its base image, though: rebuilding the base doesn't update existing snapshots.
- `VNC_PASSWORD` is cleared from a snapshot's environment. Everything else in the container, including files under `~`, ends up in exported archives, so don't export snapshots that contain secrets.

## Test runs

`ccu test` runs an app test written once as a spec, from a known state, as often as you like. Each run resets the desktop from a snapshot, runs the spec's steps, evaluates its checks, and keeps the evidence.

```bash
ccu test run examples/mousepad.yaml                  # one run; exit 1 if it fails
ccu test run app.yaml --from golden --repeat 5       # 5 runs, each from a fresh copy of "golden"
ccu test run a.yaml b.yaml --keep-going --record     # don't stop at the first failure; record video
ccu test run app.yaml --no-reset                     # against the current desktop, while writing a spec
ccu test validate app.yaml                           # check a spec without running it
ccu test draft > app.yaml                            # turn this session's actions into a spec draft
```

A spec is YAML (or JSON). Every item is one action with its arguments:

```yaml
name: Mousepad saves a note
from: base                # snapshot to reset to before each run; "base" = the clean image
setup:                    # a failure here is an error (the environment), not a test failure
  - check: {file: {path: ~/note.txt, exists: false}}
  - launch: mousepad
  - wait_window: Mousepad
steps:
  - type: "Hello from ccu test\n"
  - key: ctrl+s
  - wait_window: Save As
  - type: /home/user/note.txt
  - key: Return
checks:                   # all are evaluated; each is retried for up to check_timeout (5 s)
  - window: {title: note.txt - Mousepad, active: true}
  - file: {path: ~/note.txt, contains: Hello from ccu test}
  - shell: {run: "wc -l < ~/note.txt", matches: "^1$"}
  - text: {text: Hello from ccu test, region: [0, 0, 1280, 400]}
```

| Steps | Arguments (a list fills them in order; a single value is the first) |
|---|---|
| `launch` | `command`, `wait_window`, `timeout` (30) |
| `wait_window` / `focus` | `title` (substring, case-insensitive), `timeout` (30) |
| `click`, `double_click`, `triple_click`, `right_click`, `middle_click` | `x`, `y`, `button`, `count`, `mods` (e.g. `ctrl`) |
| `move` / `drag` / `scroll` | `[x, y]` / `[x0, y0, x1, y1]` / `[x, y, direction, amount]` |
| `type` / `key` / `wait` | text (`\n` presses Return) / combo or list of combos / seconds |
| `shell` | `run`, `timeout` (60), `root`; fails on a non-zero exit |
| `screenshot` | optional label (every item gets a screenshot anyway) |
| `click_element` / `wait_element` | `name`, `role`, `app`, `timeout` (10); accessibility tree |
| `check` | any check, inline: `- check: {file: ~/out.txt}` |

| Checks | Passes when |
|---|---|
| `window` | `title` (substring) or `matches` (regex) is open; `active: true` = it's focused; `exists: false` = it isn't open |
| `file` | `path` exists, and `contains` / `matches` (regex) / `equals` its text; `exists: false` = it doesn't exist |
| `shell` | `run` exits with `exit` (0), and its stdout `contains` / `matches` |
| `text` | OCR finds `text` (or regex `matches`) on screen, or in `region: [x0, y0, x1, y1]`; `absent: true` inverts |
| `element` | an accessibility element with `name` (and `role`, `app`, `states`) exists; `exists: false` inverts |

Any item also takes `name:` (a label for the report), `optional: true` (a failure is reported but doesn't fail the run) and `shot: false`. Top-level settings: `timeout` (whole run, 600 s), `settle` (pause before each step's screenshot, 0.5 s), `check_timeout` (5 s).

- **Reset.** Before each run, `ccu test` does `ccu reset` to the spec's `from:` snapshot, or `--from SNAP` / `--base` if given, or (with neither) to the snapshot the container came from. `--no-reset` skips it. Opening apps is part of `setup`, since snapshots don't keep running processes.
- **Failures.** A failing step skips the rest of the run, and the checks are not evaluated. All checks are evaluated even after one fails. Without `--keep-going`, `ccu test run` stops after the first failed run.
- **"Text on screen" uses OCR** (tesseract, at 2x scale, case and whitespace ignored), so it works for any app, including Wine and canvas-drawn UIs. Prefer short, distinctive phrases and a `region`. `element` checks and `click_element` use the accessibility tree instead, which is exact but only works for apps that expose one (most GTK apps do; Wine apps generally don't).
- **Results** go to `$CCU_SHOT_DIR/test-<time>/` (or `--out DIR`): `summary.json`, `junit.xml` (for CI) and `index.html` for all runs, and per run `result.json`, `report.html` (each step with its status, message and screenshot), `shots/`, `audit/` (the run's audit log entries with their screenshots and the HTML timeline, plus `verify.txt`), the spec, `reset.log`, and `recording.mp4` with `--record`. Actions are logged with source `test`.
- **Drafts.** `ccu test draft` writes a spec from the audit log of the current container: the launches, clicks, keys and `ccu exec` commands, plus a `wait_window` wherever the active window changed. Typed text is logged only as its length (unless `CCU_AUDIT_TYPE=full`), so it appears as `TODO`. `--since SEQ` or `--last N` limits the range. Fill in the text, add checks, and run it.
- Specs and results hold the typed text and the expected values in plain text. Don't put secrets in them.
- The runner (`/opt/ccu/ccu_test.py`) and the reports run inside the container, so the host needs no Python. `ccu test` copies the runner from this checkout into the container on every run, so containers from older images work too. Older images lack PyYAML and tesseract, though (`ccu build`, or write JSON and skip `text` checks).

## Restricting network access

By default the desktop has full internet access. For security testing, you can limit it to the app under test and its dependencies:

```bash
ccu up --egress allowlist.txt   # only these domains (HTTP/HTTPS through a proxy)
ccu up --offline                # nothing at all
ccu egress                      # mode and current allowlist
ccu egress log                  # blocked requests (-f to follow, --all for allowed ones too)
ccu egress set new-list.txt     # replace the list of a running filtered desktop (or `offline`)
```

The allowlist has one domain per line. `#` starts a comment, and case doesn't matter.

```text
# app under test
staging.example.com        # exactly this host
*.example-cdn.net          # example-cdn.net and every subdomain
.example-api.com           # same as *.example-api.com
host.containers.internal   # services on your Mac (e.g. localhost:3000)
# apt mirrors (arm64 uses ports.ubuntu.com, amd64 archive/security.ubuntu.com)
ports.ubuntu.com
```

Entries are host names only: no URLs, ports or paths. An allowlisted host is allowed on any port. IP addresses are matched literally, so `http://1.2.3.4/` only works if `1.2.3.4` is listed.

**How it works.** The agent has passwordless root inside the desktop, so nothing inside that container can enforce a policy. Instead:

- The desktop sits on its own `--internal` Podman network (`<name>-internal`, with DNS disabled). It has no route off that network, no resolver, and runs without `NET_ADMIN`/`NET_RAW`, so root can't add routes, addresses or raw sockets.
- A separate squid container (`<name>-egress`) is attached to that network and to the default one. It's the only thing the desktop can reach, and it forwards a request only when the requested host matches the allowlist. HTTPS goes through `CONNECT`, so there's no TLS interception and no certificate to install.
- `HTTP_PROXY`/`HTTPS_PROXY` point at it inside the desktop. They're also kept through `sudo`, and apt is configured to use them, so browsers, curl, apt and `ccu install` work for allowed hosts. Anything that ignores proxy settings (raw sockets, UDP, QUIC, SSH, `curl --noproxy`) fails. That's the intended default.
- The noVNC viewer keeps working: Podman still publishes the port of a container on an internal network, inbound only.

`ccu down` stops both containers. `ccu rm` removes the desktop, the proxy and the network. The mode is fixed when the desktop is created. To switch between unrestricted and filtered, run `ccu rm` and then `ccu up` with or without `--egress`. Within a filtered desktop you can change the list at any time with `ccu egress set` or `ccu up --egress FILE`.

**What it doesn't cover:**

- An allowed domain is fully trusted. If you allow a shared host (a CDN, cloud storage, `github.com`), the agent can send data to anything served from it, including through domain fronting. Keep entries as narrow as you can.
- Filtering is by host name, not by URL or content. Allowed traffic isn't inspected.
- DNS: the internal network has no resolver, and the proxy rejects off-list names before it looks anything up, so DNS can't be used to send data out. The proxy resolves only names on the allowlist. A wildcard entry covers any subdomain, so that domain's DNS servers can see whatever subdomain names are requested.
- `ccu exec`, `cp-in`/`cp-out` and the viewer run from the host through Podman, not over the container's network, so the filter doesn't apply to them.
- Corporate networks: the egress proxy connects out directly and ignores your host's `HTTP_PROXY`. That works where the Mac's network client intercepts traffic transparently (Zscaler Client Connector, for example), as long as the CA is trusted (`ccu certs`). It doesn't yet work on networks that only allow traffic through an explicit upstream proxy.

## Corporate networks

Behind a TLS-inspecting proxy (Zscaler, Netskope, Palo Alto, ...), your Mac trusts the company's root CA but the container doesn't, so apt, pip, curl and browsers fail with certificate errors. `ccu` copies that trust into the container instead of turning verification off:

```bash
ccu certs sync          # macOS: export the extra roots your Mac trusts (admin/MDM/user)
ccu certs add corp.pem  # any host: trust a CA file (PEM or DER) that IT gave you
ccu certs list          # what is stored; ccu certs rm NAME removes one
ccu up                  # new containers get them; a running one is updated right away
```

- **What `sync` exports:** self-signed CA roots from `/Library/Keychains/System.keychain` and your user keychains that macOS trusts for TLS (checked with `security verify-cert`, so admin and MDM trust settings count) and that are not Apple built-in roots. Public CAs are already in Ubuntu's bundle, so in practice this is just your company's root. Certs Ubuntu already trusts are skipped again inside the container. Run it again after IT rotates the root. If your proxy needs an intermediate it doesn't send, add it with `ccu certs add`.
- **Where they go:** `~/.config/containercomp/certs/` (`CCU_CERT_DIR`), plus any files in `CCU_CA_FILE` (colon-separated, not copied). They're applied on every `ccu up`, by `certs add/rm/sync/apply` to the running container, and to `ccu build`, so the image's own `pip install` works behind the proxy.
- **What trusts them inside:**

  | Tool | How |
  |---|---|
  | curl, wget, apt, Python `ssl`/urllib (and, untested, git, Wine and Java via ca-certificates-java) | system store (`update-ca-certificates`) |
  | Python requests/httpx/pip (certifi) | `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `PIP_CERT` point at the system bundle |
  | Node.js (incl. nodejs.org/nvm builds) | `NODE_EXTRA_CA_CERTS` points at the system bundle |
  | Firefox (deb or tarball) | enterprise policy `/etc/firefox/policies/policies.json` |
  | Chrome, Chromium, Electron | `~/.pki/nssdb`, which needs certutil: `ccu install libnss3-tools && ccu certs apply` (also after installing a browser later) |

  The env variables are set in the image, so containers from images built before this feature need `ccu build` and a new container (`ccu rm && ccu up`) for the Python certifi and Node cases; the system store, Firefox and NSS work on older containers too. Tools with their own pinned bundle (some Java apps with a private keystore, a few Electron apps) may still need their own configuration.

**Proxy.** If `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY` or `NO_PROXY` (either case) are set when the container is created, `ccu up` passes them in, in both cases, and writes them to apt's config and sudo's `env_keep`, so `sudo apt` works too. `ccu build` passes them as build args. A proxy on `localhost`/`127.0.0.1` on your Mac (a local PAC helper or forwarding proxy, for example) is rewritten to `host.containers.internal`, which is how the container reaches the Mac. `localhost` and `host.containers.internal` are added to `NO_PROXY`. Proxy settings are fixed when the container is created, so after changing them run `ccu rm && ccu up`. Set `CCU_PROXY=0` to pass nothing (that also stops Podman's own proxy passthrough). PAC files aren't evaluated, so point `HTTPS_PROXY` at the actual proxy.

**Pulling the base image (`podman pull`) behind the proxy.** That runs in the Podman VM, which has its own trust store. Opt in with:

```bash
ccu certs sync --machine   # or: ccu certs apply --machine
```

It installs the same CAs as `/etc/pki/ca-trust/source/anchors/containercomp.pem` in the VM and runs `update-ca-trust`. It changes the VM that every Podman container on your Mac shares, which is why it never happens implicitly. If pulls still fail, restart the VM (this stops all containers): `podman machine stop && podman machine start`.
## Notes and limits

- **Snaps don't work in containers.** That includes Ubuntu's `firefox` and `chromium` apt packages. Use vendor repos, tarballs or AppImages instead.
- **Electron and Chromium apps** usually need `--no-sandbox` in a container.
- **x86 on Apple Silicon** runs through Rosetta (see Requirements). It's fine for installers and everyday apps, though slower than native.
- **Wine** is the Ubuntu 24.04 package (Wine 9.0) plus `winetricks`, with the Wine prefix already set up in the image.
  - **64-bit Windows programs work on Apple Silicon.** Tested with the 7-Zip x64 `.msi`: it installs through the GUI and the installed app runs.
  - **32-bit Windows programs (PE32) don't work on Apple Silicon.** That includes many `setup.exe` stubs, even ones named "x64". They need Wine's 32-bit side, and Rosetta doesn't translate 32-bit Linux code, so it falls back to qemu-i386, which fails. The new-WoW64 Wine in Ubuntu 26.04 and WineHQ's Wine 11 also failed in testing. On a real x86 Linux host, 32-bit programs run normally. Prefer 64-bit installers or `.msi` files; `ccu install` warns when it detects a 32-bit `.exe`.
  - App compatibility varies; check [WineHQ AppDB](https://appdb.winehq.org/).
- **No GPU.** Rendering is software-only (llvmpipe).
- **Security:** the viewer listens only on `127.0.0.1` and has no password by default. The container runs rootless under Podman, so root inside it is not root on your Mac.

## Layout

```
Containerfile                 image definition (one file, both targets)
bin/ccu                       host CLI
rootfs/opt/ccu/cu.py          in-container control library + CLI (`cu`)
rootfs/opt/ccu/mcp_server.py  MCP server (FastMCP, stdio)
rootfs/opt/ccu/ccu_audit.py   audit log, `cu exec`, screen recording
rootfs/opt/ccu/ccu_test.py    test runner and reports for `ccu test`
examples/                     example test specs
tests/                        unit tests (python3 -m unittest discover -s tests)
rootfs/usr/local/bin/ccu-install   app installer (apt/deb/AppImage/Wine/archives)
rootfs/etc/supervisor/conf.d/ccu.conf  Xvfb, XFCE, x11vnc, noVNC
egress/                       egress proxy image (squid + config) for `ccu up --egress`
skill/containercomp/SKILL.md  Claude Code skill
install.sh                    links the CLI and skill; --mcp registers the MCP server
```

## License

MIT
