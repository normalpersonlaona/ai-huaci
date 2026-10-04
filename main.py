"""
划词助手 —— 主程序。

线程模型（这个理顺了整个东西才稳）：
    主线程     tkinter 主循环。所有界面操作都在这，别的地方碰 widget 会炸。
    钩子线程   全局鼠标钩子。回调里只往队列丢一条消息就立刻返回 ——
              回调卡住 = 全系统鼠标卡住。
    AI 线程    每次请求开一条，流式结果也是丢队列，主线程去取。
    托盘线程   Shell_NotifyIcon 的消息循环。

所有跨线程的东西都走 self.q 这一个队列，主线程用 after() 定时取。
"""

import os
import queue
import sys
import threading
import time
import tkinter as tk

import win32
import config as config_mod
import ai
from popup import Popup
from settings import SettingsWindow
from tray import Tray

HERE = os.path.dirname(os.path.abspath(__file__))
ICON = os.path.join(HERE, 'icon.ico')

# 抓选区时等剪贴板更新的上限（毫秒）。慢的软件可能要 200ms 以上。
CLIP_WAIT_MS = 320
DEBUG = '--debug' in sys.argv


def log(*a):
    if DEBUG:
        print('[debug]', *a, flush=True)


class App:
    def __init__(self):
        # ⚠️ 必须在建任何窗口之前
        win32.enable_dpi_awareness()

        self.cfg = config_mod.Config()
        self.q = queue.Queue()

        self.root = tk.Tk()
        self.root.withdraw()                 # 根窗口一直藏着，只用它的消息循环

        self.popup = Popup(self.root)
        self.popup.on_pick = self._on_pick
        self.popup.on_closed = self._on_popup_closed

        self.settings = SettingsWindow(self.root, self.cfg, on_saved=self._after_save)

        self.hook = win32.MouseHook(self._on_mouse)
        self.tray = None

        self.paused = False
        self._gen = 0                        # 请求代次，用来丢弃过期结果
        self._last_text = ''                 # 防重复弹
        self._last_time = 0.0
        self._menu = None
        self._busy_text = ''                 # 本次要处理的选中文字

    # ─────────────────────────────────────────────
    #  启动
    # ─────────────────────────────────────────────

    def run(self):
        self.hook.start()
        if not self.hook.alive:
            print('鼠标钩子装不上。可能有安全软件拦着。')

        self.tray = Tray(ICON, '划词助手', self._on_tray)
        tray_ok = self.tray.start()

        self._poll()                          # 开始轮询事件队列

        ready, why = self.cfg.ready()
        if not ready:
            self.root.after(600, lambda: self._first_run(why))
        elif tray_ok:
            self.tray.notify('划词助手已启动', '选中文字就会弹出按钮。右键托盘图标可以改设置。')

        self.root.mainloop()

    def _first_run(self, why):
        self.tray and self.tray.notify('划词助手还需要配置', why)
        self.settings.open()

    def _after_save(self):
        self.popup.hide()

    # ─────────────────────────────────────────────
    #  事件总线
    # ─────────────────────────────────────────────

    def _on_mouse(self, ev):
        """⚠️ 这个跑在钩子线程上。只准丢队列，别的什么都不许干。"""
        try:
            self.q.put(('mouse', ev))
        except Exception:
            pass

    def _on_tray(self, kind, x, y):
        """⚠️ 跑在托盘线程上。同样只丢队列。"""
        self.q.put(('tray', (kind, x, y)))

    def _poll(self):
        """主线程每 30ms 取一次队列。"""
        drained = 0
        while drained < 40:
            try:
                kind, payload = self.q.get_nowait()
            except queue.Empty:
                break
            drained += 1
            try:
                if kind == 'mouse':
                    self._handle_mouse(payload)
                elif kind == 'tray':
                    self._handle_tray(*payload)
                elif kind == 'delta':
                    gen, text = payload
                    if gen == self._gen:
                        self.popup.append(text)
                elif kind == 'done':
                    gen, text = payload
                    if gen == self._gen:
                        self.popup.append(text)
                elif kind == 'error':
                    gen, msg = payload
                    if gen == self._gen:
                        self.popup.error(msg)
            except Exception as e:
                if DEBUG:
                    print('[poll] 处理事件出错:', e, flush=True)

        self.root.after(30, self._poll)

    # ─────────────────────────────────────────────
    #  鼠标事件
    # ─────────────────────────────────────────────

    def _handle_mouse(self, ev):
        kind = ev.get('kind')

        if kind == 'click':
            # 点到浮窗外面 → 关掉
            if self.popup.visible and not self.popup.contains(ev.get('x', 0), ev.get('y', 0)):
                self.popup.hide()
            return

        if kind != 'select':
            return

        if self.paused:
            log('暂停中，跳过')
            return

        x, y = ev.get('x', 0), ev.get('y', 0)
        x0, y0 = ev.get('x0', x), ev.get('y0', y)

        # 在自己浮窗里划选（复制结果文字）→ 不算
        if self.popup.contains(x, y) or self.popup.contains(x0, y0):
            log('在自己浮窗里划的，忽略')
            return

        if self.cfg['skipTerminal'] and win32.is_terminal():
            log('终端里，跳过（避免 Ctrl+C 打断命令）')
            return

        self._capture(x, y)

    # ─────────────────────────────────────────────
    #  抓选区
    # ─────────────────────────────────────────────

    def _capture(self, x, y):
        """
        偷 Ctrl+C 拿选区。整套流程：
          备份剪贴板 → 发 Ctrl+C → 等它变 → 读出来 → 还原剪贴板 → 弹窗
        """
        formats = win32.clipboard_formats()
        if formats is None:
            return
        had_text = win32.CF_UNICODETEXT in formats or win32.CF_TEXT in formats
        backup = win32.clipboard_get_text() if had_text else None
        before = backup

        win32.send_ctrl_c()

        # 等剪贴板更新（慢的软件要一两百毫秒）
        got = None
        step = 20
        waited = 0
        while waited < CLIP_WAIT_MS:
            time.sleep(step / 1000.0)
            waited += step
            now = win32.clipboard_get_text()
            if now and now != before:
                got = now
                break

        # 还原用户原来的剪贴板
        if had_text:
            if backup is not None:
                win32.clipboard_set_text(backup)
        elif got:
            # 原来不是文本，还原不了（图片/文件这类），只能留着了
            pass

        if not got:
            log('没抓到东西（可能没选中文字，或者那个软件不吃模拟 Ctrl+C）')
            return

        text = got.strip()
        if len(text) < self.cfg['minChars']:
            log(f'太短（{len(text)} 字），不弹')
            return

        # 同一个位置、同一段文字，1 秒内不重复弹
        now_t = time.time()
        if text == self._last_text and now_t - self._last_time < 1.0:
            return
        self._last_text = text
        self._last_time = now_t

        self._busy_text = text
        self._gen += 1                       # 上一轮的流式结果作废

        self.popup.show_actions(x, y, text, self.cfg.actions, self._on_pick)

    # ─────────────────────────────────────────────
    #  点了按钮
    # ─────────────────────────────────────────────

    def _on_pick(self, action):
        if action is None:
            # 「换个动作」→ 回到按钮态
            self._gen += 1
            x, y = self.popup._anchor
            self.popup.show_actions(x, y, self._busy_text, self.cfg.actions, self._on_pick)
            return

        ready, why = self.cfg.ready()
        if not ready:
            self.popup.show_result(action.get('label', ''))
            self.popup.error(f'{why}\n\n右键托盘图标 → 设置，填一下。')
            return

        self._gen += 1
        gen = self._gen
        self.popup.show_result(action.get('label', ''))
        self.popup.append('')

        text = self._busy_text

        def work():
            def stop():
                return gen != self._gen

            ai.stream_chat(
                self.cfg,
                text,
                action,
                on_delta=lambda t: self.q.put(('delta', (gen, t))),
                on_done=lambda t: self.q.put(('done', (gen, t))),
                on_error=lambda e: self.q.put(('error', (gen, e))),
                should_stop=stop,
            )

        threading.Thread(target=work, daemon=True, name='AI').start()

    def _on_popup_closed(self):
        # 关掉浮窗 = 中止这次请求（代次一变，回来的结果就没人要了）
        self._gen += 1

    # ─────────────────────────────────────────────
    #  托盘
    # ─────────────────────────────────────────────

    def _handle_tray(self, kind, x, y):
        log('托盘事件:', kind)
        if kind == 'dblclick':
            self.settings.open()
        elif kind in ('click', 'menu'):
            # 左键和右键都弹菜单 —— 之前左键是直接切"暂停"，
            # 太容易误触（碰一下就不弹窗了，还不知道为啥），改成菜单更稳。
            self._show_menu(x, y)

    def _toggle_pause(self):
        self.paused = not self.paused
        if self.tray:
            self.tray.set_tooltip('划词助手（已暂停）' if self.paused else '划词助手')
            self.tray.notify('划词助手', '已暂停划词' if self.paused else '已恢复划词')
        if self.paused:
            self.popup.hide()

    def _show_menu(self, x, y):
        if self._menu and self._menu.winfo_exists():
            self._menu.destroy()

        m = tk.Toplevel(self.root)
        self._menu = m
        m.overrideredirect(True)
        m.attributes('-topmost', True)
        m.configure(bg='#c9ccd4')

        holder = tk.Frame(m, bg='#ffffff')
        holder.pack(fill='both', expand=True, padx=1, pady=1)

        def item(text, cb, fg='#1a1d21'):
            lbl = tk.Label(holder, text=text, bg='#ffffff', fg=fg,
                           font=('Microsoft YaHei UI', 10), anchor='w',
                           padx=16, pady=7, cursor='hand2')
            lbl.pack(fill='x')
            lbl.bind('<Enter>', lambda e: lbl.configure(bg='#2f6feb', fg='#ffffff'))
            lbl.bind('<Leave>', lambda e: lbl.configure(bg='#ffffff', fg=fg))
            lbl.bind('<Button-1>', lambda e: (self._close_menu(), cb()))
            return lbl

        item('设置…', self.settings.open)
        item('暂停划词' if not self.paused else '恢复划词', self._toggle_pause)
        tk.Frame(holder, bg='#e3e6ea', height=1).pack(fill='x', pady=4)
        item('打开配置文件夹', self._open_config_dir)
        item('退出', self._quit, fg='#d9453a')

        m.update_idletasks()
        w, h = m.winfo_reqwidth(), m.winfo_reqheight()
        left, top, right, bottom = win32.work_area()
        px = min(x, right - w - 4)
        py = min(y, bottom - h - 4)
        m.geometry(f'{w}x{h}+{max(left, px)}+{max(top, py)}')

        hwnd = win32.real_hwnd(m)
        win32.popup_no_activate(hwnd, px, py, w, h)
        win32.round_corners(hwnd)

        # 点菜单外面就收起来
        def watch():
            if not (self._menu and self._menu.winfo_exists()):
                return
            px_, py_ = win32.cursor_pos()
            if not (m.winfo_rootx() <= px_ <= m.winfo_rootx() + m.winfo_width()
                    and m.winfo_rooty() <= py_ <= m.winfo_rooty() + m.winfo_height()):
                # 只有鼠标离开且按下了才关，避免刚弹出来就被关掉
                import ctypes
                if ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000:
                    self._close_menu()
                    return
            self.root.after(80, watch)

        self.root.after(150, watch)

    def _close_menu(self):
        if self._menu and self._menu.winfo_exists():
            self._menu.destroy()
        self._menu = None

    def _open_config_dir(self):
        try:
            os.startfile(config_mod.CONFIG_DIR)
        except Exception:
            pass

    def _quit(self):
        try:
            self.hook.stop()
            if self.tray:
                self.tray.stop()
        finally:
            self.root.quit()


def main():
    app = App()
    try:
        app.run()
    except KeyboardInterrupt:
        app._quit()


if __name__ == '__main__':
    main()
