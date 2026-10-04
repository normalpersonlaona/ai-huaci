"""
Win32 封装 —— 全部用 ctypes，不依赖任何第三方库。

⚠️ 写这个文件的头号注意事项：**每个 API 都要显式声明 argtypes / restype**。
   64 位下如果不声明，ctypes 会把指针参数当 int 处理，直接抛
   "int too long to convert"。这个错误只有运行时才炸，语法检查看不出来。

包含四块：
  1. DPI 感知      —— 让 Win32 坐标和 tkinter 坐标对上
  2. 全局鼠标钩子  —— 检测"划选"这个动作
  3. 剪贴板        —— 抓选区要靠它
  4. 模拟按键      —— Ctrl+C；以及判断当前是什么窗口
"""

import ctypes
import ctypes.wintypes as w
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
try:
    shcore = ctypes.windll.shcore
except OSError:
    shcore = None

# ─────────────────────────────────────────────────────────
#  1. DPI 感知
# ─────────────────────────────────────────────────────────

def enable_dpi_awareness():
    """
    必须在**创建任何窗口之前**调用。

    不调用的话，在有缩放的屏幕上：
      - GetCursorPos 给你的是"逻辑坐标"
      - tkinter 摆窗口用的也是逻辑坐标，但渲染时又会被系统再缩放一次
    结果是浮窗飘到离鼠标很远的地方。
    """
    try:
        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        shcore.SetProcessDpiAwareness(2)
        return 'per-monitor'
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return 'system'
    except Exception:
        return 'none'


# ─────────────────────────────────────────────────────────
#  2. 全局鼠标钩子
# ─────────────────────────────────────────────────────────

WH_MOUSE_LL = 14
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_MOUSEWHEEL = 0x020A
PM_REMOVE = 0x0001
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [('x', w.LONG), ('y', w.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('pt', POINT),
        ('mouseData', w.DWORD),
        ('flags', w.DWORD),
        ('time', w.DWORD),
        ('dwExtraInfo', ctypes.POINTER(w.ULONG)),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, w.WPARAM, w.LPARAM)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, w.HINSTANCE, w.DWORD]
user32.SetWindowsHookExW.restype = w.HHOOK
user32.CallNextHookEx.argtypes = [w.HHOOK, ctypes.c_int, w.WPARAM, w.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [w.HHOOK]
user32.UnhookWindowsHookEx.restype = w.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
user32.PeekMessageW.restype = w.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
user32.GetMessageW.restype = ctypes.c_int


class MouseHook:
    """
    全局低级鼠标钩子。

    ⚠️ 回调里**绝对不能干慢活**。这个回调在系统输入链路上，
       你卡 100ms，全系统的鼠标就卡 100ms。
       所以：回调只负责判断 + 往队列里丢一条消息，别的什么都不干。

    用法：
        hook = MouseHook(on_event)     # on_event(kind, x, y)
        hook.start()
        ...
        hook.stop()
    """

    def __init__(self, on_event):
        self._on_event = on_event
        self._thread = None
        self._hook = None
        self._running = False
        self._ready = threading.Event()
        self._proc = None          # 必须留着引用，被 GC 掉会直接崩

    # ── 公开接口 ──

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name='MouseHook', daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)

    def stop(self):
        self._running = False
        if self._hook:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None

    @property
    def alive(self):
        return bool(self._hook)

    # ── 内部 ──

    def _run(self):
        emit = self._on_event

        # 拖拽状态机
        state = {
            'down': False, 'x0': 0, 'y0': 0, 'moved': 0, 't0': 0.0,
        }
        moved = 0

        def cb(nCode, wParam, lParam):
            nonlocal moved
            try:
                if nCode == 0:
                    info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                    x, y = info.pt.x, info.pt.y

                    if wParam == WM_LBUTTONDOWN:
                        state['down'] = True
                        state['x0'], state['y0'] = x, y
                        state['t0'] = time.time()
                        moved = 0

                    elif wParam == WM_MOUSEMOVE and state['down']:
                        dx = abs(x - state['x0'])
                        dy = abs(y - state['y0'])
                        if dx > moved:
                            moved = dx
                        if dy > moved:
                            moved = dy

                    elif wParam == WM_LBUTTONUP:
                        if state['down']:
                            state['down'] = False
                            dt = time.time() - state['t0']
                            # 判定"这是一次划选"：
                            #   拖了 8 像素以上（防手抖）、
                            #   用时在 0.05~3 秒之间（防慢拖和误触）
                            if moved >= 8 and 0.05 <= dt <= 3.0:
                                emit({'kind': 'select', 'x': x, 'y': y,
                                      'x0': state['x0'], 'y0': state['y0']})
                            else:
                                emit({'kind': 'click', 'x': x, 'y': y,
                                      'x0': state['x0'], 'y0': state['y0']})
                        else:
                            emit({'kind': 'click', 'x': x, 'y': y})

                    elif wParam == WM_RBUTTONDOWN:
                        emit({'kind': 'right', 'x': x, 'y': y})

            except Exception:
                pass   # 回调里绝不能让异常冒出去

            return user32.CallNextHookEx(None, nCode, wParam, lParam)

        self._proc = HOOKPROC(cb)
        self._hook = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
        self._ready.set()

        if not self._hook:
            return

        msg = w.MSG()
        # GetMessage 会阻塞，但 stop() 里 UnhookWindowsHookEx 之后拿不到消息，
        # 这里用 PeekMessage + 短睡眠，方便优雅退出
        while self._running:
            got = False
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
                got = True
            if not got:
                time.sleep(0.004)


# ─────────────────────────────────────────────────────────
#  3. 剪贴板
# ─────────────────────────────────────────────────────────

CF_UNICODETEXT = 13
CF_TEXT = 1
CF_BITMAP = 2
CF_HDROP = 15
GMEM_MOVEABLE = 0x0002

user32.OpenClipboard.argtypes = [w.HWND]
user32.OpenClipboard.restype = w.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = w.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = w.BOOL
user32.IsClipboardFormatAvailable.argtypes = [w.UINT]
user32.IsClipboardFormatAvailable.restype = w.BOOL
user32.GetClipboardData.argtypes = [w.UINT]
user32.GetClipboardData.restype = w.HANDLE
user32.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
user32.SetClipboardData.restype = w.HANDLE
user32.EnumClipboardFormats.argtypes = [w.UINT]
user32.EnumClipboardFormats.restype = w.UINT

kernel32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = w.HANDLE
kernel32.GlobalLock.argtypes = [w.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [w.HANDLE]
kernel32.GlobalUnlock.restype = w.BOOL
kernel32.GlobalFree.argtypes = [w.HANDLE]
kernel32.GlobalFree.restype = w.HANDLE


def clipboard_formats():
    """列出剪贴板里现有的格式编号。打不开就返回 None。"""
    if not user32.OpenClipboard(None):
        return None
    try:
        out = []
        f = 0
        while True:
            f = user32.EnumClipboardFormats(f)
            if f == 0:
                break
            out.append(f)
        return out
    finally:
        user32.CloseClipboard()


def clipboard_get_text():
    """读剪贴板文本，没有就返回 None。"""
    if not user32.OpenClipboard(None):
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()


def clipboard_set_text(text):
    """写文本进剪贴板。成功返回 True。"""
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        buf = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return False
        ptr = kernel32.GlobalLock(h)
        if not ptr:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(ptr, buf, size)
        kernel32.GlobalUnlock(h)
        # 这一步成功之后，内存所有权归系统了，不能再手动 free
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


# ─────────────────────────────────────────────────────────
#  4. 模拟按键 / 窗口判断
# ─────────────────────────────────────────────────────────

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
VK_CONTROL = 0x11
VK_C = 0x43

user32.SendInput.argtypes = [w.UINT, ctypes.c_void_p, ctypes.c_int]
user32.SendInput.restype = w.UINT
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = w.BOOL
user32.GetForegroundWindow.restype = w.HWND
user32.GetClassNameW.argtypes = [w.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.GetWindowThreadProcessId.restype = w.DWORD


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk', w.WORD), ('wScan', w.WORD), ('dwFlags', w.DWORD),
        ('time', w.DWORD), ('dwExtraInfo', ctypes.POINTER(w.ULONG)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', w.LONG), ('dy', w.LONG),
        ('mouseData', w.DWORD), ('dwFlags', w.DWORD), ('time', w.DWORD),
        ('dwExtraInfo', ctypes.POINTER(w.ULONG)),
    ]


class _INPUTunion(ctypes.Union):
    # ⚠️ 这个 union 里**必须**放着最大的那个成员（MOUSEINPUT，32 字节）。
    #    只写 KEYBDINPUT（24 字节）的话，整个 INPUT 会变成 32 字节而不是 40，
    #    SendInput 会直接返回 0 并报「参数无效」(87)，而且不给任何解释。
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT)]


class KINPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [('type', w.DWORD), ('u', _INPUTunion)]


def _send_key(vk, up=False):
    inp = KINPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, None)
    return user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(KINPUT))


def send_ctrl_c():
    """
    模拟按一下 Ctrl+C。返回 (成功次数, 最后一次的错误码)。

    失败最常见的原因是**前台窗口属于管理员权限的进程** ——
    Windows 的 UIPI 机制不允许普通进程往高权限窗口发输入，
    SendInput 会直接返回 0，而且不给任何提示。

    ⚠️ 这个动作会在**当前焦点窗口**里生效。焦点在终端里的话就是 SIGINT，
       会打断正在跑的命令 —— 所以调用前务必先过 is_terminal()。
    """
    results = [
        _send_key(VK_CONTROL, False),
        _send_key(VK_C, False),
        _send_key(VK_C, True),
        _send_key(VK_CONTROL, True),
    ]
    ok = sum(1 for r in results if r)
    return ok, (kernel32.GetLastError() if ok < 4 else 0)


def cursor_pos():
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def foreground_class():
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(user32.GetForegroundWindow(), buf, 256)
    return buf.value


def foreground_process_name():
    """前台窗口属于哪个进程（小写，含 .exe）。取不到返回空串。"""
    hwnd = user32.GetForegroundWindow()
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ''

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    kernel32.OpenProcess.restype = w.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        w.HANDLE, w.DWORD, ctypes.c_wchar_p, ctypes.POINTER(w.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = w.BOOL
    kernel32.CloseHandle.argtypes = [w.HANDLE]

    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return ''
    try:
        size = w.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit('\\', 1)[-1].lower()
        return ''
    finally:
        kernel32.CloseHandle(h)


# 这些窗口里划选会出人命（Ctrl+C = 打断命令），一律不弹
TERMINAL_CLASSES = {
    'consolewindowclass',            # 传统 cmd / PowerShell
    'cascadia_hosting_window_class',  # Windows Terminal
    'mintty',                         # Git Bash
    'windowsterminal',
}
TERMINAL_PROCS = {
    'cmd.exe', 'powershell.exe', 'pwsh.exe', 'windowsterminal.exe',
    'mintty.exe', 'bash.exe', 'wsl.exe', 'conhost.exe', 'wt.exe',
}


def is_terminal():
    """当前前台窗口是不是终端/命令行。"""
    cls = foreground_class().lower()
    if cls in TERMINAL_CLASSES:
        return True
    proc = foreground_process_name()
    return proc in TERMINAL_PROCS


def screen_size():
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def work_area():
    """主屏的工作区（去掉任务栏）。返回 (left, top, right, bottom)。"""
    class RECT(ctypes.Structure):
        _fields_ = [('left', w.LONG), ('top', w.LONG),
                    ('right', w.LONG), ('bottom', w.LONG)]
    user32.SystemParametersInfoW.argtypes = [w.UINT, w.UINT, ctypes.c_void_p, w.UINT]
    r = RECT()
    # SPI_GETWORKAREA = 0x0030
    user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0)
    return r.left, r.top, r.right, r.bottom


# ─────────────────────────────────────────────────────────
#  5. 窗口小工具（浮窗要用）
# ─────────────────────────────────────────────────────────

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1

user32.SetWindowPos.argtypes = [w.HWND, w.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, w.UINT]
user32.SetWindowPos.restype = w.BOOL
user32.GetAncestor.argtypes = [w.HWND, w.UINT]
user32.GetAncestor.restype = w.HWND
GA_ROOT = 2


def real_hwnd(tk_window):
    """
    拿到 tk 窗口真正的顶层 HWND。

    tkinter 的 winfo_id() 给的可能是内部子窗口，圆角、置顶这些操作
    要作用在真正的顶层窗口上才有效。
    """
    try:
        h = tk_window.winfo_id()
        root = user32.GetAncestor(h, GA_ROOT)
        return root or h
    except Exception:
        return None


def popup_no_activate(hwnd, x, y, width, height):
    """
    显示浮窗但**不抢焦点**。

    抢焦点的话，用户在原软件里选中的文字会立刻被取消选中（看起来闪一下），
    很打扰。用 SWP_NOACTIVATE 就能只显示不抢。
    """
    if not hwnd:
        return
    user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height,
                        SWP_NOACTIVATE | SWP_SHOWWINDOW)


def round_corners(hwnd, radius_pref=2):
    """
    Win11 的圆角。radius_pref: 0=默认 1=不要圆角 2=圆角 3=小圆角
    Win10 上这个调用会失败，忽略即可。
    """
    if not hwnd:
        return False
    try:
        dwmapi = ctypes.windll.dwmapi
        val = ctypes.c_int(radius_pref)
        # DWMWA_WINDOW_CORNER_PREFERENCE = 33
        hr = dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(val), ctypes.sizeof(val))
        return hr == 0
    except Exception:
        return False
