"""
任务栏托盘图标 —— 纯 ctypes，不依赖 pystray / Pillow。

原理：建一个看不见的 Win32 窗口，让它专门收托盘消息。
      Shell_NotifyIcon 需要一个 HWND 来回调，不给它窗口就收不到点击。

整个跑在一条独立线程里（有自己的窗口和消息循环）。
回调只往队列里丢事件，主线程去处理。
"""

import ctypes
import ctypes.wintypes as w
import os
import threading
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_APP = 0x8000
WM_TRAY = WM_APP + 1
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIIF_INFO = 0x01

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
HWND_MESSAGE = -3
CW_USEDEFAULT = 0x80000000


class GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', w.DWORD), ('Data2', w.WORD), ('Data3', w.WORD),
        ('Data4', ctypes.c_byte * 8),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', w.DWORD),
        ('hWnd', w.HWND),
        ('uID', w.UINT),
        ('uFlags', w.UINT),
        ('uCallbackMessage', w.UINT),
        ('hIcon', w.HANDLE),
        ('szTip', ctypes.c_wchar * 128),
        ('dwState', w.DWORD),
        ('dwStateMask', w.DWORD),
        ('szInfo', ctypes.c_wchar * 256),
        ('uVersion', w.UINT),
        ('szInfoTitle', ctypes.c_wchar * 64),
        ('dwInfoFlags', w.DWORD),
        ('guidItem', GUID),
        ('hBalloonIcon', w.HANDLE),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ('cbSize', w.UINT),
        ('style', w.UINT),
        ('lpfnWndProc', WNDPROC),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', w.HINSTANCE),
        ('hIcon', w.HANDLE),
        ('hCursor', w.HANDLE),
        ('hbrBackground', w.HANDLE),
        ('lpszMenuName', ctypes.c_wchar_p),
        ('lpszClassName', ctypes.c_wchar_p),
        ('hIconSm', w.HANDLE),
    ]


user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.RegisterClassExW.restype = w.ATOM
user32.CreateWindowExW.argtypes = [
    w.DWORD, ctypes.c_wchar_p, ctypes.c_wchar_p, w.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = w.HWND
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.DestroyWindow.argtypes = [w.HWND]
user32.LoadImageW.argtypes = [w.HINSTANCE, ctypes.c_wchar_p, w.UINT,
                              ctypes.c_int, ctypes.c_int, w.UINT]
user32.LoadImageW.restype = w.HANDLE
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = w.BOOL


class Tray:
    """
    icon_path  — .ico 文件路径
    tooltip    — 鼠标悬停时显示的字
    on_event   — 回调，参数是 ('menu'|'click'|'dblclick', x, y)
                 ⚠️ 这个回调跑在托盘线程上，别在里面干重活，丢队列就行。
    """

    def __init__(self, icon_path, tooltip, on_event):
        self.icon_path = icon_path
        self.tooltip = tooltip[:127]
        self.on_event = on_event

        self.hwnd = None
        self.hicon = None
        self._thread = None
        self._running = False
        self._proc = None
        self._cls_atom = None
        self._nid = None
        self._ready = threading.Event()
        self.ok = False

    # ─────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name='Tray', daemon=True)
        self._thread.start()
        self._ready.wait(timeout=4)
        return self.ok

    def stop(self):
        self._running = False
        if self.hwnd:
            try:
                if self._nid:
                    shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
                user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
            except Exception:
                pass

    def notify(self, title, text):
        """弹个气泡提示（Win10/11 会走通知中心）。"""
        if not self._nid or not self._running:
            return
        try:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_INFO
            nid.szInfoTitle = title[:63]
            nid.szInfo = text[:255]
            nid.dwInfoFlags = NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    def set_tooltip(self, text):
        self.tooltip = text[:127]
        if not self._nid or not self._running:
            return
        try:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_TIP
            nid.szTip = self.tooltip
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
        except Exception:
            pass

    # ─────────────────────────────────────────────

    def _run(self):
        cls_name = f'AiHuaciTray_{os.getpid()}'
        hinst = kernel32.GetModuleHandleW(None)

        def wndproc(hwnd, msg, wparam, lparam):
            try:
                if msg == WM_TRAY:
                    x, y = _cursor()
                    if lparam == WM_RBUTTONUP:
                        self.on_event('menu', x, y)
                    elif lparam == WM_LBUTTONDBLCLK:
                        self.on_event('dblclick', x, y)
                    elif lparam == WM_LBUTTONUP:
                        self.on_event('click', x, y)
                    return 0
                if msg == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
            except Exception:
                pass
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._proc = WNDPROC(wndproc)          # 必须留引用

        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        self._cls_atom = user32.RegisterClassExW(ctypes.byref(wc))
        if not self._cls_atom:
            self._ready.set()
            return

        # 消息专用窗口：不显示、不出现在任务栏，但能收消息
        self.hwnd = user32.CreateWindowExW(
            0, cls_name, cls_name, 0, 0, 0, 0, 0,
            HWND_MESSAGE, None, hinst, None)
        if not self.hwnd:
            self._ready.set()
            return

        # 图标：文件在就用文件的，不在就退回系统默认图标
        if os.path.exists(self.icon_path):
            self.hicon = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                           LR_LOADFROMFILE)
        if not self.hicon:
            self.hicon = user32.LoadIconW(None, 32512)   # IDI_APPLICATION

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip

        self.ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        self._nid = nid
        self._ready.set()

        if not self.ok:
            return

        msg = w.MSG()
        while self._running:
            r = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if r in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass


def _cursor():
    class P(ctypes.Structure):
        _fields_ = [('x', w.LONG), ('y', w.LONG)]
    user32.GetCursorPos.argtypes = [ctypes.POINTER(P)]
    p = P()
    user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


if __name__ == '__main__':
    # 单独跑这个文件可以试托盘图标在不在
    import time as _t

    def on_ev(kind, x, y):
        print(f'  [事件] {kind} @({x},{y})')

    here = os.path.dirname(os.path.abspath(__file__))
    t = Tray(os.path.join(here, 'icon.ico'), '划词助手（测试）', on_ev)
    print('启动托盘…', t.start())
    print('看右下角托盘区，图标在吗？5 秒后自动退出')
    _t.sleep(5)
    t.stop()
    print('已退出')
