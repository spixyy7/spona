<div align="center">

<img src="docs/icon.png" width="96" alt="Spona icon">

# Spona

**Screenshots from one or more Android phones in one click.**

A small, fast desktop app built on `adb`. Plug in a phone over USB, press <kbd>F5</kbd>, and the screenshot is saved and shown right away.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Qt](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-3ddc84)

<img src="docs/screenshot.png" width="860" alt="Spona with a phone, a phone lent by another PC and two linked PCs">

</div>

## Features

**Capture**
- Take a screenshot with one click, <kbd>F5</kbd> or <kbd>Space</kbd>; it is saved and shown immediately
- Capture several phones at once. Every connected phone appears as a chip; pick the ones you want and they are captured in parallel and shown side by side
- If a phone screen is off, Spona wakes it up, captures the lock screen, then turns the screen off again
- **Auto-receive:** take a screenshot on the phone the normal way and it shows up on the PC a second later. The arrow next to the button picks what happens on the phone, and Spona remembers it:
  - **Off**: keep it on the phone only
  - **Copy to PC**: keep it on the phone too
  - **Move to PC**: delete it from the phone once it is safely saved on the PC
- **Auto-copy:** every new screenshot, captured or received, lands in the clipboard right away, ready to paste

**Phones**
- Live status for each phone: connected, waiting for approval, offline, locked, screen off
- Unlock over adb: Spona wakes the screen, opens the PIN pad and types your PIN or password. You can let it remember the PIN; it is stored encrypted with Windows DPAPI
- Right-click a phone to capture only that one, unlock it, lock it, or forget its saved PIN
- **Tailscale:** use your phones without a cable, from anywhere on your tailnet. Everything above works the same way over the network
- **Clipboard sync:** copy text on the PC and paste it on the phone, or the other way round. Pick **Both ways**, **PC → phone only** or **Phone → PC only** in **Settings → Clipboard**. It works with the selected phones, over USB or Tailscale. Text only

**Tailscale setup** (needs [Tailscale](https://tailscale.com) on the PC and on the phone)
1. Plug the phone in with USB once
2. Click **+** next to the phones, or open **Settings → Tailscale → Connect a phone…**
3. Click **Go wireless**. Spona switches adb to network mode, finds the phone on your tailnet and checks it is the same phone
4. Unplug the cable. The phone shows up as *Phone · Tailscale* and Spona reconnects it by itself whenever it is reachable

You can also connect a phone that is already listening by typing its Tailscale address. Network adb mode is reset when the phone restarts, so plug it in and press **Go wireless** again after a reboot. Turn it off or forget phones any time in **Settings**.

**PCs** (Spona on each PC, all signed in to the same Tailscale account)
- Your other PCs that run Spona show up next to the phones by themselves. There is nothing to type: Spona asks Tailscale on this PC which PCs are yours
- Click a PC to pick where things go, the same way you pick phones. What you copy (text, an image, or several separate images) and your new screenshots are sent to the selected PCs, ready to paste
- **Host:** click **This PC** and turn on *Host* to lend this PC's phones to your other PCs. There they show up as *Phone · via PC* and work as if they were plugged in: Capture, Unlock, Lock, Auto-receive and Clipboard sync, each with that PC's own settings. A PC without adb can use them too
- The host is used first: if a phone can be reached both through a host and directly, it goes through the host, so nothing runs twice. Turn Host off and the other PCs go back to their own connection
- Right-click a screenshot and pick **Send to PC**, or right-click a PC to send the selected screenshots there
- Choose what is sent, or turn it all off, in **This PC** or **Settings → Other PCs**
- Only PCs on your own Tailscale account can connect, and only over your tailnet. The first time, Windows may ask to let Spona through the firewall: allow it on private networks

**Gallery**
- Thumbnail strip of everything in the save folder, kept in sync with the disk
- **Select** several shots the easy way: press **Select** (or click the round check on any thumbnail), then click or swipe over thumbnails. **Select all** (or <kbd>Ctrl</kbd>+<kbd>A</kbd>) picks everything and turns into **Clear**. Delete removes them all at once with the same PC only / PC + phone prompt. <kbd>Esc</kbd> stops
- Selected shots are shown side by side to compare them
- Smooth fullscreen viewer that hides its controls while you look
- Copy any image with <kbd>Ctrl</kbd>+<kbd>C</kbd>, the right-click menu, or by dragging it into another app. It pastes as an image in chat apps and as a file in Explorer
- Copy as many shots as you like at once. Pick how with the arrow next to Copy, the right-click menu or **Settings → Copying several images**:
  - **Grid**: one image. Every shot stays whole, and tall and wide shots are fitted into even rows of the same height, so any mix of ratios looks tidy
  - **Side by side**: one image, all shots in one row
  - **Separate images**: each shot on its own, so a chat gets them as separate photos and Explorer as separate files
  - Shots go in the order they were taken, and marks you drew are included
- Delete with a quick prompt: **PC only** or **PC + phone** (for shots that came in with Auto-receive). Tick *Remember my choice* and it stops asking; change it any time in **Settings**. With PC + phone, each shot is deleted wherever it is, and Spona tells you where
- Delete always works: if another app (OneDrive, an image viewer) keeps the file open, Spona frees it and deletes it anyway. If that is not possible, the shot is hidden and deleted as soon as the file is free, even after a restart
- Show the file in Explorer, change the save folder (your choice is remembered)

**Marker**
- Draw on any screenshot in fullscreen: six colors, adjustable size and opacity, eraser, undo and redo
- Strokes are smoothed and stored at full image resolution, so they stay sharp at any window size
- Copy the marked image straight to the clipboard, or save it as a new `_marked.png` next to the original

<div align="center">
<img src="docs/marker.png" width="760" alt="Marker toolbar in fullscreen">
</div>

## Getting started

You need:

1. **Python 3.10+**
2. **[Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)**. Spona finds `adb` on `PATH`, in `ANDROID_HOME` / `ANDROID_SDK_ROOT`, or in the usual SDK folders. If it is somewhere else, click **Find adb.exe** in the app.
3. **USB debugging** turned on in the phone's Developer options

Clone or download this repository, then install it from its folder:

```bash
pip install .
spona
```

`pip install .` adds a `spona` command that opens the app without a console window. Use `pip install -e .` instead if you want your own changes to apply right away.

To run it without installing:

```bash
pip install -r requirements.txt
python -m spona
```

On Windows you can also double-click `Spona.pyw`.

The first time you capture, Spona asks where to save screenshots and remembers your choice. You can change it later with **Change** in the top bar.

## Keyboard shortcuts

| Keys | Action |
| --- | --- |
| <kbd>F5</kbd> or <kbd>Space</kbd> | Capture the selected phones |
| <kbd>Ctrl</kbd>+<kbd>U</kbd> | Unlock the selected phones |
| <kbd>M</kbd> | Marker (opens fullscreen in drawing mode) |
| <kbd>F11</kbd>, <kbd>F</kbd>, <kbd>Enter</kbd> or double-click | Toggle fullscreen |
| <kbd>Esc</kbd> | Leave fullscreen |
| <kbd>←</kbd> <kbd>→</kbd> <kbd>Home</kbd> <kbd>End</kbd>, mouse wheel | Browse screenshots |
| <kbd>Ctrl</kbd>+<kbd>A</kbd> | Select all screenshots (again to clear) |
| <kbd>Esc</kbd> | Stop selecting |
| <kbd>Ctrl</kbd>+<kbd>C</kbd> | Copy (several shots: grid, side by side or separate, as chosen) |
| <kbd>Delete</kbd> | Delete (asks PC only or PC + phone) |
| <kbd>Ctrl</kbd>+<kbd>O</kbd> | Open the screenshots folder |
| <kbd>Ctrl</kbd>+<kbd>L</kbd> | Change the save folder |

While drawing:

| Keys | Action |
| --- | --- |
| <kbd>P</kbd> / <kbd>E</kbd> | Marker / eraser |
| <kbd>1</kbd> … <kbd>6</kbd> | Pick a color |
| <kbd>[</kbd> <kbd>]</kbd> or <kbd>Ctrl</kbd>+wheel | Smaller / bigger brush |
| <kbd>Ctrl</kbd>+<kbd>Z</kbd>, <kbd>Ctrl</kbd>+<kbd>Y</kbd> | Undo, redo |
| <kbd>Delete</kbd> | Clear all marks (undoable) |
| <kbd>Ctrl</kbd>+<kbd>C</kbd> | Copy the marked image |
| <kbd>Ctrl</kbd>+<kbd>S</kbd> | Save as a new image |
| <kbd>M</kbd> or <kbd>Esc</kbd> | Stop drawing |

## How it works

Spona is a thin layer over standard `adb` commands:

| What | Command |
| --- | --- |
| Find phones | `adb devices -l` |
| Phone name and Android version | `adb shell getprop` |
| Screen and lock state | `dumpsys power`, `dumpsys window`, `dumpsys trust` |
| Screenshot | `adb exec-out screencap -p` |
| Wake, lock | `input keyevent KEYCODE_WAKEUP` / `KEYCODE_SLEEP` |
| Unlock | `wm dismiss-keyguard`, then `input text` with the PIN |
| Auto-receive | watches the phone's `Pictures/Screenshots` and `DCIM/Screenshots` folders and pulls new files with `exec-out cat` |
| Delete on the phone (Move to PC, PC + phone delete) | `content delete` on MediaStore, then `rm` |
| Delete a file another app keeps open | Windows Restart Manager names the app, then Spona closes only that app's handles to the file and moves it to the Recycle Bin |
| Tailscale | `adb tcpip 5555` once over USB, then `adb connect <tailscale-ip>:5555`; peers come from `tailscale status --json` |
| PCs | each Spona listens on its Tailscale address (TCP port 47615) and connects to the other PCs of the same account listed by `tailscale status --json`. Messages are small JSON headers, images go as files checked with SHA-1, and each one is sent to a PC once |
| Clipboard sync | pushes a small helper (`helper/ClipHelper.java`, built to `spona/clip_helper.dex`) to `/data/local/tmp` and runs it with `app_process` as the shell user. It listens for clipboard changes on the phone and sets the text Spona sends, all over one `adb shell` pipe |

> [!NOTE]
> Only PIN and password locks can be entered over adb. Pattern, fingerprint and face unlock are not supported.

## Privacy

Spona never writes inside its own folder. Everything it stores is local:

| Data | Location |
| --- | --- |
| Settings | `%APPDATA%\Spona\settings.json` |
| Saved PINs (optional, encrypted with Windows DPAPI for your user) | `%APPDATA%\Spona\vault.json` |
| Which received shots came from which phone (for PC + phone delete) | `%APPDATA%\Spona\origins.json` |
| Saved Tailscale phones, PC sharing choices | `%APPDATA%\Spona\settings.json` |
| Screenshots | `Pictures\Spona`, or the folder you pick |

Spona itself sends nothing anywhere outside your own devices. With Tailscale, adb talks to your phone over your own tailnet. Clipboard sync is off until you turn it on, and copied text only travels between the PC and your own phones through adb. Sharing with PCs only reaches PCs on your own Tailscale account; turn it off in **Settings → Other PCs**. Files that arrive from another PC for the clipboard are kept in `%TEMP%\Spona\received` and cleaned up after a few hours.

## Project layout

```
spona/
├── app.py           entry point and single-instance handling
├── main_window.py   main window and app logic
├── adb.py           adb wrapper: devices, screenshots, screen state, unlock, network mode
├── tailscale.py     lists phones and PCs on your tailnet
├── link.py          finds your other PCs with Spona and keeps a connection to each
├── sharing.py       prepares and stores the images and text sent between PCs
├── workers.py       background threads: device monitor, capture, unlock, auto-receive, reconnect, clipboard sync
├── devices.py       phone and PC chips in the top bar
├── viewer.py        image view and fullscreen viewer
├── marker.py        marker toolbar
├── annotations.py   strokes, undo/redo and rendering of marks
├── gallery.py       thumbnail strip
├── images.py        image loading, caching and clipboard helpers
├── collage.py       grid and side-by-side layout for copying several shots
├── naming.py        safe, unique file names
├── dialogs.py       PIN, delete and settings dialogs
├── origins.py       remembers where received shots live on the phone
├── filelocks.py     finds and frees files other apps keep open
├── vault.py         encrypted PIN storage
├── settings.py      settings file
├── theme.py         colors and styles
├── icons.py         icons and app icon
├── widgets.py       small shared widgets
└── clip_helper.dex  phone-side clipboard helper (built from helper/)
helper/
├── ClipHelper.java  clipboard helper that runs on the phone
└── build.py         rebuilds clip_helper.dex (needs a JDK and the Android SDK)
```

To rebuild the clipboard helper after changing it, set `ANDROID_HOME` (and `JAVA_HOME` if `javac` is not on `PATH`) and run `python helper/build.py`.

## License

[MIT](LICENSE)
