---
name: containercomp
description: Drive a Linux desktop running in a Podman container (Ubuntu 24.04 + XFCE) with mouse, keyboard and screenshots through the `ccu` CLI. Use when the user wants to install, run, test or operate a GUI app in a sandbox/container instead of on their Mac, when an app needs root/admin to install, for Linux or x86 apps, for Windows .exe/.msi apps through Wine, or when they say "containercomp", "ccu", or "computer use in the container".
---

# containercomp: computer use inside a container

`ccu` controls a disposable Ubuntu desktop in Podman. Inside the container you have passwordless sudo, so installers that need admin rights run there without touching the Mac. The user can watch, or take over, at the noVNC URL that `ccu up` prints.

Two targets:
- `native` (default): an arm64 desktop. It's fast. Use it for anything that has an arm64 Linux build.
- `x86` (`ccu -t x86 ...` or `CCU_TARGET=x86`): an amd64 desktop under emulation, with Wine. Use it for x86-only Linux binaries and Windows `.exe`/`.msi` files. It's noticeably slower, so allow longer waits.

Every command below takes `-t x86` right after `ccu` to address the x86 desktop, or `-n NAME` to address a named desktop (see "Several desktops" below).

## Start up

```bash
ccu status                 # is it built/running?
ccu up                     # builds the image on first run (several minutes), then starts it
```

Tell the user the viewer URL that `ccu up` prints (`http://localhost:6080/` for native and `:6081` for x86 by default; a named desktop gets the next free port).

## Several desktops (parallel work)

Several desktops can run at once, each with its own name, screen, viewer port, audit log and network mode. Use your own desktop when:
- the user asks you to work in parallel, or you are one of several agents or subagents using `ccu`;
- the user asks for a separate or throwaway desktop, or for tests that shouldn't disturb the one they're watching.

Then:
1. Run `ccu ls` to see which desktops exist. Don't use or change one you didn't create unless the user pointed you at it.
2. Pick a short, unused name for your task, such as `ccu-review-pr12` or `tests-2` (letters, digits, `.`, `_`, `-`), and create it: `ccu -n NAME up` (add `--from SNAP` to start from a snapshot, `-t x86` for x86, `--offline`/`--egress FILE` for restricted network). It prints the desktop's own viewer URL.
3. **Pass `-n NAME` on every command**, right after `ccu`: `ccu -n NAME screenshot`, `ccu -n NAME click 300 200 --shot`. A command without `-n` goes to the default `ccu` desktop, which may be someone else's. The desktop remembers whether it is x86, so `-t` is only needed with `up`.
4. When done, stop it with `ccu -n NAME down`, or remove it with `ccu -n NAME rm` if it was a throwaway desktop for this task and the user doesn't need its state. Never remove or reset desktops you didn't create without asking.

Snapshots are shared by every desktop of the same image, so `ccu -n NAME up --from golden` gives each parallel run an identical starting point. With MCP, each desktop needs its own server: `claude mcp add ccu-NAME -- ccu -n NAME mcp`.

## Install apps

```bash
ccu install gimp inkscape                  # apt packages
ccu install ~/Downloads/app_arm64.deb      # host file: copied in, then installed
ccu install ~/Downloads/Tool-1.2-arm64.AppImage   # unpacked; becomes the command `tool`
ccu install https://example.com/app.tar.gz # downloaded, extracted to ~/apps/app/; prints the executables it found
ccu install chromium                       # browsers: chromium, firefox, google-chrome (x86 only)
ccu -t x86 install ~/Downloads/setup.exe   # starts the Windows installer on the desktop
```

A `.exe` or `.msi` install opens the installer GUI. Finish it with the screen-control loop below. Windows programs are installed under `~/.wine/drive_c/Program Files/`. Launch one like this: `ccu -t x86 launch 'wine "C:\Program Files\7-Zip\7zFM.exe"'`. Pass the whole command as one single-quoted string.

On Apple Silicon only **64-bit** Windows programs work. 32-bit ones (`file` reports `PE32 executable`) fail with errors such as `status c0000018` or a qemu crash. If `ccu install` warns that a file is 32-bit, look for the vendor's 64-bit installer or `.msi` instead of retrying. If `ccu -t x86 up` warns that Rosetta is off, tell the user. The fix needs the user's approval because it restarts the Podman VM.

Watch for these:
- Browsers: use `ccu install chromium` or `ccu install firefox`, not apt names like `chromium-browser`. Ubuntu's own packages are snap stubs, and snaps don't work in containers; `ccu install` sets up a deb source instead. Google Chrome has no Linux arm64 build, so on the native target use `chromium` (built by the xtradeb PPA); `google-chrome` works on the x86 target. Launch with `ccu launch chromium URL` or `ccu launch firefox URL`.
- Chromium and Chrome run with their sandbox in the container. Add `--no-sandbox` only if an app exits with a sandbox error (some Electron apps add it themselves).
- To find the binary a package installed, run `ccu exec dpkg -L <pkg> | grep bin/`.

## Certificate errors and proxies (corporate networks)

If downloads in the container fail with `certificate verify failed`, `SSL certificate problem` or `unable to get local issuer certificate` while the same URL works on the Mac, the user is probably behind a TLS-inspecting proxy. Don't turn verification off (`-k`, `--trusted-host`, `verify=False`). Instead:

```bash
ccu certs sync             # trust the extra roots the Mac trusts (applies to the running container)
ccu certs add ~/corp.pem   # or a CA file the user names
```

Chrome, Chromium and Firefox pick these up too, including browsers installed later with `ccu install`. On an image built before certutil was added, Chromium reports `NET::ERR_CERT_AUTHORITY_INVALID`; run `ccu install libnss3-tools && ccu certs apply`. Proxy variables are only picked up when the container is created, so if `HTTPS_PROXY` changed, a `ccu rm && ccu up` is needed (ask first). `ccu certs sync --machine` changes the shared Podman VM; run it only when the user asks, for `podman pull` failures.

## The control loop

1. **Look.** Run `ccu screenshot`. It prints a PNG path. Read that file with the Read tool to see the screen.
2. **Act.** Run a single action using pixel coordinates taken from the latest screenshot. The screenshot is at native resolution (1280x800 by default), so image pixels are screen pixels. If the app has an accessibility tree, act by element name instead (`ccu click_element NAME`, see the next section).
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

## Clipboard

```bash
ccu clip paste - < notes.txt        # set the clipboard and press ctrl+v in the focused field
ccu clip paste --key ctrl+shift+v "make test"   # terminals paste with ctrl+shift+v
ccu clip set "text"                 # set only (also: cmd | ccu clip set -)
ccu clip get                        # read what an app copied (after ctrl+c / a "Copy" button)
ccu clip push / ccu clip pull       # Mac clipboard -> desktop / desktop -> Mac (text or PNG)
```

- For anything longer than a line or two, or with newlines, `clip paste` is faster and more reliable than `type`: `type` presses Return for every newline (which can submit a form) and sends one key per character. Use `type` for short input and for fields that block pasting.
- To read text you can't select easily from a screenshot (a generated token, an error message, a URL), copy it in the app and run `ccu clip get` instead of zooming and transcribing.
- `ccu clip pull` overwrites the user's Mac clipboard. Do it only when the user asks for something on their clipboard.
- Clipboard contents are logged like typed text (length only by default).

Key names use xdotool syntax: `Return`, `Escape`, `Tab`, `BackSpace`, `Delete`, `Up`, `Down`, `Left`, `Right`, `Home`, `End`, `Page_Up`, `Page_Down`, `F1`..`F12`, `super`, plus combos such as `ctrl+shift+t` and `alt+F4`.

## Act on UI elements by name (preferred when the app has a tree)

GTK and Qt apps (most of the Linux desktop, including the XFCE panel and terminal) publish their widgets through the accessibility tree. When an app does, read the tree instead of guessing pixels: it's exact, and a scoped tree costs far fewer tokens than a screenshot.

```bash
ccu tree --app mousepad                  # one line per element, grouped by app
ccu tree --app mousepad --role button    # "button" matches push_button, toggle_button, ...
ccu tree --name save                     # case-insensitive substring of the name
ccu click_element "Save As" --shot       # clicks the element's centre with the real mouse
ccu click_element "Match case" --role check_box --app mousepad
ccu click_element OK --timeout 10        # wait up to 10 s for it to appear
ccu click_element notes.txt --role table_cell --count 2   # double-click a file in a file dialog
ccu find "search for" --role text        # check an element exists / read its text and states
ccu tree --apps                          # which apps have a tree
```

Output lines look like `check_box "Match case" @329,318 433x23 [checked]`: role, name, **centre** `@X,Y` in screenshot pixels, size, notable states (`focused`, `checked`, `selected`, `expanded`, `disabled`, ...), and `text="..."` for fields and terminals.

- **Use the tree first** for GTK and Qt apps (most Linux apps, including the XFCE panel and terminal). Scope it with `--app` (and `--role`/`--name`); an unscoped tree of a busy desktop can cost as much as a screenshot.
- **Fall back to pixels** when `ccu tree --app X` shows nothing or only a `frame`: plain X11 apps, Wine apps, games, and Chromium/Electron apps started without `--force-renderer-accessibility`. If you launch a Chromium-based or Electron app yourself, add that flag: `ccu launch 'app --no-sandbox --force-renderer-accessibility'`.
- **Matching:** case-insensitive; an exact name wins over substrings. If several elements match, the command fails and lists them. Then add `--role`/`--app`, or `--index N` (0-based) to pick one. Unnamed fields go by their label, e.g. `text "Search for:"`.
- `click_element` raises the element's window first if another app's window covers it. If the element is showing but off the screen (a dialog taller than the screen), the error says so; then `--action` invokes the element's own action without the mouse.
- Still take a screenshot (`--shot`) after actions that change the screen a lot, and to check things the tree doesn't show (images, colours, layout). Use `ccu find` or a narrow `ccu tree` to check small changes such as a checkbox state or a field's text.
- GTK 4 apps (GNOME apps) report positions relative to their window; `ccu` converts them, but positions inside GTK 4 popovers can be off. If a click lands wrong there, use pixels.

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
- **For repeatable tests**, set the app up once, save a snapshot, and run `ccu reset` before each test run so every run starts from the identical state, or let `ccu test run` do the resets (see "Repeatable tests"). Report which snapshot you used.
- Open windows aren't saved, only files. After a reset, launch the app again. If an app might be writing files, close it or save in it before you snapshot.
- Names are letters, digits, `.`, `_` and `-`. `save` refuses to overwrite an existing name unless you pass `--force`.
- **Ask the user before you delete a snapshot** (`ccu snapshot rm`) or overwrite one with `--force`. Snapshots may hold setup work that took them a long time. Saving snapshots, and resetting between test runs the user asked for, are fine without asking.

## Repeatable tests (`ccu test`)

When the user wants an app behavior tested repeatedly, or wants a result they can rerun or put in CI, write a test spec instead of only driving the app by hand. `ccu test run` resets the desktop before every run, so each run starts from the same state.

```bash
ccu test run spec.yaml                       # one run; exit code 0 = passed
ccu test run spec.yaml --repeat 3            # flakiness check: three runs, each from a fresh reset
ccu test run spec.yaml --no-reset            # quick iteration on the current desktop while writing it
ccu test validate spec.yaml                  # lists every problem in the spec, without running it
ccu test draft > spec.yaml                   # spec draft from what you just did by hand
```

A spec is YAML. Each item is a one-key mapping `- action: arguments`. A list fills the arguments in order, and a single value is the first argument:

```yaml
name: Mousepad saves a note
from: base                 # snapshot to reset to ("base" = clean image); omit = the snapshot the container came from
setup:                     # bring the app to the starting point; failures here mark the run as an error
  - check: {file: {path: ~/note.txt, exists: false}}
  - launch: mousepad
  - wait_window: Mousepad
steps:
  - click: [300, 200]
  - type: "Hello\n"        # \n presses Return
  - key: ctrl+s            # or a list: [ctrl+a, Delete]
  - wait_window: Save As
  - type: /home/user/note.txt
    name: file name        # optional label for the report
  - key: Return
checks:                    # each check is retried for up to 5 s (check_timeout), and all are evaluated
  - window: {title: note.txt - Mousepad, active: true}
  - file: {path: ~/note.txt, contains: Hello}
  - shell: {run: "grep -c Hello ~/note.txt", matches: "^1$"}
  - text: {text: Hello, region: [0, 0, 1280, 400]}     # OCR
```

- Steps: `launch`, `wait_window`, `focus`, `click` (also `double_click`, `right_click`, ...) `[x, y]` or `{x, y, button, count, mods}`, `move`, `drag`, `scroll`, `type`, `key`, `wait`, `shell` (fails on a non-zero exit), `screenshot`, `click_element`/`wait_element` `{name, role, app, index}`, and `check: {...}` for an assertion between steps.
- Checks: `window` (`title` substring or `matches` regex; `active`, `exists: false`), `file` (`exists`, `contains`, `matches`, `equals`), `shell` (`exit`, `contains`, `matches` on stdout), `text` (OCR; `region`, `matches`, `absent: true`), `element` (accessibility tree; `role`, `states`, `exists: false`). Every item also takes `optional: true`; waits and checks take `timeout:` (seconds).
- Make specs robust: `wait_window` after every launch and before typing into a dialog; prefer keys and typed paths over clicks; prefer `file`/`shell`/`window` checks over `text` (OCR can misread, so keep `text` checks to short, distinct phrases and give a `region`); put state you need in a snapshot (`from:`), not in long setup. Add a setup check that fails if the previous run's output is still there, so a missed reset shows up.
- Accessibility steps (`click_element`, `element`) fail with "accessibility support not available in this image" on images without it. Use coordinates and OCR there.

**Reading results.** `ccu test run` prints a line per item and, at the end, the path of `index.html` in the results directory (`summary.json` and `junit.xml` sit next to it). For a failure, read `summary.json`: each run lists its `failures` (which item, and why: the OCR text seen, the open windows, the file contents) and a `screenshot` path of the first failing item. Read that PNG to see the screen at that point. Each run directory also has `result.json` with every item, `shots/` (one screenshot per item), `audit/` (that run's audit log entries) and `reset.log`. Report to the user which checks failed and why, with the results path. Don't loosen a check just to get a pass unless the user agrees that the expectation was wrong.

**Turning a manual session into a spec.** After doing a task by hand with `ccu` commands: run `ccu test draft > spec.yaml`. It replays your launches, clicks, keys and `ccu exec` commands, and adds a `wait_window` wherever the active window changed. Then edit it: typed text shows up as `"TODO"` (the audit log only keeps its length), so fill it in. Replace coordinates that depend on window layout with keys or `click_element` where possible, drop exploratory steps, and add `checks` for the outcome the user cares about. Then `ccu test validate`, and `ccu test run` it until it passes twice in a row (`--repeat 2`). Examples that pass: `examples/mousepad.yaml` (keys and OCR) and `examples/mousepad-elements.yaml` (element names) in the containercomp repo. Get element names from `ccu tree` rather than guessing from the screenshot: menu labels often differ from what the tree exposes (Mousepad's "Find" item, for example).

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

The same controls are also available as MCP tools: `screenshot`, `click`, `type_text`, `key`, `clipboard_get`, `clipboard_set` (with `paste: true` to paste), `launch`, `install`, `ui_tree`, `find_element`, `click_element`, and others. Register them with `claude mcp add containercomp -- ccu mcp`. If those tools (`mcp__containercomp__*`) are present, you can use them instead of the CLI. A server drives one desktop (the default one here); for a named desktop, use the server registered for it (`mcp__ccu-NAME__*`) or the CLI with `-n NAME`. They return screenshots inline, without a file read. The MCP server only connects while the container is running, so if its tools are missing, run `ccu up` and use the CLI.
