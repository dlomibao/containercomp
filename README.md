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

Lifecycle   build | up | down | rm | status | open | logs [svc] | shell [--root] | exec [--root] CMD | mcp
Apps        install <apt-pkg|url|host-file>...   cp-in HOST [DEST]   cp-out SRC [HOST]
Screen      screenshot [FILE]   zoom X0 Y0 X1 Y1 [--scale N]
            click X Y [--button B] [--mods ctrl]   double_click | triple_click | right_click | middle_click
            move X Y   drag X0 Y0 X1 Y1   scroll X Y up|down|left|right [N]   mouse_down | mouse_up
            type TEXT   key COMBO...   launch CMD...   windows | active | size | cursor
            focus TITLE   wait_window TITLE [--timeout S]   wait S
            (append --shot to any screen command to screenshot afterwards)
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

## Persistence

`ccu down` stops the container and `ccu up` resumes it with everything you installed. `ccu rm` deletes the container and returns to a clean image. To keep a configured setup as a new image:

```bash
podman commit ccu localhost/containercomp:native   # overwrite the base image with the current state
```

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
rootfs/usr/local/bin/ccu-install   app installer (apt/deb/AppImage/Wine/archives)
rootfs/etc/supervisor/conf.d/ccu.conf  Xvfb, XFCE, x11vnc, noVNC
egress/                       egress proxy image (squid + config) for `ccu up --egress`
skill/containercomp/SKILL.md  Claude Code skill
install.sh                    links the CLI and skill; --mcp registers the MCP server
```

## License

MIT
