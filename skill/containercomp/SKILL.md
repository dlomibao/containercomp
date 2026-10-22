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

A `.exe` or `.msi` install opens the installer GUI. Finish it with the screen-control loop below.

Watch for these:
- Ubuntu's `firefox` and `chromium` apt packages are snap stubs, and snaps don't work in containers. Install browsers from vendor repos or tarballs instead.
- Electron and Chromium-based apps usually need `--no-sandbox`.
- To find the binary a package installed, run `ccu exec dpkg -L <pkg> | grep bin/`.

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
- If the desktop looks wedged, check `ccu logs desktop`. `ccu down && ccu up` restarts it and keeps state. `ccu rm && ccu up` resets it to a clean image. Ask the user first, because `rm` discards everything installed.

## Safety

- The container is a sandbox. Root in it is fine; root on the Mac is not. Never run `ccu exec --root` commands that are aimed at host paths.
- Don't type the user's real passwords, tokens or payment details into apps in the container unless the user asks you to in this conversation.
- Get confirmation before purchases, sending messages, or other irreversible actions that a GUI app might take on the user's behalf.

## MCP alternative

The same controls are also available as MCP tools: `screenshot`, `click`, `type_text`, `key`, `launch`, `install`, and others. Register them with `claude mcp add containercomp -- ccu mcp`. If those tools (`mcp__containercomp__*`) are present, you can use them instead of the CLI. They return screenshots inline, without a file read. The MCP server only connects while the container is running, so if its tools are missing, run `ccu up` and use the CLI.
