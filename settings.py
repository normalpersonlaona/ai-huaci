"""
设置窗口。

三个标签页：
  服务商 —— 填 Key、接口地址、模型
  动作   —— 浮窗上那排按钮，增删改，提示词自己写
  行为   —— 选中几个字才弹、终端里弹不弹、系统提示词
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import webbrowser

import config
import ai

FONT = ('Microsoft YaHei UI', 10)
FONT_S = ('Microsoft YaHei UI', 9)


class SettingsWindow:
    def __init__(self, root, cfg, on_saved=None):
        self.root = root
        self.cfg = cfg
        self.on_saved = on_saved
        self.win = None
        self._action_rows = []

    # ─────────────────────────────────────────────

    def open(self):
        if self.win and self.win.winfo_exists():
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            return
        self._build()

    def _build(self):
        self.win = tk.Toplevel(self.root)
        self.win.title('划词助手 · 设置')
        self.win.geometry('660x580')
        self.win.minsize(600, 460)
        self.win.configure(bg='#f6f7f9')

        style = ttk.Style()
        try:
            style.theme_use('vista')
        except Exception:
            pass
        style.configure('.', font=FONT)
        style.configure('TNotebook.Tab', padding=(16, 7))

        nb = ttk.Notebook(self.win)
        nb.pack(fill='both', expand=True, padx=12, pady=(12, 6))

        self._tab_provider(nb)
        self._tab_actions(nb)
        self._tab_behavior(nb)

        bar = tk.Frame(self.win, bg='#f6f7f9')
        bar.pack(fill='x', padx=12, pady=(0, 12))

        ttk.Button(bar, text='保存', command=self._save).pack(side='right')
        ttk.Button(bar, text='取消', command=self.win.destroy).pack(side='right', padx=(0, 8))
        ttk.Button(bar, text='测试连接', command=self._test).pack(side='left')

        self.status = tk.Label(bar, text='', bg='#f6f7f9', fg='#666', font=FONT_S)
        self.status.pack(side='left', padx=12)

        self.win.protocol('WM_DELETE_WINDOW', self.win.destroy)

    # ─────────────────────────────────────────────
    #  标签页 1：服务商
    # ─────────────────────────────────────────────

    def _tab_provider(self, nb):
        f = ttk.Frame(nb, padding=16)
        nb.add(f, text='  服务商  ')

        ttk.Label(f, text='服务商').grid(row=0, column=0, sticky='w', pady=6)
        self.var_provider = tk.StringVar(value=self.cfg['provider'])
        cb = ttk.Combobox(f, textvariable=self.var_provider, state='readonly',
                          values=[p['name'] for p in config.PROVIDERS], width=30)
        cb.grid(row=0, column=1, sticky='ew', pady=6)
        cb.bind('<<ComboboxSelected>>', self._pick_provider)

        ttk.Label(f, text='API Key').grid(row=1, column=0, sticky='w', pady=6)
        keyrow = ttk.Frame(f)
        keyrow.grid(row=1, column=1, sticky='ew', pady=6)
        self.var_key = tk.StringVar(value=self.cfg['apiKey'])
        self.ent_key = ttk.Entry(keyrow, textvariable=self.var_key, show='●')
        self.ent_key.pack(side='left', fill='x', expand=True)
        self.var_showkey = tk.BooleanVar(value=False)
        ttk.Checkbutton(keyrow, text='显示', variable=self.var_showkey,
                        command=lambda: self.ent_key.configure(
                            show='' if self.var_showkey.get() else '●')
                        ).pack(side='left', padx=(8, 0))

        self.link = ttk.Label(f, text='', foreground='#2f6feb', cursor='hand2')
        self.link.grid(row=2, column=1, sticky='w', pady=(0, 6))
        self.link.bind('<Button-1>', self._open_key_url)

        ttk.Label(f, text='接口地址').grid(row=3, column=0, sticky='w', pady=6)
        self.var_base = tk.StringVar(value=self.cfg['baseUrl'])
        ttk.Entry(f, textvariable=self.var_base).grid(row=3, column=1, sticky='ew', pady=6)

        ttk.Label(f, text='模型').grid(row=4, column=0, sticky='w', pady=6)
        mrow = ttk.Frame(f)
        mrow.grid(row=4, column=1, sticky='ew', pady=6)
        self.var_model = tk.StringVar(value=self.cfg['model'])
        self.cb_model = ttk.Combobox(mrow, textvariable=self.var_model, width=24)
        self.cb_model.pack(side='left', fill='x', expand=True)
        ttk.Button(mrow, text='拉取列表', width=10,
                   command=self._fetch_models).pack(side='left', padx=(8, 0))

        ttk.Label(f, text='（模型名可以自己填，不认识的直接打字）',
                  foreground='#888', font=FONT_S).grid(row=5, column=1, sticky='w')

        f.columnconfigure(1, weight=1)
        self._pick_provider(None, keep_values=True)

    def _pick_provider(self, _e, keep_values=False):
        name = self.var_provider.get()
        for p in config.PROVIDERS:
            if p['name'] == name:
                if not keep_values or not self.var_base.get():
                    self.var_base.set(p['baseUrl'])
                self.cb_model.configure(values=p['models'])
                if not keep_values:
                    self.var_model.set(p['models'][0] if p['models'] else '')
                    self.cfg['provider'] = p['id']
                self.current_provider = p
                if p['keyUrl']:
                    self.link.configure(text='去哪里拿 Key ↗')
                else:
                    self.link.configure(text='')
                return

    def _open_key_url(self, _e):
        p = getattr(self, 'current_provider', None)
        if p and p['keyUrl']:
            webbrowser.open(p['keyUrl'])

    def _fetch_models(self):
        base = self.var_base.get().strip()
        key = self.var_key.get().strip()
        if not key:
            self._say('先填 API Key', 'red')
            return
        self._say('拉取中…', '#666')

        def work():
            ids, err = ai.list_models(base, key)
            def back():
                if ids:
                    self.cb_model.configure(values=ids)
                    self._say(f'拉到 {len(ids)} 个模型', 'green')
                else:
                    self._say(err[:70] or '拉不到', 'red')
            self.win.after(0, back)

        threading.Thread(target=work, daemon=True).start()

    # ─────────────────────────────────────────────
    #  标签页 2：动作
    # ─────────────────────────────────────────────

    def _tab_actions(self, nb):
        f = ttk.Frame(nb, padding=16)
        nb.add(f, text='  动作  ')

        ttk.Label(f, text='浮窗上显示这几个按钮。名字和提示词都能改，也能加新的。',
                  foreground='#666', font=FONT_S).pack(anchor='w', pady=(0, 8))

        canvas = tk.Canvas(f, highlightthickness=0, bg='#ffffff')
        sb = ttk.Scrollbar(f, orient='vertical', command=canvas.yview)
        self.act_holder = ttk.Frame(canvas)

        self.act_holder.bind(
            '<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.create_window((0, 0), window=self.act_holder, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side='left', fill='both', expand=True)
        sb.pack(side='right', fill='y')

        for a in self.cfg.actions:
            self._add_action_row(a)

        ttk.Button(f, text='＋ 添加动作', command=lambda: self._add_action_row(
            {'label': '新动作', 'prompt': ''})).pack(anchor='w', pady=(8, 0))

    def _add_action_row(self, act):
        row = ttk.LabelFrame(self.act_holder, text='', padding=8)
        row.pack(fill='x', pady=4, padx=2)

        top = ttk.Frame(row)
        top.pack(fill='x')
        ttk.Label(top, text='按钮名字').pack(side='left')
        var_label = tk.StringVar(value=act.get('label', ''))
        ttk.Entry(top, textvariable=var_label, width=18).pack(side='left', padx=8)
        ttk.Button(top, text='删除', width=6,
                   command=lambda: self._del_row(row)).pack(side='right')

        ttk.Label(row, text='提示词（会拼在你选中的文字前面）',
                  foreground='#666', font=FONT_S).pack(anchor='w', pady=(8, 2))
        txt = tk.Text(row, height=3, wrap='word', font=FONT, relief='solid', borderwidth=1)
        txt.insert('1.0', act.get('prompt', ''))
        txt.pack(fill='x')

        self._action_rows.append({'frame': row, 'label': var_label, 'prompt': txt})

    def _del_row(self, row):
        self._action_rows = [r for r in self._action_rows if r['frame'] is not row]
        row.destroy()

    # ─────────────────────────────────────────────
    #  标签页 3：行为
    # ─────────────────────────────────────────────

    def _tab_behavior(self, nb):
        f = ttk.Frame(nb, padding=16)
        nb.add(f, text='  行为  ')

        ttk.Label(f, text='至少选中几个字才弹窗').grid(row=0, column=0, sticky='w', pady=8)
        self.var_min = tk.IntVar(value=self.cfg['minChars'])
        ttk.Spinbox(f, from_=1, to=20, textvariable=self.var_min, width=6
                    ).grid(row=0, column=1, sticky='w', pady=8)
        ttk.Label(f, text='调大一点可以避免鼠标手滑蹭出两个字就弹',
                  foreground='#888', font=FONT_S).grid(row=0, column=2, sticky='w', padx=8)

        self.var_skipterm = tk.BooleanVar(value=self.cfg['skipTerminal'])
        ttk.Checkbutton(f, text='终端 / 命令行窗口里不弹（防止 Ctrl+C 打断你正在跑的命令）',
                        variable=self.var_skipterm).grid(
            row=1, column=0, columnspan=3, sticky='w', pady=8)

        ttk.Label(f, text='系统提示词').grid(row=2, column=0, sticky='nw', pady=8)
        self.txt_sys = tk.Text(f, height=4, wrap='word', font=FONT,
                               relief='solid', borderwidth=1)
        self.txt_sys.insert('1.0', self.cfg['systemPrompt'])
        self.txt_sys.grid(row=2, column=1, columnspan=2, sticky='ew', pady=8)

        ttk.Label(f, text='最长回复（token）').grid(row=3, column=0, sticky='w', pady=8)
        self.var_max = tk.IntVar(value=self.cfg['maxTokens'])
        ttk.Spinbox(f, from_=100, to=8000, increment=100, textvariable=self.var_max, width=8
                    ).grid(row=3, column=1, sticky='w', pady=8)

        ttk.Label(f, text='温度（越高越放飞，0~2）').grid(row=4, column=0, sticky='w', pady=8)
        self.var_temp = tk.DoubleVar(value=self.cfg['temperature'])
        ttk.Spinbox(f, from_=0, to=2, increment=0.1, textvariable=self.var_temp, width=6
                    ).grid(row=4, column=1, sticky='w', pady=8)

        f.columnconfigure(1, weight=1)

    # ─────────────────────────────────────────────
    #  保存 / 测试
    # ─────────────────────────────────────────────

    def _collect(self):
        acts = []
        for r in self._action_rows:
            label = r['label'].get().strip()
            prompt = r['prompt'].get('1.0', 'end-1c').strip()
            if not label and not prompt:
                continue
            acts.append({
                'id': label or 'act',
                'label': label or '未命名',
                'prompt': prompt,
            })
        if not acts:
            acts = [{'id': 'summarize', 'label': '总结',
                     'prompt': '把下面的内容总结成要点。'}]

        p = getattr(self, 'current_provider', None) or self.cfg.provider()
        return {
            'provider': p['id'],
            'apiKey': self.var_key.get().strip(),
            'baseUrl': self.var_base.get().strip(),
            'model': self.var_model.get().strip(),
            'actions': acts,
            'minChars': max(1, self.var_min.get()),
            'skipTerminal': bool(self.var_skipterm.get()),
            'systemPrompt': self.txt_sys.get('1.0', 'end-1c').strip() or config.DEFAULTS['systemPrompt'],
            'maxTokens': max(50, self.var_max.get()),
            'temperature': float(self.var_temp.get()),
        }

    def _save(self):
        for k, v in self._collect().items():
            self.cfg[k] = v
        if self.cfg.save():
            self._say('已保存', 'green')
            if self.on_saved:
                self.on_saved()
            self.win.after(600, self.win.destroy)
        else:
            self._say('保存失败，看控制台', 'red')

    def _test(self):
        data = self._collect()
        if not data['apiKey']:
            self._say('先填 API Key', 'red')
            return
        self._say('测试中…', '#666')

        tmp = config.Config()
        tmp.data.update(data)

        def work():
            got = {'text': '', 'err': ''}
            ai.stream_chat(
                tmp, '你好', {'prompt': '回一句「连接正常」就行，别的不用说。'},
                on_delta=lambda t: None,
                on_done=lambda t: got.update(text=t),
                on_error=lambda e: got.update(err=e),
            )
            def back():
                if got['err']:
                    self._say('失败：' + got['err'].split('\n')[0][:60], 'red')
                else:
                    self._say('通！模型回了：' + got['text'].strip()[:30], 'green')
            self.win.after(0, back)

        threading.Thread(target=work, daemon=True).start()

    def _say(self, msg, color='#666'):
        if self.win and self.win.winfo_exists():
            self.status.configure(text=msg, fg=color)


# 允许外面直接 `import settings; settings.SettingsWindow`
__all__ = ['SettingsWindow']
