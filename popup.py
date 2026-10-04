"""
划词浮窗。

两个状态：
  按钮态  [总结] [翻译] [是什么？]            ← 选中文字后先出这个
  结果态  标题 + 流式文字 + [复制] [换个动作]  ← 点了按钮之后

刻意做的几件事：
  - 出现时**不抢焦点**（否则原软件里选中的文字会闪一下取消选中）
  - 自动躲开屏幕边缘，不会跑到任务栏底下去
  - 高度随内容长，长到上限就滚动
"""

import tkinter as tk
import win32

BG = '#1f2430'
BG_SOFT = '#2b3345'
BORDER = '#39415a'
FG = '#e7eaf2'
DIM = '#8d95ab'
ACCENT = '#4f8cf7'
ACCENT_HI = '#6ba0ff'
OK = '#5ec27a'
DANGER = '#e8635a'

FONT = ('Microsoft YaHei UI', 10)
FONT_S = ('Microsoft YaHei UI', 9)
FONT_TITLE = ('Microsoft YaHei UI', 10, 'bold')

LINE_H = 21          # 一行大概多高，用来估窗口高度


class Popup:
    MAX_W = 470
    MAX_H = 430
    MIN_W = 200
    ACTIONS_H = 46       # 按钮态就一行，固定高

    def __init__(self, root):
        self.root = root
        self.win = None
        self.hwnd = None
        self.state = 'hidden'          # hidden | actions | result

        self.selected_text = ''
        self.current_action = None
        self.on_pick = None
        self.on_closed = None

        self._anchor = (0, 0)
        self._card = None
        self._header = None
        self._title = None
        self._body = None
        self._text = None
        self._foot = None

    # ─────────────────────────────────────────────
    #  对外接口
    # ─────────────────────────────────────────────

    @property
    def visible(self):
        try:
            return self.state != 'hidden' and self.win is not None and self.win.winfo_exists()
        except Exception:
            return False

    def show_actions(self, x, y, text, actions, on_pick):
        """鼠标松开的位置 x/y，抓到的选中文字 text。"""
        self.selected_text = text
        self.on_pick = on_pick
        self.current_action = None
        self._anchor = (x, y)

        self._ensure_window()
        self._render_actions(actions)
        self.state = 'actions'

    def show_result(self, title):
        """从按钮态切到结果态（还没内容）。"""
        if not self.win or not self.win.winfo_exists():
            return
        self.state = 'result'
        self._body_clear()
        self._header.pack(fill='x')
        self._title.configure(text=title, fg=FG)
        self._build_text()
        self._build_footer()
        self._relayout()

    def append(self, full_text):
        """流式：每次拿到的是当前完整文本，直接整体替换。"""
        if not self._text or not self.visible:
            return
        self._text.configure(state='normal')
        self._text.delete('1.0', 'end')
        self._text.insert('1.0', full_text)
        self._text.configure(state='disabled')
        self._text.see('end')
        self._relayout(grow_only=True)

    def error(self, msg):
        if not self._text or not self.visible:
            return
        self._text.configure(state='normal', fg=DANGER)
        self._text.delete('1.0', 'end')
        self._text.insert('1.0', msg)
        self._text.configure(state='disabled')
        self._relayout(grow_only=True)

    def hide(self):
        was = self.visible
        try:
            if self.win and self.win.winfo_exists():
                self.win.withdraw()
        except Exception:
            pass
        self.state = 'hidden'
        self._text = None
        if was and self.on_closed:
            try:
                self.on_closed()
            except Exception:
                pass

    def contains(self, x, y):
        """点是不是落在浮窗里 —— 区分"点了自己"和"点了外面"。"""
        if not self.visible:
            return False
        try:
            wx, wy = self.win.winfo_rootx(), self.win.winfo_rooty()
            ww, wh = self.win.winfo_width(), self.win.winfo_height()
            return wx <= x <= wx + ww and wy <= y <= wy + wh
        except Exception:
            return False

    # ─────────────────────────────────────────────
    #  窗口
    # ─────────────────────────────────────────────

    def _ensure_window(self):
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            return
        self.win = tk.Toplevel(self.root)
        self.win.withdraw()
        self.win.overrideredirect(True)            # 去掉标题栏
        self.win.attributes('-topmost', True)      # 永远最上层

        outer = tk.Frame(self.win, bg=BORDER)      # 外圈 = 边框
        outer.pack(fill='both', expand=True, padx=1, pady=1)
        self._card = tk.Frame(outer, bg=BG)
        self._card.pack(fill='both', expand=True)

        self._header = tk.Frame(self._card, bg=BG)
        self._title = tk.Label(self._header, text='', bg=BG, fg=FG,
                               font=FONT_TITLE, anchor='w')
        self._title.pack(side='left', padx=(12, 0), pady=(9, 2))

        close = tk.Label(self._header, text='✕', bg=BG, fg=DIM,
                         font=FONT_S, cursor='hand2', padx=8)
        close.pack(side='right', padx=(0, 6), pady=(9, 2))
        close.bind('<Button-1>', lambda e: self.hide())
        close.bind('<Enter>', lambda e: close.configure(fg=DANGER))
        close.bind('<Leave>', lambda e: close.configure(fg=DIM))

        self._body = tk.Frame(self._card, bg=BG)
        self._body.pack(fill='both', expand=True)
        self.win.bind('<Escape>', lambda e: self.hide())

    def _body_clear(self):
        for child in self._body.winfo_children():
            child.destroy()
        self._text = None
        self._foot = None
        self._header.pack_forget()

    def _place(self, x, y, w, h):
        left, top, right, bottom = win32.work_area()
        px, py = x + 14, y + 20
        if px + w > right:
            px = x - w - 14
        if py + h > bottom:
            py = y - h - 20
        px = max(left + 4, min(px, right - w - 4))
        py = max(top + 4, min(py, bottom - h - 4))
        return px, py

    def _relayout(self, grow_only=False):
        if not (self.win and self.win.winfo_exists()):
            return
        self.win.update_idletasks()

        if self.state == 'result' and self._text:
            self._text.update_idletasks()
            # 用 Text 自己的 count 数行数，比按字数估准
            try:
                rows = int(self._text.count('1.0', 'end', 'displaylines')[0])
            except Exception:
                rows = int(self._text.index('end-1c').split('.')[0])
            h = 64 + rows * LINE_H + 38
            w = self.MAX_W
            w = max(self.MIN_W, min(w, self.MAX_W))
            h = max(120, min(h, self.MAX_H))
        else:
            # 按钮态：就一行按钮，别给它套个 MIN_H 下限，不然会多出一大块空白
            h = self.ACTIONS_H
            w = getattr(self, '_btn_w', self.MIN_W)
            w = max(self.MIN_W, min(w, self.MAX_W))

        if grow_only:
            w = max(w, self.win.winfo_width())
            h = max(h, self.win.winfo_height())

        px, py = self._place(self._anchor[0], self._anchor[1], w, h)
        self.win.geometry(f'{w}x{h}+{px}+{py}')
        self.win.update_idletasks()

        self.hwnd = win32.real_hwnd(self.win)
        win32.popup_no_activate(self.hwnd, px, py, w, h)
        win32.round_corners(self.hwnd)

    # ─────────────────────────────────────────────
    #  按钮态
    # ─────────────────────────────────────────────

    def _render_actions(self, actions):
        self._body_clear()
        self.state = 'actions'

        row = tk.Frame(self._body, bg=BG)
        row.pack(padx=8, pady=8)

        total_w = 0
        for act in actions:
            label = act.get('label', '?')
            btn = tk.Label(row, text=label, bg=BG_SOFT, fg=FG, font=FONT,
                           padx=14, pady=6, cursor='hand2')
            btn.pack(side='left', padx=4)
            btn.bind('<Enter>', lambda e, w=btn: w.configure(bg=ACCENT, fg='#ffffff'))
            btn.bind('<Leave>', lambda e, w=btn: w.configure(bg=BG_SOFT, fg=FG))
            btn.bind('<Button-1>', lambda e, a=act: self._pick(a))
            total_w += 28 + len(label) * 15 + 8

        self._btn_w = max(self.MIN_W, total_w + 24)
        self._relayout()

    def _pick(self, action):
        if self.on_pick:
            self.on_pick(action)

    # ─────────────────────────────────────────────
    #  结果态
    # ─────────────────────────────────────────────

    def _build_text(self):
        holder = tk.Frame(self._body, bg=BG)
        holder.pack(fill='both', expand=True, padx=12, pady=(2, 0))

        self._text = tk.Text(
            holder, wrap='word', bg=BG, fg=FG, font=FONT,
            relief='flat', highlightthickness=0, bd=0,
            padx=0, pady=0, spacing1=1, spacing3=3,
            state='disabled', cursor='arrow',
            selectbackground=ACCENT, selectforeground='#ffffff',
        )
        self._text.pack(fill='both', expand=True)
        self._text.bind('<MouseWheel>', self._on_wheel)
        self.win.bind('<MouseWheel>', self._on_wheel)

    def _on_wheel(self, e):
        if self._text:
            self._text.yview_scroll(int(-e.delta / 120), 'units')
        return 'break'

    def _build_footer(self):
        self._foot = tk.Frame(self._body, bg=BG)
        self._foot.pack(fill='x', padx=12, pady=(6, 10))
        self._mk_foot('复制', self._copy)
        self._mk_foot('换个动作', self._back)

    def _mk_foot(self, text, cb, fg=DIM):
        lbl = tk.Label(self._foot, text=text, bg=BG, fg=fg, font=FONT_S, cursor='hand2')
        lbl.pack(side='left', padx=(0, 16))
        lbl.bind('<Button-1>', lambda e: cb())
        lbl.bind('<Enter>', lambda e: lbl.configure(fg=ACCENT_HI))
        lbl.bind('<Leave>', lambda e: lbl.configure(fg=fg))
        return lbl

    def _copy(self):
        if not self._text:
            return
        txt = self._text.get('1.0', 'end-1c').strip()
        if not txt:
            return
        win32.clipboard_set_text(txt)
        for child in self._foot.winfo_children():
            if child.cget('text') == '复制':
                child.configure(text='已复制', fg=OK)
                self.win.after(1200, lambda c=child: c.configure(text='复制', fg=DIM))

    def _back(self):
        if self.on_pick:
            self.on_pick(None)     # None = 回到按钮态
