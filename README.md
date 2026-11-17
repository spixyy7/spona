<div align="center">

<img src="docs/icon.png" width="96" alt="Spona icon">

# Spona

**Screenshots from one or more Android phones in one click.**

A small, fast desktop app built on `adb`. Plug in a phone over USB, press <kbd>F5</kbd>, and the screenshot is saved and shown right away.

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![Qt](https://img.shields.io/badge/UI-PySide6-41CD52?logo=qt&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?logo=windows&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-3ddc84)

<img src="docs/screenshot.png" width="860" alt="Spona with two phones captured side by side">

</div>

## Features

**Capture**
- Take a screenshot with one click, <kbd>F5</kbd> or <kbd>Space</kbd>; it is saved and shown immediately
- Capture several phones at once. Every connected phone appears as a chip; pick the ones you want and they are captured in parallel and shown side by side
- If a phone screen is off, Spona wakes it up, captures the lock screen, then turns the screen off again
- **Auto-receive:** turn it on, take a screenshot on the phone the normal way, and it shows up on the PC a second later
- **Auto-copy:** every new screenshot, captured or received, lands in the clipboard right away, ready to paste

**Phones**
- Live status for each phone: connected, waiting for approval, offline, locked, screen off
- Unlock over adb: Spona wakes the screen, opens the PIN pad and types your PIN or password. You can let it remember the PIN; it is stored encrypted with Windows DPAPI
- Right-click a phone to capture only that one, unlock it, lock it, or forget its saved PIN

**Gallery**
- Thumbnail strip of everything in the save folder, kept in sync with the disk
- Select several shots (<kbd>Ctrl</kbd>/<kbd>Shift</kbd>-click, <kbd>Ctrl</kbd>+<kbd>A</kbd>) to compare them side by side
- Smooth fullscreen viewer that hides its controls while you look
- Copy any image with <kbd>Ctrl</kbd>+<kbd>C</kbd>, the right-click menu, or by dragging it into another app. It pastes as an image in chat apps and as a file in Explorer
- Delete with a quick prompt: **PC only** or **PC + phone** (for shots that came in with Auto-receive). Tick *Remember my choice* and it stops asking; change it any time in **Settings**
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

Clone or download this repository, then from its folder:

```bash
pip install -r requirements.txt
python -m spona
```

On Windows you can also double-click `Spona.pyw` to start it without a console window.

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
| <kbd>Ctrl</kbd>+<kbd>A</kbd> | Select all screenshots |
| <kbd>Ctrl</kbd>+<kbd>C</kbd> | Copy |
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
| Delete on the phone | `content delete` on MediaStore, then `rm` |

> [!NOTE]
> Only PIN and password locks can be entered over adb. Pattern, fingerprint and face unlock are not supported.

## Privacy

Spona never writes inside its own folder. Everything it stores is local:

| Data | Location |
| --- | --- |
| Settings | `%APPDATA%\Spona\settings.json` |
| Saved PINs (optional, encrypted with Windows DPAPI for your user) | `%APPDATA%\Spona\vault.json` |
| Which received shots came from which phone (for PC + phone delete) | `%APPDATA%\Spona\origins.json` |
| Screenshots | `Pictures\Spona`, or the folder you pick |

Nothing is sent over the network.

## Project layout

```
spona/
├── app.py           entry point and single-instance handling
├── main_window.py   main window and app logic
├── adb.py           adb wrapper: devices, screenshots, screen state, unlock
├── workers.py       background threads: device monitor, capture, unlock, auto-receive
├── devices.py       phone chips in the top bar
├── viewer.py        image view and fullscreen viewer
├── marker.py        marker toolbar
├── annotations.py   strokes, undo/redo and rendering of marks
├── gallery.py       thumbnail strip
├── images.py        image loading, caching and clipboard helpers
├── naming.py        safe, unique file names
├── dialogs.py       PIN, delete and settings dialogs
├── origins.py       remembers where received shots live on the phone
├── vault.py         encrypted PIN storage
├── settings.py      settings file
├── theme.py         colors and styles
├── icons.py         icons and app icon
└── widgets.py       small shared widgets
```

## License

[MIT](LICENSE)
