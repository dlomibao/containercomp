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
Audit       audit tail [N] | verify | export [DIR]   record start [FILE] | stop | status
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

- `source` is `cli` (through `ccu`), `mcp`, or `container` (`cu` run directly inside the container). `kind` is `action`, `observation` or `command`. `ts` is when the action started, `ms` how long it took, and `window` the active window title at that moment.
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
rootfs/opt/ccu/ccu_audit.py   audit log, `cu exec`, screen recording
rootfs/usr/local/bin/ccu-install   app installer (apt/deb/AppImage/Wine/archives)
rootfs/etc/supervisor/conf.d/ccu.conf  Xvfb, XFCE, x11vnc, noVNC
skill/containercomp/SKILL.md  Claude Code skill
install.sh                    links the CLI and skill; --mcp registers the MCP server
```

## License

MIT
