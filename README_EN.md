# Huaci Assistant

> Select some text anywhere. A small popup appears. Click "Summarize / Translate / What is this?" and the answer streams in right there.

A tiny Windows desktop utility. It sits in the background — one tray icon, no taskbar clutter.
Works in Word, PDF readers, browsers, WeChat, anywhere: it hooks the system mouse, so it isn't picky about the app.

**Zero third-party dependencies.** Everything is built on the Python standard library plus hand-written `ctypes`. No `pip install` required.

---

## What it does

1. **Drag-select** a piece of text in any application
2. Buttons pop up where you released the mouse: `总结` `翻译` `是什么？`
3. Click one → the popup grows in place and the answer streams out character by character
4. Click outside the popup → it closes

The buttons and the request are separate steps: **no click means no API call**, so nothing is spent idly.

> Note: the default button labels and the whole UI are in Chinese. The buttons are fully editable, so you can rename them to anything.

---

## Requirements

| | |
|---|---|
| OS | Windows 10 / 11 (uses Win32 APIs — **Windows only**) |
| Python | 3.8+ · [download](https://www.python.org/downloads/) |
| Dependencies | none |

When installing Python, tick **Add python.exe to PATH**.

---

## Getting started

```
1. Double-click  启动.bat        (launches silently, no console window)
2. A blue icon appears in the system tray
3. Right-click the tray icon → 设置 (Settings) → enter your API key
4. Select some text somewhere and try it
```

If nothing happens, run **调试启动.bat** instead — it keeps a console open and prints what it's doing at each step.

### First run

Without an API key, the app opens the settings window for you and shows a notification.
Settings live here:

```
%APPDATA%\ai-huaci\config.json
```

**Deliberately outside the project folder** — so if you ever push this to GitHub, your key doesn't come along.

---

## Supported providers

All via the OpenAI-compatible protocol (`/chat/completions`). Six presets built in:

| Provider | Default base URL |
|---|---|
| **DeepSeek** | `api.deepseek.com/v1` |
| **Zhipu GLM** | `open.bigmodel.cn/api/paas/v4` |
| **Qwen (Tongyi)** | `dashscope.aliyuncs.com/compatible-mode/v1` |
| **OpenAI GPT** | `api.openai.com/v1` |
| **Xiaomi MiMo** | `api.xiaomimimo.com/v1` |
| **Custom / relay** | fill in your own |

Pick "Custom" and enter your own base URL, model name, and key — relay services, a local Ollama, vLLM, anything OpenAI-compatible works.

---

## Custom actions

Tray icon → Settings → the **动作** (Actions) tab.

The three defaults (Summarize / Translate / What is this?) can all be renamed, re-prompted, deleted, or added to.
The prompt is simply how you want the AI to handle the selected text.

Some examples:

| Button | Prompt |
|---|---|
| Make it flow | Rewrite this more fluently and formally, keeping the meaning. Output only the result. |
| Find bugs | Below is a piece of code. Point out likely bugs or edge cases, ordered by severity. |
| Explain to a beginner | Turn this into an explanation a high-schooler could follow. No jargon, under 300 words. |

---

## Settings

The **行为** (Behavior) tab:

| Option | Default | Effect |
|---|---|---|
| Minimum characters | 2 | Stops the popup firing on a stray twitch |
| Don't trigger in terminals | on | **Leave this on** — see below |
| Popup delay | 0 ms | Some apps are slow to copy; raise this if needed |

---

## Tray menu

Left- or right-click the tray icon:

- **设置…** — Settings
- **暂停划词 / 恢复划词** — Pause / resume
- **打开配置文件夹** — Open the config folder
- **退出** — Quit

Double-click the tray icon to jump straight to settings.

---

## ⚠️ Things you should know

The way this tool implements "select to act" forces a few trade-offs. Please read these before using it.

### 1. It captures the selection by simulating Ctrl+C

Windows gives ordinary programs no API to read the current selection.
There is exactly one workable approach: **simulate Ctrl+C, let the app copy to the clipboard, read it, then restore your clipboard.**

Consequences:

- **Your clipboard is briefly occupied** (usually tens of milliseconds). The app backs it up and restores it — but if it held an **image or files**, it can't be restored and gets overwritten.
- Selecting text **in a terminal will interrupt the running command**, because Ctrl+C means "interrupt" there.
  So by default the app does nothing in terminal windows (cmd / PowerShell / Windows Terminal / Git Bash).
  **Don't turn that off** unless you know exactly what you're doing.
- Some apps don't accept simulated keystrokes (certain windows running as administrator, some games). No popup there.

### 2. Antivirus software may complain

The app does three things that look suspicious to a scanner: **installs a global mouse hook**, **sends simulated keystrokes**, and **reads/writes the clipboard**.
That renders as "you may be monitored." If it gets blocked, whitelist it.

### 3. It doesn't touch the network unless you click a button

The program sends a request **only after you click an action button**, and only to the API endpoint you configured yourself.
No telemetry, no analytics, no auto-update, nothing sent anywhere else.
Your API key stays in `%APPDATA%\ai-huaci\config.json` on your machine and goes only to the endpoint you entered.

### 4. This is a personal tool

No auto-update, no crash reporting, no compatibility work beyond multi-monitor and high-DPI.
The code is what it is — feel free to change it.

---

## Project layout

```
ai-huaci/
├── main.py          Entry point: threading, event loop, app logic
├── win32.py         Win32 layer (mouse hook / clipboard / SendInput / window effects)
├── popup.py         The popup (actions state + result state)
├── settings.py      Settings window (3 tabs: provider / actions / behavior)
├── tray.py          Tray icon — pure ctypes, no pystray
├── ai.py            Model requests (SSE streaming)
├── config.py        Config read/write + provider presets
├── make_icon.py     Generates icon.ico in pure Python — no Pillow
├── icon.ico         Tray / window icon
├── 启动.bat         Silent launch
└── 调试启动.bat     Launch with console
```

### Threading model

Getting this straight is what makes the whole thing stable:

```
Main thread    tkinter main loop. Every UI operation happens here.
Hook thread    Global mouse hook. The callback drops one message on a queue
               and returns immediately — a blocked callback means a blocked
               mouse for the entire system.
AI thread      One per request. Streamed chunks go on the queue too.
Tray thread    The Shell_NotifyIcon message loop.
```

All cross-thread communication goes through **a single `queue.Queue`**, drained by the main thread on a 30 ms `after()` tick.
This is the only safe arrangement: tkinter is not thread-safe, and touching a widget from another thread will crash it.

---

## Two traps if you modify the code

Written down so you don't have to rediscover them.

### 1. ctypes needs explicit `argtypes` / `restype`

Without them, on 64-bit, ctypes treats pointer arguments as `int` and raises
`int too long to convert`. **A syntax check will never catch this — it only blows up on the line where it runs.**

### 2. The `INPUT` struct must be 40 bytes on x64

The `INPUT` used by `SendInput` has a union inside it, and that union **must contain the largest member**, `MOUSEINPUT` (32 bytes):

```python
class _INPUTunion(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]
```

With only `KEYBDINPUT` (24 bytes), the whole `INPUT` becomes 32 bytes instead of 40.
`SendInput` then returns 0 with error 87 ("invalid parameter") and **explains nothing**.
The symptom is "Ctrl+C does absolutely nothing," and it can cost you hours.

---

## License

MIT — see [LICENSE](LICENSE). Use it freely, no warranty.

---

## 中文

[README.md](README.md)
