---
name: containercomp
description: Drive a Linux desktop running in a Podman container (Ubuntu 24.04 + XFCE) with mouse, keyboard and screenshots through the `ccu` CLI. Use when the user wants to install, run, test or operate a GUI app in a sandbox/container instead of on their Mac, when an app needs root/admin to install, for Linux or x86 apps, for Windows .exe/.msi apps through Wine, or when they say "containercomp", "ccu", or "computer use in the container".
---

# containercomp: computer use inside a container

`ccu` controls a disposable Ubuntu desktop in Podman. Inside the container you have passwordless sudo, so installers that need admin rights run there without touching the Mac. The user can watch, or take over, at the noVNC URL that `ccu up` prints.

Two targets:
- `native` (default): an arm64 desktop. It's fast. Use it for anything that has an arm64 Linux build.
- `x86` (`ccu -t x86 ...` or `CCU_TARGET=x86`): an amd64 desktop under emulation, with Wine. Use it for x86-only Linux binaries and Windows `.exe`/`.msi` files. It's noticeably slower, so allow longer waits.

Every command below takes `-t x86` right after `ccu` to address the x86 desktop.

## Start up

```bash
ccu status                 # is it built/running?
ccu up                     # builds the image on first run (several minutes), then starts it
```

Tell the user the viewer URL (`http://localhost:6080/` for native, `:6081` for x86).

## Install apps

```bash
ccu install gimp inkscape                  # apt packages
ccu install ~/Downloads/app_arm64.deb      # host file: copied in, then installed
ccu install ~/Downloads/Tool.AppImage      # becomes a command: `tool`
ccu install https://example.com/app.tar.gz # downloaded, then extracted to ~/apps/app/
ccu -t x86 install ~/Downloads/setup.exe   # starts the Windows installer on the desktop
```

A `.exe` or `.msi` install opens the installer GUI. Finish it with the screen-control loop below. Windows programs are installed under `~/.wine/drive_c/Program Files/`. Launch one like this: `ccu -t x86 launch 'wine "C:\Program Files\7-Zip\7zFM.exe"'`. Pass the whole command as one single-quoted string.

On Apple Silicon only **64-bit** Windows programs work. 32-bit ones (`file` reports `PE32 executable`) fail with errors such as `status c0000018` or a qemu crash. If `ccu install` warns that a file is 32-bit, look for the vendor's 64-bit installer or `.msi` instead of retrying. If `ccu -t x86 up` warns that Rosetta is off, tell the user. The fix needs the user's approval because it restarts the Podman VM.

Watch for these:
- Ubuntu's `firefox` and `chromium` apt packages are snap stubs, and snaps don't work in containers. Install browsers from vendor repos or tarballs instead.
- Electron and Chromium-based apps usually need `--no-sandbox`.
- To find the binary a package installed, run `ccu exec dpkg -L <pkg> | grep bin/`.

## Certificate errors and proxies (corporate networks)

If downloads in the container fail with `certificate verify failed`, `SSL certificate problem` or `unable to get local issuer certificate` while the same URL works on the Mac, the user is probably behind a TLS-inspecting proxy. Don't turn verification off (`-k`, `--trusted-host`, `verify=False`). Instead:

```bash
ccu certs sync             # trust the extra roots the Mac trusts (applies to the running container)
ccu certs add ~/corp.pem   # or a CA file the user names
```

For Chrome/Chromium, also run `ccu install libnss3-tools && ccu certs apply`. Proxy variables are only picked up when the container is created, so if `HTTPS_PROXY` changed, a `ccu rm && ccu up` is needed (ask first). `ccu certs sync --machine` changes the shared Podman VM; run it only when the user asks, for `podman pull` failures.

## The control loop

1. **Look.** Run `ccu screenshot`. It prints a PNG path. Read that file with the Read tool to see the screen.
2. **Act.** Run a single action using pixel coordinates taken from the latest screenshot. The screenshot is at native resolution (1280x800 by default), so image pixels are screen pixels.
3. **Verify.** Take another screenshot. Adding `--shot` to the action does steps 2 and 3 together: `ccu click 640 400 --shot`.
4. Repeat until done.

```bash
ccu launch mousepad                 # GUI apps go in the background; use launch, not exec
ccu wait_window Mousepad            # block until the window appears (--timeout 60 on x86)
ccu click 300 200 --shot
ccu type "Hello from the container"
ccu key ctrl+s --shot
ccu double_click 120 340            # also: right_click, triple_click, middle_click
ccu click 500 300 --mods ctrl       # ctrl+click
ccu drag 100 100 400 300
ccu scroll 640 400 down 5
ccu move 200 30                     # hover
ccu zoom 900 0 1280 200             # enlarged crop for small text (crop coords are NOT screen coords)
ccu windows / ccu active            # window list / focused window title
ccu focus "Mousepad"                # raise a window by title
ccu wait 2
```

Key names use xdotool syntax: `Return`, `Escape`, `Tab`, `BackSpace`, `Delete`, `Up`, `Down`, `Left`, `Right`, `Home`, `End`, `Page_Up`, `Page_Down`, `F1`..`F12`, `super`, plus combos such as `ctrl+shift+t` and `alt+F4`.

## Tips

- Check the screen before every click. Don't click from memory after anything has changed.
- Prefer keyboard shortcuts and typing over hunting for small targets. Click a field before you type into it.
- After a launch or a click that loads something, use `wait_window`, `ccu wait 1`, or `--shot` instead of assuming the action finished.
- If a click misses, take a screenshot, then `zoom` the area and retry. Don't repeat the same coordinates blindly.
- When a key such as Return seems to do nothing, take a screenshot before pressing it again. GTK file dialogs sometimes use the first Return to accept an autocomplete suggestion.
- Read screenshots at most every action or two. Don't screenshot in tight loops; each one costs context.
- For non-GUI work, such as reading files, checking processes or editing config, `ccu exec <cmd>` (or `ccu exec --root ...`) is faster and more reliable than driving a terminal window.
- Move files between the Mac and the container with `ccu cp-in <host> [dest]` and `ccu cp-out <container path> [host dest]`. Container paths are relative to `/home/user`.
- If the desktop looks wedged, check `ccu logs desktop`. `ccu down && ccu up` restarts it and keeps state. `ccu reset` recreates it from the snapshot it came from, and `ccu reset --base` from the clean image. Both discard changes made since, so ask the user first unless they are covered by a snapshot or the user asked for a reset.

## Snapshots and clean resets

A snapshot saves the container's whole filesystem (installed apps, files, settings) as an image, so you can return to that exact state.

```bash
ccu snapshot save golden -m "app installed and configured"   # pauses the desktop for a second
ccu snapshot list                    # * marks the snapshot the container came from
ccu reset --from golden              # new container from the snapshot
ccu reset                            # again from the same snapshot (see `ccu status`)
ccu reset --base                     # back to the clean image
```

- **Before a risky change** (an untrusted installer, a big config change, an upgrade), save a snapshot first so it can be undone with `ccu reset --from NAME`.
- **For repeatable tests**, set the app up once, save a snapshot, and run `ccu reset` before each test run so every run starts from the identical state. Report which snapshot you used.
- Open windows aren't saved, only files. After a reset, launch the app again. If an app might be writing files, close it or save in it before you snapshot.
- Names are letters, digits, `.`, `_` and `-`. `save` refuses to overwrite an existing name unless you pass `--force`.
- **Ask the user before you delete a snapshot** (`ccu snapshot rm`) or overwrite one with `--force`. Snapshots may hold setup work that took them a long time. Saving snapshots, and resetting between test runs the user asked for, are fine without asking.

## Safety

- The container is a sandbox. Root in it is fine; root on the Mac is not. Never run `ccu exec --root` commands that are aimed at host paths.
- Don't type the user's real passwords, tokens or payment details into apps in the container unless the user asks you to in this conversation.
- Get confirmation before purchases, sending messages, or other irreversible actions that a GUI app might take on the user's behalf.

## Audit log and recording

Every action is logged in the container, with source, arguments, result, active window and a screenshot taken just after it. Don't try to avoid or edit the log. Typed text is logged only as its length by default, but screenshots still show text that is visible on screen.

```bash
ccu record start            # when the user asks for a video; ccu record stop prints the .mp4 path
ccu audit export            # copies the log, screenshots and an HTML timeline to the host; prints index.html
ccu audit verify            # hash chain + host mirror check
```

## Restricted network (egress allowlist)

The user can start the desktop with limited network access: `ccu up --egress allowlist.txt` (only the listed domains) or `ccu up --offline` (none). `ccu status` shows which mode is active on its `egress:` line. In these modes:

- Traffic leaves only through an HTTP(S) proxy that is already set in `HTTP_PROXY`/`HTTPS_PROXY` (including under `sudo` and for apt). Hosts that aren't allowlisted fail with `403` / `CONNECT tunnel failed`. Anything that bypasses the proxy fails with `Could not resolve host` or `Network is unreachable`.
- **Blocked requests are by design.** Don't work around them: no `--noproxy`, unsetting proxy variables, raw IPs, other DNS servers, tunnels, mirrors on allowed hosts, or changing network settings as root. The block is enforced outside the container, so those attempts only waste time, and trying them goes against what the user asked for.
- When something fails, run `ccu egress log` to see exactly which hosts were blocked. Then tell the user which domains the task needs and why, and ask them to extend the allowlist. They can run `ccu egress set <file>` without recreating the desktop. Continue once they have done so.
- Apt on the native (arm64) target needs `ports.ubuntu.com`. The x86 target needs `archive.ubuntu.com` and `security.ubuntu.com`.

## MCP alternative

The same controls are also available as MCP tools: `screenshot`, `click`, `type_text`, `key`, `launch`, `install`, and others. Register them with `claude mcp add containercomp -- ccu mcp`. If those tools (`mcp__containercomp__*`) are present, you can use them instead of the CLI. They return screenshots inline, without a file read. The MCP server only connects while the container is running, so if its tools are missing, run `ccu up` and use the CLI.
