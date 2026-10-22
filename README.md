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
| `CCU_IMAGE` | `localhost/containercomp:<target>` | image tag to build and run |
| `VNC_PASSWORD` | *(none)* | require a password in the viewer |
| `CCU_SHOT_DIR` | `$TMPDIR/ccu` | where screenshots are saved |

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
skill/containercomp/SKILL.md  Claude Code skill
install.sh                    links the CLI and skill; --mcp registers the MCP server
```

## License

MIT
