#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""bank_editor.py —— 题库编辑器（图形界面 · Python 标准库 tkinter，零依赖）。

双击「打开题库编辑器.bat」即可；也可以：
    python quiz\\bank_editor\\bank_editor.py [题库.json]

设计收敛（Fluent 那套原则的 tkinter 版）：
  · 颜色/间距集中在 PALETTE 与 PAD，控件不写死颜色；浅色/深色两套
  · 命令栏一行按用途分组（文件 | 编辑 | 数据），按钮宽度按中文字宽算，不截字
  · 表格：点表头排序（带 ▲▼ 指示）、斑马纹、没答案的行压暗
  · 下方详情面板看全题干、直接改答案，面板高度可拖
  · 破坏性操作可撤销（Ctrl+Z，20 步）；有未保存改动时标题标 ●，关窗会拦一下
  · 保存前自动备份成 <名字>.<时间戳>.bak

功能：打开/保存/另存为 · 导出选中 · 导出当前筛选结果 · 删除选中 · 撤销
      · 拆分(6 种，带预览) · 合并(3 种冲突策略) · 整理(同题多 key 合并) · 体检
      · 改答案 · 搜索/筛选 · 导出 CSV / 从 CSV 写回
"""

import ctypes
import csv
import datetime
import json
import os
import queue
import re
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bank_core as core  # noqa: E402
import bank_ai  # noqa: E402

APP = "易智教 · 题库编辑器"
VERSION = "2.4.3"
UNDO_MAX = 20
SEARCH_PH = "搜索题干 / 答案 / key…（空格分开＝全中）"
# 界面偏好（窗口大小/主题/排序/上次目录）存这儿，别往项目目录里塞文件
UI_STATE_PATH = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                             "yzj-bank-editor", "ui.json")


def load_ui_state():
    try:
        with open(UI_STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:
        return {}


def save_ui_state(state):
    try:
        os.makedirs(os.path.dirname(UI_STATE_PATH), exist_ok=True)
        with open(UI_STATE_PATH, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=1)
    except Exception:
        pass          # 存不了偏好不算事，别打断用户

# ---------------------------------------------------------------- 设计令牌
# 颜色集中在这里：浅色/深色各一套，控件不写死颜色。
PALETTE = {
    "light": {"bg": "#f6f7f9", "surface": "#ffffff", "border": "#e1e5ea", "text": "#1b1f24",
              "muted": "#6a7280", "accent": "#0f6cbd", "accentText": "#ffffff", "accentHover": "#0d5a9e",
              "alt": "#fafbfc", "sel": "#d7e8fb", "danger": "#c4314b", "dangerHover": "#a52640",
              "ok": "#137333", "warn": "#8a6d00", "head": "#eef2f6"},
    "dark": {"bg": "#1e1e1e", "surface": "#2a2a2a", "border": "#3b3b3b", "text": "#f2f2f2",
             "muted": "#a2a8ae", "accent": "#4cc2ff", "accentText": "#00263a", "accentHover": "#74d0ff",
             "alt": "#252525", "sel": "#0d4a6b", "danger": "#ff6b81", "dangerHover": "#ff8a9c",
             "ok": "#6dd58c", "warn": "#e8c14a", "head": "#333333"},
}
PAD = 8          # 基础间距：所有间距都是它的倍数（8/16/24），视觉节奏才稳
FONT_UI = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_MONO = ("Cascadia Mono", 9)


def enable_dpi():
    """Windows 高分屏下别糊：开启进程级 DPI 感知。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class BankEditor(tk.Tk):
    # ------------------------------------------------------------ 生命周期
    def __init__(self, initial=None, dark=False):
        super().__init__()
        enable_dpi()
        self.withdraw()                     # 布局算完再显示，免得看到控件跳一下
        self.geometry("1240x820")
        self.minsize(980, 680)

        self.ui_state = load_ui_state()
        if self.ui_state.get("geometry"):
            try:
                self.geometry(self.ui_state["geometry"])   # 上次关窗时多大，这次还多大
            except tk.TclError:
                pass
        self.path = None
        self.bank = {}
        self.rows_all = []
        self.rows = []
        self.sort_col = self.ui_state.get("sort_col") or None
        self.sort_desc = bool(self.ui_state.get("sort_desc", True))
        self.undo_stack = []
        self.dirty = False
        self.theme = "light"
        dark = dark or bool(self.ui_state.get("dark"))

        self.style = ttk.Style(self)
        try:
            self.style.theme_use("clam")     # clam 才能让我们自己控色
        except tk.TclError:
            pass
        self._build_fonts()
        self._fix_hi_dpi()
        self._build_menu()
        self._build_toolbar()
        self._build_filters()
        self._build_main()
        self._build_status()
        self._bind_keys()
        self.apply_theme("light")
        if dark:
            self.apply_theme("dark")
        self._set_title()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.show_empty(True)
        if initial and os.path.exists(initial):
            self.load_file(initial)
        self.deiconify()
        self.after(80, self._place_sash)

    def _build_fonts(self):
        """注册三个具名字体；控件统一引用，改字号只改 FONT_* 常量。"""
        self._fonts = []
        for name, spec in (("YZJ", FONT_UI), ("YZJTitle", FONT_TITLE), ("YZJMono", FONT_MONO)):
            try:
                tkfont.nametofont(name)
                continue
            except tk.TclError:
                pass
            kw = {"family": spec[0], "size": spec[1]}
            if len(spec) > 2:
                kw["weight"] = spec[2]
            self._fonts.append(tkfont.Font(self, name=name, exists=False, **kw))

    def _fix_hi_dpi(self):
        """开了 DPI 感知后 Tk 不会自己跟着缩放，这里按真实 DPI 校正一次。"""
        try:
            dpi = float(self.winfo_fpixels("1i"))
            if abs(dpi - 96.0) > 1.0:
                self.tk.call("tk", "scaling", dpi / 72.0)
        except Exception:
            pass

    # ------------------------------------------------------------ 主题
    def apply_theme(self, name):
        p = PALETTE[name]
        self.theme = name
        self.configure(bg=p["bg"])
        s = self.style
        s.configure(".", background=p["bg"], foreground=p["text"], font="YZJ")
        s.configure("TFrame", background=p["bg"])
        s.configure("Card.TFrame", background=p["surface"])
        s.configure("Head.TFrame", background=p["surface"])
        s.configure("TLabel", background=p["bg"], foreground=p["text"])
        s.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
        s.configure("Title.TLabel", background=p["surface"], foreground=p["text"], font="YZJTitle")
        s.configure("HeadMuted.TLabel", background=p["surface"], foreground=p["muted"])
        s.configure("Sec.TLabel", background=p["bg"], foreground=p["muted"], font="YZJTitle")
        s.configure("Status.TLabel", background=p["surface"], foreground=p["muted"], padding=(PAD, 4))
        s.configure("Dirty.TLabel", background=p["surface"], foreground=p["warn"], padding=(PAD, 4))
        s.configure("Empty.TLabel", background=p["surface"], foreground=p["muted"], font="YZJTitle")
        s.configure("Ph.TLabel", background=p["surface"], foreground=p["muted"])
        s.configure("TButton", background=p["surface"], foreground=p["text"], borderwidth=1,
                    focusthickness=0, padding=(10, 5), relief="flat")
        s.map("TButton", background=[("active", p["head"]), ("disabled", p["surface"])],
              foreground=[("disabled", p["muted"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["accentText"])
        s.map("Accent.TButton", background=[("active", p["accentHover"])])
        s.configure("Danger.TButton", background=p["surface"], foreground=p["danger"])
        s.map("Danger.TButton", background=[("active", p["head"])])
        s.configure("TEntry", fieldbackground=p["surface"], foreground=p["text"],
                    insertcolor=p["text"], bordercolor=p["border"], padding=5)
        s.configure("TCombobox", fieldbackground=p["surface"], background=p["surface"],
                    foreground=p["text"], arrowcolor=p["text"], padding=4)
        s.map("TCombobox", fieldbackground=[("readonly", p["surface"])])
        s.configure("Treeview", background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["text"], rowheight=26, borderwidth=0)
        s.map("Treeview", background=[("selected", p["sel"])], foreground=[("selected", p["text"])])
        s.configure("Treeview.Heading", background=p["head"], foreground=p["muted"],
                    relief="flat", padding=(6, 6), font="YZJ")
        s.map("Treeview.Heading", background=[("active", p["border"])])
        s.configure("TPanedwindow", background=p["bg"])
        s.configure("Sash", sashthickness=6, gripcount=0)
        for w in (self.txt_detail, self.txt_answer):
            w.configure(bg=p["surface"], fg=p["text"], insertbackground=p["text"],
                        highlightbackground=p["border"])
        if hasattr(self, "tree"):
            self._paint_tags()      # 行颜色是 Tk 层面配的，换主题要重配，否则还是旧底色
        self.update_theme_btn()

    def toggle_theme(self):
        self.apply_theme("dark" if self.theme == "light" else "light")

    def update_theme_btn(self):
        if hasattr(self, "btn_theme"):
            self.btn_theme.configure(text="☀ 浅色" if self.theme == "dark" else "🌙 深色")

    # ------------------------------------------------------------ 命令栏（一行，按用途分组）
    def _build_toolbar(self):
        # 两行：一行是文件/编辑，一行是数据处理。挤在一行会在 1240 宽下把右边几个按钮吃掉
        wrap = ttk.Frame(self, padding=(PAD * 2, PAD, PAD * 2, 0))
        wrap.pack(fill="x")

        def row():
            f = ttk.Frame(wrap)
            f.pack(fill="x", pady=(0, 4))
            return f

        def group(parent, actions):
            box = ttk.Frame(parent)
            box.pack(side="left", padx=(0, PAD))
            for text, cmd, style in actions:
                # 不写 width：让 Tk 按中文字宽自己撑开，中英混排也不会截字
                ttk.Button(box, text=text, command=cmd, style=style).pack(side="left", padx=(0, 4))

        def sep(parent):
            ttk.Separator(parent, orient="vertical").pack(side="left", fill="y", padx=(PAD, PAD))

        r1 = row()
        group(r1, [("打开…", self.open_file, "TButton"), ("保存", self.save_file, "Accent.TButton"),
                   ("另存为…", self.save_as, "TButton")])
        sep(r1)
        group(r1, [("导出选中…", self.export_selected, "TButton"),
                   ("导出筛选…", self.export_all, "TButton"),
                   ("删除", self.delete_selected, "Danger.TButton"),
                   ("撤销", self.undo, "TButton")])
        right = ttk.Frame(r1)
        right.pack(side="right")
        self.btn_theme = ttk.Button(right, text="🌙 深色", command=self.toggle_theme)
        self.btn_theme.pack(side="right")
        ttk.Button(right, text="帮助", command=self.show_help).pack(side="right", padx=(0, PAD))

        r2 = row()
        group(r2, [("拆分…", self.do_split, "TButton"), ("年级分流…", self.do_split_year, "TButton"),
                   ("合并…", self.do_merge, "TButton"), ("批量归并…", self.do_batch_year, "TButton"),
                   ("去重", self.do_consolidate, "TButton"), ("体检", self.do_health, "TButton"),
                   ("AI 整理…", self.do_ai_organize, "TButton")])

    def _build_filters(self):
        bar = ttk.Frame(self, padding=(PAD * 2, PAD, PAD * 2, PAD))
        bar.pack(fill="x")
        ttk.Label(bar, text="搜索").pack(side="left")
        self.q_var = tk.StringVar()
        ent = ttk.Entry(bar, textvariable=self.q_var, width=32)
        ent.pack(side="left", padx=(4, PAD))
        ent.bind("<KeyRelease>", lambda e: (self._sync_ph(), self.refresh()))
        ent.bind("<FocusIn>", lambda e: self._sync_ph())
        ent.bind("<FocusOut>", lambda e: self._sync_ph())
        self.ent_search = ent
        self.ph_lbl = ttk.Label(ent, text=SEARCH_PH, style="Ph.TLabel")   # 占位提示
        self.ph_lbl.bind("<Button-1>", lambda e: ent.focus_set())
        self._sync_ph()
        ttk.Label(bar, text="题型").pack(side="left")
        self.t_var = tk.StringVar(value="全部")
        cb = ttk.Combobox(bar, textvariable=self.t_var, state="readonly", width=10,
                          values=["全部"] + [core.type_name(i) for i in range(8)] + ["未标注"])
        cb.pack(side="left", padx=(4, PAD))
        cb.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(bar, text="来源").pack(side="left")
        self.src_var = tk.StringVar(value="全部")
        cb2 = ttk.Combobox(bar, textvariable=self.src_var, state="readonly", width=10,
                           values=["全部", "official", "learn", "import", "test"])
        cb2.pack(side="left", padx=(4, PAD))
        cb2.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(bar, text="科目").pack(side="left")
        self.subj_var = tk.StringVar(value="全部")
        self.cb_subj = ttk.Combobox(bar, textvariable=self.subj_var, state="readonly", width=16,
                                    values=["全部"])
        self.cb_subj.pack(side="left", padx=(4, PAD))
        self.cb_subj.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Button(bar, text="清除筛选", command=self.clear_filter).pack(side="left")
        ttk.Label(bar, text="点表头排序 · Ctrl/Shift 多选 · 选中一行在下面改答案",
                  style="Muted.TLabel").pack(side="right")

    def _sync_ph(self):
        """搜索框占位提示：空了且没焦点才显示。"""
        try:
            if self.q_var.get().strip() or self.focus_get() is self.ent_search:
                self.ph_lbl.place_forget()
            else:
                self.ph_lbl.place(x=7, y=5)
        except (KeyError, tk.TclError):
            pass

    # ------------------------------------------------------------ 主区
    def _build_main(self):
        self.pane = ttk.Panedwindow(self, orient="vertical")
        self.pane.pack(fill="both", expand=True, padx=PAD * 2, pady=(0, PAD))

        wrap = ttk.Frame(self.pane, style="Card.TFrame", padding=1)
        cols = ("no", "type", "subj", "question", "answer", "src", "from", "time", "key")
        self.tree = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        self.heads = {"no": ("#", 46, "e"), "type": ("题型", 72, "w"),
                      "subj": ("科目", 128, "w"), "question": ("题干", 372, "w"),
                      "answer": ("答案", 140, "w"), "src": ("来源", 76, "w"),
                      "from": ("来源账号", 138, "w"),
                      "time": ("写入时间", 118, "center"), "key": ("key", 96, "w")}
        for c in cols:
            _text, width, anchor = self.heads[c]
            self.tree.column(c, width=width, anchor=anchor, stretch=(c == "question"))
        self._sync_headings()
        hs = ttk.Scrollbar(wrap, orient="horizontal", command=self.tree.xview)   # 窄窗口时能横向拖
        hs.pack(side="bottom", fill="x")
        vs = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.on_double_click)
        self.tree.bind("<Button-3>", self.on_right_click)
        self.tree.bind("<<TreeviewSelect>>", lambda e: (self.update_status(), self.show_detail()))
        self.pane.add(wrap, weight=3)

        # 空状态：没打开文件时盖在表格上的提示（挂在表格那一格，别飘到窗口根上）
        self.empty_lbl = ttk.Label(wrap, style="Empty.TLabel", anchor="center", justify="center",
                                   text="还没有打开题库\n\n点左上角「打开…」\n或把 .json 文件拖到「打开题库编辑器.bat」上")

        detail = ttk.Frame(self.pane, style="Card.TFrame", padding=PAD)
        ttk.Label(detail, text="选中题的详情", style="Title.TLabel").pack(anchor="w")
        self.txt_detail = tk.Text(detail, height=3, wrap="word", relief="flat", padx=6, pady=6,
                                  font="YZJ")
        self.txt_detail.pack(fill="both", expand=True, pady=(4, PAD))
        row = ttk.Frame(detail, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="答案", style="Title.TLabel").pack(side="left")
        self.txt_answer = tk.Entry(row, relief="flat", font="YZJMono", highlightthickness=1)
        self.txt_answer.pack(side="left", fill="x", expand=True, padx=(4, PAD))
        self.txt_answer.bind("<Return>", lambda e: (self.save_answer(), "break")[1])   # 回车即存
        ttk.Button(row, text="保存这条", style="Accent.TButton", command=self.save_answer).pack(side="left")
        self.pane.add(detail, weight=1)

    def _place_sash(self):
        """把上下分隔条放到合适位置：表格占大头，详情面板留够看题的高度。"""
        try:
            h = self.pane.winfo_height()
            if h > 300:
                self.pane.sashpos(0, max(260, h - 202))
        except tk.TclError:
            pass

    def _build_status(self):
        bar = ttk.Frame(self, style="Head.TFrame")
        bar.pack(fill="x", side="bottom")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self.status, style="Status.TLabel").pack(side="left")
        self.dirty_var = tk.StringVar(value="")
        self.lbl_dirty = ttk.Label(bar, textvariable=self.dirty_var, style="Dirty.TLabel")
        self.lbl_dirty.pack(side="right")

    def _build_menu(self):
        m = tk.Menu(self, tearoff=0)
        fm = tk.Menu(m, tearoff=0)
        for label, cmd, acc in [("打开…", self.open_file, "Ctrl+O"), ("保存", self.save_file, "Ctrl+S"),
                                ("另存为…", self.save_as, "Ctrl+Shift+S"),
                                ("导出 CSV（Excel 看/改）…", self.export_csv, None),
                                ("从 CSV 写回答案…", self.import_csv, None)]:
            fm.add_command(label=label, command=cmd, accelerator=acc)
        fm.add_separator()
        fm.add_command(label="退出", command=self.on_close)
        m.add_cascade(label="文件", menu=fm)

        em = tk.Menu(m, tearoff=0)
        em.add_command(label="导出选中的题…", command=self.export_selected)
        em.add_command(label="导出当前筛选结果…", command=self.export_all, accelerator="Ctrl+E")
        em.add_command(label="删除选中的题", command=self.delete_selected)
        em.add_command(label="撤销上一步", command=self.undo, accelerator="Ctrl+Z")
        em.add_separator()
        em.add_command(label="全选", command=self.select_all, accelerator="Ctrl+A")
        em.add_command(label="查找", command=self._focus_search, accelerator="Ctrl+F")
        m.add_cascade(label="编辑", menu=em)

        dm = tk.Menu(m, tearoff=0)
        dm.add_command(label="拆分题库…", command=self.do_split)
        dm.add_command(label="按年级 × 科目分流…", command=self.do_split_year)
        dm.add_command(label="合并题库…", command=self.do_merge)
        dm.add_command(label="批量归并（多文件 → 按年级 × 科目出文件）…", command=self.do_batch_year)
        dm.add_command(label="整理去重（同一题多个 key 合并）", command=self.do_consolidate)
        dm.add_command(label="体检", command=self.do_health)
        dm.add_command(label="AI 整理题库（补科目 / 标无效题 / 找重复）…", command=self.do_ai_organize)
        m.add_cascade(label="数据", menu=dm)

        hm = tk.Menu(m, tearoff=0)
        hm.add_command(label="使用说明 / 快捷键", command=self.show_help)
        hm.add_command(label="切换浅色 / 深色", command=self.toggle_theme)
        m.add_cascade(label="帮助", menu=hm)
        self.config(menu=m)

    def _bind_keys(self):
        self.bind("<Control-o>", lambda e: self.open_file())
        self.bind("<Control-s>", lambda e: self.save_file())
        self.bind("<Control-S>", lambda e: self.save_as())
        self.bind("<Control-e>", lambda e: self.export_all())
        self.bind("<Control-f>", self._focus_search)
        self.bind("<Control-a>", self._select_all_ctx)
        self.bind("<Control-z>", lambda e: self.undo())
        self.bind("<Delete>", lambda e: self.delete_selected())
        self.bind("<F5>", lambda e: self.refresh())
        self.bind("<Escape>", lambda e: self.tree.selection_remove(*self.tree.selection()))

    def _focus_search(self, _event=None):
        self.ent_search.focus_set()
        self.ent_search.selection_range(0, "end")
        return "break"

    def _select_all_ctx(self, _event=None):
        """焦点在输入框里就全选文字，在列表里就全选行。"""
        try:
            cls = self.focus_get().winfo_class()
        except Exception:
            cls = ""
        if cls == "Text":
            self.focus_get().tag_add("sel", "1.0", "end-1c")
        elif cls in ("Entry", "TEntry"):
            self.focus_get().selection_range(0, "end")
        else:
            self.select_all()
        return "break"

    # ------------------------------------------------------------ 数据
    def push_undo(self, label):
        self.undo_stack.append((label, dict(self.bank)))
        if len(self.undo_stack) > UNDO_MAX:
            self.undo_stack.pop(0)

    def mark_dirty(self, on=True):
        self.dirty = bool(on)
        self._set_title()

    def undo(self):
        if not self.undo_stack:
            self.say("没有可撤销的操作了")
            return
        label, snap = self.undo_stack.pop()
        self.bank = snap
        self.rows_all = core.entries(self.bank)
        self.mark_dirty(True)
        self.refresh()
        self.say("已撤销：%s（还没写回文件，点「保存」才落盘）" % label)

    def load_file(self, path):
        try:
            self.bank = core.load_bank(path)
        except Exception as e:
            messagebox.showerror(APP, "打不开这个文件：\n%s" % e)
            return
        self.path = path
        self.rows_all = core.entries(self.bank)
        self.undo_stack.clear()
        self.show_empty(False)
        self.mark_dirty(False)
        self.refresh()
        meta = self.bank.get("__meta__") or {}
        ts = meta.get("exportedAt")
        when = datetime.datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d %H:%M") if ts else "未知"
        self.say("已打开 %s —— %d 条，导出时间 %s" % (os.path.basename(path), len(self.rows_all), when))

    def show_empty(self, on):
        if on:
            self.empty_lbl.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.empty_lbl.place_forget()

    def _sync_headings(self):
        """表头文字 + 排序箭头（▼降序 / ▲升序）。"""
        for c, (text, _w, _a) in self.heads.items():
            mark = ""
            if self.sort_col == c:
                mark = "  ▼" if self.sort_desc else "  ▲"
            self.tree.heading(c, text=text + mark, command=lambda cc=c: self.sort_by(cc))

    def _paint_tags(self):
        """斑马纹 × 有没有答案：四种组合全写死，免得标签合并时互相打架。"""
        p = PALETTE[self.theme]
        self.tree.tag_configure("odd", background=p["surface"], foreground=p["text"])
        self.tree.tag_configure("alt", background=p["alt"], foreground=p["text"])
        self.tree.tag_configure("odd-noans", background=p["surface"], foreground=p["muted"])
        self.tree.tag_configure("alt-noans", background=p["alt"], foreground=p["muted"])

    def _source_label(self, r):
        """「来源账号」列：优先 姓名(学号)；都没有就退到班级，最后给个 -"""
        stu, who, cls = str(r.get("stu") or ""), str(r.get("who") or ""), str(r.get("cls") or "")
        if who and stu:
            return "%s(%s)" % (who, stu)
        return stu or who or cls or "-"

    def refresh(self):
        t_sel = self.t_var.get()
        rev = {core.type_name(i): i for i in range(8)}
        t_filter = None if t_sel == "全部" else (rev.get(t_sel) if t_sel != "未标注" else "未标注")
        rows = core.filter_entries(self.rows_all, self.q_var.get(), t_filter,
                                   None if self.src_var.get() == "全部" else self.src_var.get())
        # v2.2 科目筛选：候选按当前题库动态生成（题库里没有的科目不会出现在下拉里）
        subs = sorted({str(r.get("subj") or "").strip() for r in self.rows_all if str(r.get("subj") or "").strip()})
        blank = any(not str(r.get("subj") or "").strip() for r in self.rows_all)
        vals = ["全部"] + subs + (["（没标科目）"] if blank else [])
        if list(self.cb_subj["values"]) != vals:
            self.cb_subj.configure(values=vals)
        s_sel = self.subj_var.get()
        if s_sel == "（没标科目）":
            rows = [r for r in rows if not str(r.get("subj") or "").strip()]
        elif s_sel and s_sel != "全部":
            rows = [r for r in rows if str(r.get("subj") or "").strip() == s_sel]
        if self.sort_col:
            keys = {"no": None, "type": lambda r: core.type_name(r["t"]), "question": lambda r: str(r["q"]),
                    "answer": lambda r: str(r["a"]), "src": lambda r: str(r["src"]),
                    "subj": lambda r: str(r.get("subj") or ""), "from": lambda r: self._source_label(r),
                    "time": lambda r: r["at"], "key": lambda r: r["key"]}
            kf = keys.get(self.sort_col)
            if kf:
                rows = sorted(rows, key=kf, reverse=self.sort_desc)
        self.rows = rows
        self.tree.delete(*self.tree.get_children())
        for i, r in enumerate(rows, 1):
            when = datetime.datetime.fromtimestamp(r["at"] / 1000).strftime("%m-%d %H:%M") if r["at"] else ""
            tag = ("alt" if i % 2 == 0 else "odd") + \
                  ("-noans" if not str(r.get("a") or "").strip() else "")
            self.tree.insert("", "end", iid=r["key"], tags=(tag,),
                             values=(i, core.type_name(r["t"]), (r.get("subj") or "-")[:40],
                                     (r["q"] or "")[:160],
                                     str(r["a"] or "")[:100], r["src"], self._source_label(r)[:24],
                                     when, r["key"]))
        self._paint_tags()
        self._sync_headings()
        self.update_status()
        self.show_detail()

    def sort_by(self, col):
        if col == "no":                      # 点 # 就恢复原始顺序（写入时间倒序）
            self.sort_col, self.sort_desc = None, True
            self.refresh()
            self.say("已恢复原始顺序")
            return
        self.sort_desc = not self.sort_desc if self.sort_col == col else True
        self.sort_col = col
        self.refresh()
        self.say("按「%s」排序（%s）" % (self.heads[col][0], "降序" if self.sort_desc else "升序"))

    def clear_filter(self):
        self.q_var.set("")
        self.t_var.set("全部")
        self.src_var.set("全部")
        self.subj_var.set("全部")
        self._sync_ph()
        self.refresh()

    def selected_keys(self):
        return list(self.tree.selection())

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def update_status(self):
        f = os.path.basename(self.path) if self.path else "未打开文件"
        self.status.set("%s　·　显示 %d / 共 %d 条　·　选中 %d 条　·　撤销 %d 步"
                        % (f, len(self.rows), len(self.rows_all), len(self.selected_keys()),
                           len(self.undo_stack)))
        self.dirty_var.set("● 有改动没保存，记得点「保存」" if self.dirty else "")
        self.lbl_dirty.configure(style="Dirty.TLabel" if self.dirty else "Status.TLabel")
        self._set_title()

    def _set_title(self):
        name = os.path.basename(self.path) if self.path else "未打开题库"
        self.title("%s v%s — %s%s" % (APP, VERSION, name, " ●" if self.dirty else ""))

    def on_close(self):
        if self.dirty and self.bank:
            ans = messagebox.askyesnocancel(APP, "有改动还没保存。\n\n是 = 保存并退出　否 = 不保存直接退出")
            if ans is None:
                return
            if ans:
                self.save_file()
                if self.dirty:       # 保存失败就别关窗，改的东西还在
                    return
        self.destroy()

    def say(self, msg):
        self.status.set(msg)

    def show_detail(self):
        keys = self.selected_keys()
        self.txt_detail.configure(state="normal")
        self.txt_detail.delete("1.0", "end")
        if len(keys) == 1:
            v = self.bank.get(keys[0]) or {}
            when = datetime.datetime.fromtimestamp((v.get("at") or 0) / 1000).strftime("%Y-%m-%d %H:%M:%S") if v.get("at") else "-"
            self.txt_detail.insert("end", "题干：%s\n\n题型：%s　来源：%s　写入：%s\nkey：%s\n"
                                   % (v.get("q") or "（这条没记题干：图片版或老数据）",
                                      core.type_name(v.get("t")), v.get("src") or "-", when, keys[0]))
            if v.get("subj") or v.get("cls") or v.get("stu") or v.get("who"):
                self.txt_detail.insert("end", "来源：%s　班级：%s　学号：%s　账号：%s\n"
                                       % (v.get("subj") or "-", v.get("cls") or "-",
                                          v.get("stu") or "-", v.get("who") or "-"))
            self.txt_answer.delete(0, "end")
            self.txt_answer.insert(0, str(v.get("a") or ""))
        elif len(keys) > 1:
            self.txt_detail.insert("end", "选中 %d 条（要改答案请只选一条）" % len(keys))
            self.txt_answer.delete(0, "end")
        else:
            self.txt_detail.insert("end", self._overview_text())
            self.txt_answer.delete(0, "end")
        self.txt_detail.configure(state="disabled")

    def _overview_text(self):
        """没选中任何行时，详情面板拿来当「概览 + 用法提示」，别空一大片。"""
        if not self.bank:
            return ("还没有打开题库。\n\n"
                    "点左上角「打开…」选一个导出的 .json；\n"
                    "或者把 .json 文件直接拖到「打开题库编辑器.bat」上。")
        h = core.health(self.bank)
        dist = "、".join("%s %d" % kv for kv in sorted(h["by_type"].items())) or "（空）"
        subs = sorted(h["by_subject"].items(), key=lambda kv: -kv[1])[:4]
        subj_line = "、".join("%s %d" % kv for kv in subs) or "（空）"
        return ("在列表里选一行 → 这里显示完整题干，并能直接改答案。\n\n"
                "题库概览\n"
                "  共 %d 条 · 记了题干的 %d 条 · 答案为空 %d 条\n"
                "  题型：%s\n"
                "  科目：%s\n"
                "  还没标来源的：%d 条（v1.4.7 之前收的老数据）\n"
                "  同一道题占多个 key：%d 组（可用「整理去重」合并）\n\n"
                "用法：Ctrl/Shift 多选后可「导出选中…」「删除选中」；\n"
                "      搜索框支持空格分隔的多个关键词，全部命中才显示；\n"
                "      搜索也能搜科目 / 学号（试试「光伏」或「2024130038」）。"
                % (h["total"], h["total"] - h["no_question"], h["no_answer"], dist, subj_line,
                   h.get("no_tag", 0), h["dup_questions"]))

    # ------------------------------------------------------------ 动作
    def open_file(self):
        p = filedialog.askopenfilename(title="选择题库 JSON",
                                       filetypes=[("题库 JSON", "*.json"), ("所有文件", "*.*")])
        if p:
            self.load_file(p)

    def save_file(self):
        if not self.path:
            return self.save_as()
        try:
            bak = core.save_bank(self.path, self.bank, backup=True)
        except Exception as e:
            messagebox.showerror(APP, "保存失败：\n%s" % e)
            return
        self.mark_dirty(False)
        self.say("已保存 %s%s" % (os.path.basename(self.path),
                                 ("（备份 %s）" % os.path.basename(bak)) if bak else ""))

    def save_as(self):
        if not self.bank:
            messagebox.showinfo(APP, "还没打开题库。")
            return
        p = filedialog.asksaveasfilename(title="另存为", defaultextension=".json",
                                         initialfile="yzj-bank-%s.json" % datetime.date.today().strftime("%Y%m%d"),
                                         filetypes=[("题库 JSON", "*.json")])
        if not p:
            return
        try:
            core.save_bank(p, self.bank, backup=False)
        except Exception as e:
            messagebox.showerror(APP, "保存失败：\n%s" % e)
            return
        self.path = p
        self.rows_all = core.entries(self.bank)
        self.mark_dirty(False)
        self.refresh()
        self.say("已另存为 %s（%d 条）" % (os.path.basename(p), len(self.rows_all)))

    def export_all(self):
        """把当前筛选/排序后的这些条另存成一个新题库（不动原文件）。"""
        if not self.bank:
            messagebox.showinfo(APP, "先打开一个题库。")
            return
        if not self.rows:
            messagebox.showinfo(APP, "当前筛选结果是空的，没东西可导。")
            return
        p = filedialog.asksaveasfilename(title="把当前显示的 %d 条导出为" % len(self.rows),
                                         defaultextension=".json",
                                         initialfile="yzj-bank-筛选%d条-%s.json" % (
                                             len(self.rows), datetime.datetime.now().strftime("%Y%m%d-%H%M")),
                                         filetypes=[("题库 JSON", "*.json")])
        if not p:
            return
        try:
            core.save_bank(p, core.subset(self.bank, [r["key"] for r in self.rows]), backup=False)
        except Exception as e:
            messagebox.showerror(APP, "导出失败：\n%s" % e)
            return
        self.say("已把当前显示的 %d 条导出 → %s" % (len(self.rows), os.path.basename(p)))

    def export_selected(self):
        keys = self.selected_keys()
        if not keys:
            messagebox.showinfo(APP, "先在列表里选几行（Ctrl / Shift 多选），再点「导出选中」。")
            return
        p = filedialog.asksaveasfilename(title="把选中的 %d 条导出为" % len(keys), defaultextension=".json",
                                         initialfile="yzj-bank-选中%d条-%s.json" % (
                                             len(keys), datetime.datetime.now().strftime("%Y%m%d-%H%M")),
                                         filetypes=[("题库 JSON", "*.json")])
        if not p:
            return
        try:
            core.save_bank(p, core.subset(self.bank, keys), backup=False)
        except Exception as e:
            messagebox.showerror(APP, "导出失败：\n%s" % e)
            return
        self.say("已导出选中的 %d 条 → %s" % (len(keys), os.path.basename(p)))
        if messagebox.askyesno(APP, "已导出 %d 条到\n%s\n\n要把它们从当前题库里删掉吗？" % (len(keys), p)):
            self._delete(keys)

    def delete_selected(self):
        keys = self.selected_keys()
        if not keys:
            messagebox.showinfo(APP, "先选中要删的行。")
            return
        self._delete(keys)

    def _delete(self, keys):
        if not messagebox.askyesno(APP, "确定删除这 %d 条吗？\n（删错了可以按 Ctrl+Z 撤销；保存时会自动备份）" % len(keys)):
            return
        self.push_undo("删除 %d 条" % len(keys))
        self.bank, n = core.delete_keys(self.bank, keys)
        self.rows_all = core.entries(self.bank)
        self.mark_dirty(True)
        self.refresh()
        self.say("已删除 %d 条（Ctrl+Z 可撤销；记得点「保存」写回文件）" % n)

    def save_answer(self):
        keys = self.selected_keys()
        if len(keys) != 1:
            messagebox.showinfo(APP, "请只选中一行再保存答案。")
            return
        key = keys[0]
        new = self.txt_answer.get().strip()
        if new == str((self.bank.get(key) or {}).get("a") or ""):
            self.say("答案没变化")
            return
        self.push_undo("改答案 %s" % key)
        self.bank = core.set_answer(self.bank, key, new)
        self.rows_all = core.entries(self.bank)
        self.mark_dirty(True)
        self.refresh()
        self.tree.selection_set(key)
        self.say("已改 %s 的答案（Ctrl+Z 可撤销；记得点「保存」写回文件）" % key)

    def on_double_click(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        self.tree.selection_set(row)
        self.txt_answer.focus_set()

    def on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            m = tk.Menu(self, tearoff=0)
            m.add_command(label="导出选中的题…", command=self.export_selected)
            m.add_command(label="删除选中的题", command=self.delete_selected)
            m.add_separator()
            m.add_command(label="撤销上一步", command=self.undo)
            m.tk_popup(event.x_root, event.y_root)

    # ------------------------------------------------------------ CSV
    def export_csv(self):
        if not self.bank:
            messagebox.showinfo(APP, "先打开题库。")
            return
        p = filedialog.asksaveasfilename(title="导出 CSV（Excel 可开）", defaultextension=".csv",
                                         initialfile="yzj-bank-%s.csv" % datetime.date.today().strftime("%Y%m%d"),
                                         filetypes=[("CSV", "*.csv")])
        if not p:
            return
        core.to_csv(self.bank, p)
        self.say("已导出 CSV → %s（用 Excel 改完再用「从 CSV 写回答案」）" % os.path.basename(p))

    def import_csv(self):
        if not self.bank:
            messagebox.showinfo(APP, "先打开题库。")
            return
        p = filedialog.askopenfilename(title="选改好的 CSV", filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")])
        if not p:
            return
        try:
            new_bank, stats = core.from_csv(self.bank, p)
        except Exception as e:
            messagebox.showerror(APP, "读 CSV 失败：%s" % e)
            return
        if not stats["changed"]:
            messagebox.showinfo(APP, "CSV 里没有答案变化（按 key 匹配）。")
            return
        if not messagebox.askyesno(APP, "按 CSV 更新 %d 条答案？\n（可 Ctrl+Z 撤销）" % stats["changed"]):
            return
        self.push_undo("从 CSV 更新 %d 条" % stats["changed"])
        self.bank = new_bank
        self.rows_all = core.entries(self.bank)
        self.mark_dirty(True)
        self.refresh()
        self.say("已按 CSV 更新 %d 条（Ctrl+Z 可撤销；记得点「保存」）" % stats["changed"])

    # ------------------------------------------------------------ 拆分 / 合并 / 整理 / 体检
    def _dialog(self, title, w=680, h=560):
        d = tk.Toplevel(self)
        d.title(title)
        d.transient(self)
        d.grab_set()
        d.geometry("%dx%d" % (w, h))
        d.configure(bg=PALETTE[self.theme]["bg"])
        return d

    def _tkw(self, widget):
        """给原生 tk 控件（Text / Entry）套上当前主题，深色下别白底白字。"""
        p = PALETTE[self.theme]
        try:
            widget.configure(bg=p["surface"], fg=p["text"], insertbackground=p["text"],
                             highlightthickness=1, highlightbackground=p["border"],
                             highlightcolor=p["accent"], relief="flat")
        except tk.TclError:
            pass
        return widget

    def do_split(self):
        if not self.bank:
            messagebox.showinfo(APP, "先打开一个题库。")
            return
        d = self._dialog("拆分题库", 720, 660)
        mode = tk.StringVar(value="groups")
        opts = [("groups", "★ 按多组关键词拆（分班 / 分科目，一次拆成好几份）"),
                ("paper", "★ 按整卷图片拆（同一张卷子的题放一起）"),
                ("type", "按题型拆（单选 / 多选 / 判断 / 填空…各一份）"),
                ("size", "按条数拆（每 N 条一份）"),
                ("keyword", "按题干关键词拆（含 / 不含两份）"),
                ("src", "按来源拆（official / learn / import…）")]
        for val, text in opts:
            ttk.Radiobutton(d, text=text, value=val, variable=mode).pack(anchor="w", padx=PAD * 2, pady=2)
        ttk.Label(d, text="多组关键词写法（一行一组：组名: 关键词1,关键词2）——没命中的进「未归类」：",
                  style="Muted.TLabel").pack(anchor="w", padx=PAD * 2, pady=(PAD, 2))
        grp = self._tkw(tk.Text(d, height=6, wrap="none", padx=6, pady=6))
        grp.pack(fill="x", padx=PAD * 2)
        grp.insert("1.0", "电气控制班: 电动机,起动,启动,继电器,接触器,行程开关\n"
                           "光伏班: 制绒,扩散,硅片,绒面,POCl3,杂质\n")
        row = ttk.Frame(d)
        row.pack(fill="x", padx=PAD * 2, pady=PAD)
        ttk.Label(row, text="每片条数").pack(side="left")
        size_var = tk.StringVar(value="100")
        ttk.Entry(row, textvariable=size_var, width=8).pack(side="left", padx=4)
        ttk.Label(row, text="关键词").pack(side="left", padx=(PAD * 2, 0))
        kw_var = tk.StringVar()
        ttk.Entry(row, textvariable=kw_var, width=18).pack(side="left", padx=4)
        orow = ttk.Frame(d)
        orow.pack(fill="x", padx=PAD * 2)
        # 默认就放在题库旁边，省得每次自己找路径
        base_dir = os.path.dirname(os.path.abspath(self.path)) if self.path else os.getcwd()
        out_var = tk.StringVar(value=os.path.join(
            base_dir, "拆分-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M")))
        ttk.Label(orow, text="输出目录").pack(side="left")
        ttk.Entry(orow, textvariable=out_var, width=44).pack(side="left", padx=4)
        ttk.Button(orow, text="选…",
                   command=lambda: out_var.set(filedialog.askdirectory(title="拆出来的文件放哪") or out_var.get())
                   ).pack(side="left")
        pv = tk.StringVar(value="点「预览」先看看会拆成几份 —— 预览不写任何文件。")
        ttk.Label(d, textvariable=pv, style="Muted.TLabel", wraplength=660,
                  justify="left").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))

        def compute():
            m = mode.get()
            if m == "groups":
                groups = []
                for line in grp.get("1.0", "end").splitlines():
                    if not re.search(r"[:：]", line):
                        continue
                    name, kws = re.split(r"[:：]", line, maxsplit=1)
                    kws = [w.strip() for w in re.split(r"[,，\s]+", kws) if w.strip()]
                    if name.strip() and kws:
                        groups.append((name.strip(), kws))
                if not groups:
                    raise ValueError("多组关键词至少写一行，形如：\n电气班: 电动机,继电器")
                return core.split_groups(self.bank, groups)
            if m == "paper":
                return core.split_by_paper(self.bank)
            return core.split(self.bank, mode=m, size=int(size_var.get() or 100), keyword=kw_var.get())

        def preview():
            try:
                parts = compute()
            except Exception as e:
                pv.set("预览失败：%s" % e)
                return
            top = sorted(parts.items(), key=lambda kv: -len(core.entries(kv[1])))[:6]
            pv.set("会拆成 %d 个文件：%s%s" % (
                len(parts),
                "、".join("%s（%d 条）" % (k, len(core.entries(v))) for k, v in top),
                " …" if len(parts) > 6 else ""))

        def run():
            outdir = out_var.get().strip()
            if not outdir:
                messagebox.showinfo(APP, "先选个输出目录。")
                return
            try:
                parts = compute()
            except Exception as e:
                messagebox.showerror(APP, "拆分失败：%s" % e)
                return
            base = os.path.splitext(os.path.basename(self.path or "bank"))[0]
            made = []
            for name, sub in parts.items():
                safe = "".join(ch for ch in name if ch not in '\\/:*?"<>|')
                path = os.path.join(outdir, "%s__%s.json" % (base, safe))
                core.save_bank(path, sub, backup=False)
                made.append((os.path.basename(path), len(core.entries(sub))))
            d.destroy()
            listing = "\n".join("%s（%d 条）" % x for x in made[:14])
            if messagebox.askyesno(APP, "拆好了 %d 个文件：\n%s%s\n\n要打开输出目录看看吗？"
                                   % (len(made), listing, "\n…" if len(made) > 14 else "")):
                starter = getattr(os, "startfile", None)
                if starter:
                    try:
                        starter(outdir)
                    except Exception:
                        pass
            self.say("拆分完成：%d 个文件 → %s" % (len(made), outdir))

        bar = ttk.Frame(d)
        bar.pack(fill="x", padx=PAD * 2, pady=PAD)
        ttk.Button(bar, text="开始拆分", style="Accent.TButton", command=run).pack(side="right")
        ttk.Button(bar, text="取消", command=d.destroy).pack(side="right", padx=PAD)
        ttk.Button(bar, text="预览", command=preview).pack(side="right", padx=(0, PAD))

    # ------------------------------------------------------------ 按年级分流
    @staticmethod
    def _year_js_text(name, sub, count):
        """一份题库 → 一个能被 @require / require / <script> 直接加载的 .js。"""
        return (
            "// 由「%s」导出 —— %s（%d 条，%s）\n"
            "// 用法一（Node）：const bank = require('./这个文件')\n"
            "// 用法二（网页 <script src>）：加载后挂在 window.__YZJ_BANK__\n"
            "// 用法三（并入「易智教助手」题库）：在油猴脚本或控制台里执行\n"
            "//     GM_setValue('answerBank', Object.assign(GM_getValue('answerBank', {}) || {}, bank));\n"
            "const bank = %s;\n"
            "if (typeof module !== 'undefined' && module.exports) module.exports = bank;\n"
            "if (typeof window !== 'undefined') window.__YZJ_BANK__ = bank;\n"
            % (APP, name, count, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
               json.dumps(sub, ensure_ascii=False, indent=1))
        )

    def _write_year_parts(self, parts, outdir, base, want_js, min_count=1):
        """把 {组名: 题库} 落盘；返回 (已写文件列表, 跳过的空组数)。"""
        os.makedirs(outdir, exist_ok=True)
        made, tiny = [], 0
        for name, sub in parts.items():
            n = len(core.entries(sub))
            if n < min_count:
                tiny += 1
                continue
            safe = "".join(ch for ch in str(name) if ch not in '\\/:*?"<>|').strip() or "未命名"
            core.save_bank(os.path.join(outdir, "%s__%s.json" % (base, safe)), sub, backup=False)
            made.append((os.path.basename("%s__%s.json" % (base, safe)), n))
            if want_js:
                path = os.path.join(outdir, "%s__%s.js" % (base, safe))
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self._year_js_text(name, sub, n))
        made.sort(key=lambda x: -x[1])
        return made, tiny

    def do_split_year(self):
        """按「年级 × 科目」分流：年级从班级名抽，科目靠可编辑词典。"""
        if not self.bank:
            messagebox.showinfo(APP, "先打开一个题库。")
            return
        d = self._dialog("按年级 × 科目分流", 760, 700)
        ttk.Label(d, text="年级取自条目的班级名（如「25级05班」→ 25级）；科目按下面的词典判。\n"
                          "带课程名的条目（v1.4.7 起采集的）判得准；老数据没这些字段，只能靠题干猜。",
                  style="Muted.TLabel", justify="left").pack(anchor="w", padx=PAD * 2, pady=(PAD, 2))

        ttk.Label(d, text="公共课关键词（命中 → 语数外）", style="Sec.TLabel").pack(anchor="w", padx=PAD * 2)
        pub = self._tkw(tk.Text(d, height=3, wrap="word", padx=6, pady=6))
        pub.pack(fill="x", padx=PAD * 2)
        pub.insert("1.0", "，".join(core.DEFAULT_PUBLIC_KEYWORDS))

        ttk.Label(d, text='专业课关键词（可选，一行逗号分隔；留空则“有课程名但不在公共课表里”一律算专业课）',
                  style="Sec.TLabel").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))
        maj = self._tkw(tk.Text(d, height=2, wrap="word", padx=6, pady=6))
        maj.pack(fill="x", padx=PAD * 2)
        maj.insert("1.0", "电动机,制绒,扩散,硅片,PLC,继电器")

        guess_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(d, text="没有课程名时，允许拿题干猜（猜出来的会在预览里标出条数）",
                        variable=guess_var).pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))

        pv = tk.StringVar(value="点「预览」先看看会分成哪几份 —— 预览不写任何文件。")
        ttk.Label(d, textvariable=pv, style="Muted.TLabel", wraplength=680,
                  justify="left").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))

        orow = ttk.Frame(d)
        orow.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        base_dir = os.path.dirname(os.path.abspath(self.path)) if self.path else os.getcwd()
        out_var = tk.StringVar(value=os.path.join(
            base_dir, "分年级-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M")))
        ttk.Label(orow, text="输出目录").pack(side="left")
        ttk.Entry(orow, textvariable=out_var, width=44).pack(side="left", padx=4)
        ttk.Button(orow, text="选…",
                   command=lambda: out_var.set(filedialog.askdirectory(title="拆出来的文件放哪") or out_var.get())
                   ).pack(side="left")
        js_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(d, text="同时输出 .js 包装文件（内容和 .json 一样，但能被 @require / require 直接加载）",
                        variable=js_var).pack(anchor="w", padx=PAD * 2, pady=(4, 0))

        def rules():
            return (core.parse_keywords(pub.get("1.0", "end")),
                    core.parse_keywords(maj.get("1.0", "end")))

        def preview():
            pk, mk = rules()
            groups, st = core.group_year_subject(self.bank, pk, mk, guess_var.get())
            top = sorted(groups.items(), key=lambda kv: -len(kv[1]))
            pv.set("共 %d 条 → %d 份：%s ｜ 其中靠题干猜的 %d 条、年级未知 %d 条、没科目 %d 条"
                   % (st["total"], len(groups),
                      "、".join("%s %d条" % (k, len(v)) for k, v in top[:6]),
                      st["guessed"], st["unknown_year"], st["no_subject"]))

        def run():
            outdir = out_var.get().strip()
            if not outdir:
                messagebox.showinfo(APP, "先选个输出目录。")
                return
            pk, mk = rules()
            parts = core.split_by_year_subject(self.bank, pk, mk, guess_var.get())
            base = os.path.splitext(os.path.basename(self.path or "bank"))[0]
            made, _tiny = self._write_year_parts(parts, outdir, base, js_var.get())
            d.destroy()
            listing = "\n".join("%s（%d 条）" % x for x in made[:14])
            if messagebox.askyesno(APP, "分流完成，共 %d 份：\n%s%s\n\n要打开输出目录吗？"
                                   % (len(made), listing, "\n…" if len(made) > 14 else "")):
                starter = getattr(os, "startfile", None)
                if starter:
                    try:
                        starter(outdir)
                    except Exception:
                        pass
            self.say("按年级×科目分流完成：%d 份 → %s" % (len(made), outdir))

        bar = ttk.Frame(d)
        bar.pack(fill="x", padx=PAD * 2, pady=PAD)
        ttk.Button(bar, text="开始分流", style="Accent.TButton", command=run).pack(side="right")
        ttk.Button(bar, text="取消", command=d.destroy).pack(side="right", padx=PAD)
        ttk.Button(bar, text="预览", command=preview).pack(side="right", padx=(0, PAD))

    def do_batch_year(self):
        """一次收一堆题库文件 → 合并去重 → 按「年级 × 科目」归并出文件。"""
        d = self._dialog("批量归并 → 按年级 × 科目", 780, 610)
        ttk.Label(d, text="① 把几个账号导出的题库（或以前分流出的小文件）一起选进来\n"
                          "② 先按 key 合并去重，再按「年级 × 科目」归并成若干份\n"
                          "输出目录里的文件会被自动忽略，重复跑不会自己吃自己。",
                  style="Muted.TLabel", justify="left").pack(anchor="w", padx=PAD * 2, pady=(PAD, 2))

        picked, seen = [], set()
        info = tk.StringVar(value="还没选文件。")

        def add(paths):
            added = 0
            for p in paths:
                p = os.path.abspath(p)
                if p not in seen:
                    seen.add(p)
                    picked.append(p)
                    added += 1
            info.set("%d 个文件：%s%s" % (len(picked),
                                          "、".join(os.path.basename(x) for x in picked[:6]),
                                          "…" if len(picked) > 6 else "")
                     + ("\n（这次新加了 %d 个）" % added if added else ""))

        def pick_files():
            ps = filedialog.askopenfilenames(title="选题库文件（可多选）",
                                             filetypes=[("题库 JSON", "*.json"), ("全部文件", "*.*")])
            if ps:
                add(ps)

        def pick_dir():
            p = filedialog.askdirectory(title="选一个文件夹，把它里面的题库都收进来")
            if not p:
                return
            found = []
            for root, dirs, files in os.walk(p):
                dirs[:] = [x for x in dirs if not x.startswith(".")]
                for fn in files:
                    if fn.lower().endswith(".json") and ".bak" not in fn.lower():
                        found.append(os.path.join(root, fn))
            add(found)

        srow = ttk.Frame(d)
        srow.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        ttk.Button(srow, text="选文件…", command=pick_files).pack(side="left")
        ttk.Button(srow, text="选文件夹…", command=pick_dir).pack(side="left", padx=4)
        ttk.Button(srow, text="清空", command=lambda: (picked.clear(), seen.clear(), info.set("还没选文件。"))
                   ).pack(side="left")
        ttk.Label(d, textvariable=info, style="Muted.TLabel", justify="left",
                  wraplength=700).pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))

        ttk.Label(d, text="多个文件里有同一个 key（同一道题）时：",
                  style="Sec.TLabel").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))
        policy = tk.StringVar(value="new")
        for val, text in [("new", "用后加进来的那份（后来的赢）"),
                          ("old", "保留先进来的（先到先得）"),
                          ("skip", "冲突就不动，谁也别覆盖谁")]:
            ttk.Radiobutton(d, text=text, value=val, variable=policy).pack(anchor="w", padx=PAD * 3)

        ttk.Label(d, text="公共课关键词（命中 → 语数外，含思政 / 体育 / 计算机基础等）",
                  style="Sec.TLabel").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))
        pub = self._tkw(tk.Text(d, height=3, wrap="word", padx=6, pady=6))
        pub.pack(fill="x", padx=PAD * 2)
        pub.insert("1.0", "，".join(core.DEFAULT_PUBLIC_KEYWORDS))

        ttk.Label(d, text='专业课关键词（可选，一行逗号分隔；留空则“有课程名但不在公共课表里”一律算专业课）',
                  style="Sec.TLabel").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))
        maj = self._tkw(tk.Text(d, height=2, wrap="word", padx=6, pady=6))
        maj.pack(fill="x", padx=PAD * 2)
        maj.insert("1.0", "电动机,制绒,扩散,硅片,PLC,继电器")

        orow = ttk.Frame(d)
        orow.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        guess_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(orow, text="没有课程名时允许拿题干猜", variable=guess_var).pack(side="left")
        ttk.Label(orow, text="　少于").pack(side="left")
        min_var = tk.StringVar(value="1")
        self._tkw(tk.Spinbox(orow, from_=1, to=50, width=3, textvariable=min_var,
                             buttonbackground=PALETTE[self.theme]["surface"])).pack(side="left")
        ttk.Label(orow, text="条的组不单独出文件").pack(side="left")

        brow = ttk.Frame(d)
        brow.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        base_var = tk.StringVar(value="yzj-bank-归并")
        ttk.Label(brow, text="文件前缀").pack(side="left")
        ttk.Entry(brow, textvariable=base_var, width=18).pack(side="left", padx=(2, PAD))
        out_var = tk.StringVar(value=os.path.join(
            os.path.expanduser("~"), "Desktop",
            "题库归并-%s" % datetime.datetime.now().strftime("%Y%m%d-%H%M")))
        ttk.Label(brow, text="输出目录").pack(side="left")
        ttk.Entry(brow, textvariable=out_var, width=30).pack(side="left", padx=4)
        ttk.Button(brow, text="选…",
                   command=lambda: out_var.set(filedialog.askdirectory(title="归并结果放哪") or out_var.get())
                   ).pack(side="left")
        js_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(d, text="同时输出 .js 包装文件（能被 @require / require 直接加载）", variable=js_var
                        ).pack(anchor="w", padx=PAD * 2, pady=(4, 0))

        pv = tk.StringVar(value="点「预览」先看看会归并成哪几份 —— 预览不读也不写任何文件。")
        ttk.Label(d, textvariable=pv, style="Muted.TLabel", wraplength=700, justify="left"
                  ).pack(anchor="w", padx=PAD * 2, pady=(2, 0))

        def usable_paths():
            outdir = os.path.abspath(out_var.get().strip() or ".")
            keep = []
            for p in picked:
                if os.path.abspath(os.path.dirname(p)) == outdir:
                    continue          # 别把上一次的输出又吃回来
                keep.append(p)
            return keep

        def compute():
            paths = usable_paths()
            if not paths:
                return None, None, None
            banks, bad = [], []
            for p in paths:
                try:
                    banks.append(core.load_bank(p))
                except Exception:
                    bad.append(os.path.basename(p))
            if not banks:
                return None, bad, None
            merged, _mstat = core.merge(banks, policy=policy.get())
            pk = core.parse_keywords(pub.get("1.0", "end"))
            mk = core.parse_keywords(maj.get("1.0", "end"))
            groups, st = core.group_year_subject(merged, pk, mk, guess_var.get())
            return merged, bad, (groups, st)

        def preview():
            merged, bad, res = compute()
            if merged is None:
                pv.set("还没有能读的题库文件。" + ("读不了：%s" % "、".join(bad[:5]) if bad else ""))
                return
            groups, st = res
            try:
                mn = max(1, int(min_var.get()))
            except Exception:
                mn = 1
            keep = {k: v for k, v in groups.items() if len(v) >= mn}
            top = sorted(keep.items(), key=lambda kv: -len(kv[1]))
            pv.set("%d 个文件 → 合并 %d 条 → 分成 %d 份（≥%d 条）：%s ｜ 靠题干猜 %d 条、年级未知 %d 条、没科目 %d 条%s"
                   % (len(usable_paths()), st["total"], len(keep), mn,
                      "、".join("%s %d条" % (k, len(v)) for k, v in top[:6]),
                      st["guessed"], st["unknown_year"], st["no_subject"],
                      "；读不了：" + "、".join(bad[:3]) if bad else ""))

        def run():
            outdir = out_var.get().strip()
            if not outdir:
                messagebox.showinfo(APP, "先选个输出目录。")
                return
            merged, bad, res = compute()
            if merged is None:
                messagebox.showinfo(APP, "没有能读的题库文件。" + ("读不了：%s" % "、".join(bad[:5]) if bad else ""))
                return
            groups, _st = res
            try:
                mn = max(1, int(min_var.get()))
            except Exception:
                mn = 1
            base = base_var.get().strip() or "yzj-bank-归并"
            made, tiny = self._write_year_parts(groups, outdir, base, js_var.get(), min_count=mn)
            d.destroy()
            listing = "\n".join("%s（%d 条）" % x for x in made[:14])
            msg = ("归并完成：%d 个文件 → 合并 %d 条 → 写出 %d 份%s\n%s%s\n\n要打开输出目录吗？"
                   % (len(usable_paths()), len(core.entries(merged)), len(made),
                      "（另有 %d 份不足 %d 条没出）" % (tiny, mn) if tiny else "",
                      listing, "\n…" if len(made) > 14 else "")
                   + ("\n\n读不了的文件：%s" % "、".join(bad[:5]) if bad else ""))
            if messagebox.askyesno(APP, msg):
                starter = getattr(os, "startfile", None)
                if starter:
                    try:
                        starter(outdir)
                    except Exception:
                        pass
            self.say("批量归并完成：%d 份 → %s" % (len(made), outdir))

        bar = ttk.Frame(d)
        bar.pack(fill="x", padx=PAD * 2, pady=PAD)
        ttk.Button(bar, text="开始归并", style="Accent.TButton", command=run).pack(side="right")
        ttk.Button(bar, text="取消", command=d.destroy).pack(side="right", padx=PAD)
        ttk.Button(bar, text="预览", command=preview).pack(side="right", padx=(0, PAD))

    def do_merge(self):
        paths = filedialog.askopenfilenames(title="选要合并的题库（可多选，按你选的顺序）",
                                            filetypes=[("题库 JSON", "*.json"), ("所有文件", "*.*")])
        if not paths:
            return
        d = self._dialog("合并题库（%d 个文件）" % len(paths), 640, 420)
        ttk.Label(d, text="\n".join(os.path.basename(p) for p in paths[:8]) + ("\n…" if len(paths) > 8 else ""),
                  justify="left", style="Muted.TLabel").pack(anchor="w", padx=PAD * 2, pady=PAD)
        ttk.Label(d, text="遇到同一个 key（同一道题）时：").pack(anchor="w", padx=PAD * 2)
        policy = tk.StringVar(value="new")
        for val, text in [("new", "用后一个文件的（按你选的顺序，后面的覆盖前面）"),
                          ("old", "保留已有的（先选的赢）"),
                          ("skip", "冲突就跳过，谁都不改")]:
            ttk.Radiobutton(d, text=text, value=val, variable=policy).pack(anchor="w", padx=PAD * 3)
        keep = tk.BooleanVar(value=True)
        ttk.Checkbutton(d, text="合并后直接载入编辑器里看（推荐）", variable=keep).pack(anchor="w",
                                                                          padx=PAD * 2, pady=PAD)

        def run():
            banks = []
            for p in paths:
                try:
                    banks.append(core.load_bank(p))
                except Exception as e:
                    messagebox.showerror(APP, "读不了 %s：%s" % (os.path.basename(p), e))
                    return
            merged, stats = core.merge(banks, policy=policy.get())
            out = filedialog.asksaveasfilename(title="合并结果存到哪", defaultextension=".json",
                                               initialfile="yzj-bank-合并-%s.json" % datetime.datetime.now().strftime("%Y%m%d-%H%M"),
                                               filetypes=[("题库 JSON", "*.json")])
            if not out:
                return
            core.save_bank(out, merged, backup=False)
            d.destroy()
            messagebox.showinfo(APP, "合并完成：%d 个文件 → %d 条\n新增 %d，覆盖 %d，跳过 %d\n\n%s"
                                % (stats["files"], len(core.entries(merged)), stats["added"],
                                   stats["updated"], stats["skipped"], out))
            if keep.get():
                self.load_file(out)

        bar = ttk.Frame(d)
        bar.pack(fill="x", padx=PAD * 2, pady=PAD)
        ttk.Button(bar, text="合并并保存…", style="Accent.TButton", command=run).pack(side="right")
        ttk.Button(bar, text="取消", command=d.destroy).pack(side="right", padx=PAD)

    def do_consolidate(self):
        if not self.bank:
            messagebox.showinfo(APP, "先打开一个题库。")
            return
        h = core.health(self.bank)
        if not h["dup_keys"]:
            messagebox.showinfo(APP, "体检结果：没有「同一道题占多个 key」的情况，不用整理。")
            return
        if not messagebox.askyesno(APP, "整理（重组）：把指向同一道题的多个指纹合并成一条。\n\n"
                                        "预计合并 %d 组、减少 %d 条（%d → %d）\n"
                                        "只合并有题干的条目；图片指纹那种不动。\n\n继续吗？"
                                        % (h["dup_questions"], h["dup_keys"], h["total"], h["total"] - h["dup_keys"])):
            return
        self.push_undo("整理去重")
        self.bank, stats = core.consolidate(self.bank)
        self.rows_all = core.entries(self.bank)
        self.mark_dirty(True)
        self.refresh()
        self.say("整理完成：合并 %d 组、减少 %d 条（Ctrl+Z 可撤销；记得点「保存」）"
                 % (stats["groups"], stats["merged"]))

    # ---------------------------------------------------------- AI 整理（v2.4）
    def do_ai_organize(self):
        """极简 Agent：AI 只出结构化建议 → 程序校验 → 人工勾选 → 才落库。"""
        if not self.bank:
            messagebox.showinfo(APP, "先打开一个题库。")
            return
        cand = bank_ai.candidates(core.entries(self.bank), max_items=100000)
        if not cand:
            messagebox.showinfo(APP, "这个库里没有带题干的条目。\n"
                                     "图片键（i… / d… / p…）只有哈希没有文字，AI 没法整理。")
            return
        cfg = bank_ai.load_config()
        d = self._dialog("AI 整理题库（极简 Agent）", 860, 620)
        ttk.Label(d, text="AI 只提建议：补科目 / 标无效题 / 找近似重复。程序先校验，再让你勾选；没勾的一律不动。\n"
                          "不做「补答案」：题库只存题干前 100 字、不存选项，凭题干补答案很容易把错答案写进去。",
                  style="Muted.TLabel", justify="left", wraplength=800
                  ).pack(anchor="w", padx=PAD * 2, pady=(PAD, 4))

        id_by_name = {p["name"]: p["id"] for p in bank_ai.PROVIDERS}
        prov_var = tk.StringVar(value=bank_ai.provider_by_id(cfg.get("provider"))["name"])
        proto_var = tk.StringVar(value=cfg.get("proto") or "openai")
        base_var = tk.StringVar(value=cfg.get("base_url") or "")
        key_var = tk.StringVar(value=cfg.get("api_key") or "")
        model_var = tk.StringVar(value=cfg.get("model") or "")

        r1 = ttk.Frame(d)
        r1.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        ttk.Label(r1, text="服务商").pack(side="left")
        prov_cb = ttk.Combobox(r1, state="readonly", width=22, values=list(id_by_name))
        prov_cb.set(prov_var.get())
        prov_cb.pack(side="left", padx=(4, PAD * 2))
        ttk.Label(r1, text="协议").pack(side="left")
        proto_cb = ttk.Combobox(r1, state="readonly", width=14, values=bank_ai.PROTOS)
        proto_cb.set(proto_var.get())
        proto_cb.pack(side="left", padx=4)

        def on_prov(_evt=None):
            p = bank_ai.provider_by_id(id_by_name.get(prov_cb.get(), ""))
            prov_var.set(p["name"])
            proto_var.set(p["proto"])
            proto_cb.set(p["proto"])
            base_var.set(p["base"])
            if p["models"]:
                model_var.set(p["models"][0])
        prov_cb.bind("<<ComboboxSelected>>", on_prov)
        proto_cb.bind("<<ComboboxSelected>>", lambda _e: proto_var.set(proto_cb.get()))

        ttk.Label(d, text="接口地址（base_url，不要带 /chat/completions）").pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))
        ttk.Entry(d, textvariable=base_var).pack(fill="x", padx=PAD * 2)

        r2 = ttk.Frame(d)
        r2.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        ttk.Label(r2, text="API Key").pack(side="left")
        ttk.Entry(r2, textvariable=key_var, show="*", width=34).pack(side="left", padx=(4, PAD * 2))
        ttk.Label(r2, text="模型").pack(side="left")
        model_cb = ttk.Combobox(r2, textvariable=model_var, width=28,
                                values=(bank_ai.provider_by_id(cfg.get("provider"))["models"] or []))
        model_cb.pack(side="left", padx=4)
        detect_btn = ttk.Button(r2, text="检测模型")
        detect_btn.pack(side="left")

        def detect():
            c = {"provider": id_by_name.get(prov_cb.get(), ""), "proto": proto_var.get(),
                 "base_url": base_var.get().strip(), "api_key": key_var.get().strip(),
                 "model": model_var.get().strip(), "timeout": 30}
            detect_btn.state(["disabled"])
            d.config(cursor="watch")
            d.update()
            try:
                models = bank_ai.list_models(c)
                if not models:
                    messagebox.showinfo(APP, "这家没返回模型列表，直接手填模型名也能用。")
                    return
                model_cb["values"] = models
                model_var.set(models[0])
                messagebox.showinfo(APP, "读到 %d 个模型，已填进下拉框。" % len(models))
            except Exception as e:
                messagebox.showerror(APP, "没拉到模型列表：\n%s" % e)
            finally:
                detect_btn.state(["!disabled"])
                d.config(cursor="")
        detect_btn.configure(command=detect)

        ttk.Label(d, text="要 AI 做什么").pack(anchor="w", padx=PAD * 2, pady=(PAD * 2, 0))
        task_vars = {}
        for tid, label, tip in bank_ai.TASKS:
            v = tk.BooleanVar(value=True)
            task_vars[tid] = v
            ttk.Checkbutton(d, text="%s —— %s" % (label, tip), variable=v).pack(anchor="w", padx=PAD * 3)

        r3 = ttk.Frame(d)
        r3.pack(fill="x", padx=PAD * 2, pady=(PAD, 0))
        ttk.Label(r3, text="最多处理").pack(side="left")
        max_var = tk.StringVar(value=str(min(200, len(cand))))
        self._tkw(tk.Spinbox(r3, from_=1, to=max(1, len(cand)), width=6, textvariable=max_var,
                             buttonbackground=PALETTE[self.theme]["surface"])).pack(side="left", padx=4)
        ttk.Label(r3, text="条　每批").pack(side="left")
        batch_var = tk.StringVar(value="20")
        self._tkw(tk.Spinbox(r3, from_=1, to=50, width=4, textvariable=batch_var,
                             buttonbackground=PALETTE[self.theme]["surface"])).pack(side="left", padx=4)
        ttk.Label(r3, text="条（这批里有 %d 条带题干）" % len(cand)).pack(side="left")

        pv = tk.StringVar(value="点「开始分析」——先跑一遍不写任何东西，结果会让你勾选。")
        ttk.Label(d, textvariable=pv, style="Muted.TLabel", wraplength=800, justify="left"
                  ).pack(anchor="w", padx=PAD * 2, pady=(PAD, 0))

        q = queue.Queue()
        state = {"running": False}

        def start():
            if state["running"]:
                return
            tasks = [t for t, v in task_vars.items() if v.get()]
            if not tasks:
                messagebox.showinfo(APP, "至少勾一项。")
                return
            prov = bank_ai.provider_by_id(id_by_name.get(prov_cb.get(), ""))
            base, key = base_var.get().strip(), key_var.get().strip()
            if not base:
                messagebox.showinfo(APP, "接口地址没填。")
                return
            if not key and not prov.get("no_key"):
                messagebox.showinfo(APP, "API Key 没填。")
                return
            c = {"provider": prov["id"], "proto": proto_var.get(), "base_url": base,
                 "model": model_var.get().strip(), "api_key": key, "timeout": 60}
            try:
                bank_ai.save_config(c)
            except Exception as e:
                messagebox.showwarning(APP, "配置没存下来（不影响本次）：%s" % e)
            try:
                n = max(1, min(len(cand), int(max_var.get())))
            except Exception:
                n = min(len(cand), 200)
            try:
                size = max(1, min(50, int(batch_var.get())))
            except Exception:
                size = 20
            rows = cand[:n]
            state["running"] = True
            start_btn.state(["disabled"])
            pv.set("准备中…")

            def work():
                try:
                    props, errs = bank_ai.analyze(
                        rows, c, tasks, size,
                        on_progress=lambda a, b, total: q.put(("p", a, b, total)))
                    q.put(("done", props, errs))
                except Exception as e:
                    q.put(("fail", e))
            threading.Thread(target=work, daemon=True).start()
            d.after(120, poll)

        def poll():
            if not d.winfo_exists():
                return
            try:
                while True:
                    msg = q.get_nowait()
                    if msg[0] == "p":
                        pv.set("分析中：第 %d–%d 条 / 共 %d 条…" % (msg[1], msg[2], msg[3]))
                    elif msg[0] == "done":
                        state["running"] = False
                        d.destroy()
                        self._ai_preview(msg[1], msg[2])
                        return
                    else:
                        state["running"] = False
                        start_btn.state(["!disabled"])
                        pv.set("出错了：%s" % msg[1])
            except queue.Empty:
                pass
            d.after(150, poll)

        bar = ttk.Frame(d)
        bar.pack(fill="x", padx=PAD * 2, pady=PAD)
        start_btn = ttk.Button(bar, text="开始分析", style="Accent.TButton", command=start)
        start_btn.pack(side="right")
        ttk.Button(bar, text="取消", command=d.destroy).pack(side="right", padx=PAD)

    def _ai_preview(self, proposals, errors):
        """把 AI 建议摊成表；默认全选，但只有点「应用」才会动题库。"""
        if not proposals:
            tip = "AI 没有给出可用建议。"
            if errors:
                tip += "\n\n" + "\n".join(errors[:8])
            messagebox.showinfo(APP, tip)
            return
        w = self._dialog("AI 整理预览（%d 条建议）" % len(proposals), 920, 640)
        kinds = {"subj": "补科目", "junk": "无效题", "dupe": "重复"}
        ttk.Label(w, text="默认全选；不勾的不会动。删除类会再确认一次，应用后随时 Ctrl+Z 撤销。",
                  style="Muted.TLabel").pack(anchor="w", padx=PAD * 2, pady=(PAD, 4))
        wrap = ttk.Frame(w)
        wrap.pack(fill="both", expand=True, padx=PAD * 2)
        cols = ("kind", "key", "old", "new", "why")
        tv = ttk.Treeview(wrap, columns=cols, show="headings", selectmode="extended")
        for c, (t, width) in {"kind": ("类型", 80), "key": ("条目", 150), "old": ("原值", 260),
                              "new": ("新值", 190), "why": ("理由 / 置信度", 160)}.items():
            tv.heading(c, text=t)
            tv.column(c, width=width, anchor="w", stretch=(c in ("old", "new")))
        vs = ttk.Scrollbar(wrap, orient="vertical", command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        for i, p in enumerate(proposals):
            old = str(p.get("old") or "")
            new = str(p.get("new") or "")
            if p["kind"] == "junk":
                new = "（建议删除）"
            elif p["kind"] == "dupe":
                new = "保留 " + str(p.get("keep") or "")
            tv.insert("", "end", iid=str(i), values=(
                kinds.get(p["kind"], p["kind"]), p["key"], old[:120], new[:120],
                "%s　%.2f" % (p.get("why") or "", p.get("confidence") or 0)))
        tv.selection_set(tv.get_children())

        if errors:
            ttk.Label(w, text="有 %d 条问题（不影响其它建议）：%s" % (len(errors), "；".join(errors[:3])),
                      style="Muted.TLabel", wraplength=860).pack(anchor="w", padx=PAD * 2, pady=(4, 0))

        bar = ttk.Frame(w)
        bar.pack(fill="x", padx=PAD * 2, pady=PAD)

        def apply():
            sel = [proposals[int(i)] for i in tv.selection()]
            if not sel:
                messagebox.showinfo(APP, "一条都没选。")
                return
            dels = [p for p in sel if p["kind"] in ("junk", "dupe")]
            if dels and not messagebox.askyesno(
                    APP, "其中 %d 条会被删除（可 Ctrl+Z 撤销）。继续？" % len(dels)):
                return
            self.push_undo("AI 整理")
            self.bank, stats = bank_ai.apply_proposals(self.bank, sel)
            self.rows_all = core.entries(self.bank)
            self.mark_dirty(True)
            self.refresh()
            w.destroy()
            self.say("AI 整理已应用：补科目 %d 条，删除 %d 条。Ctrl+Z 可撤销；记得点「保存」落盘。"
                     % (stats["subj"], stats["deleted"]))

        ttk.Button(bar, text="应用选中", style="Accent.TButton", command=apply).pack(side="right")
        ttk.Button(bar, text="取消", command=w.destroy).pack(side="right", padx=PAD)
        ttk.Button(bar, text="全不选", command=lambda: tv.selection_remove(*tv.get_children())).pack(side="right")
        ttk.Button(bar, text="全选", command=lambda: tv.selection_set(tv.get_children())).pack(side="right", padx=(0, PAD))

    def do_health(self):
        if not self.bank:
            messagebox.showinfo(APP, "先打开一个题库。")
            return
        h = core.health(self.bank)
        latest = datetime.datetime.fromtimestamp(h["latest"] / 1000).strftime("%Y-%m-%d %H:%M") if h["latest"] else "（无）"
        dist = "、".join("%s %d" % kv for kv in sorted(h["by_type"].items())) or "（空）"
        messagebox.showinfo(APP + " · 体检",
                            "共 %d 条\n\n题型分布：%s\n\n没记题干：%d 条（图片版或老数据）\n"
                            "答案为空：%d 条\n同一题占多个 key：%d 组、多出 %d 条（可用「整理去重」）\n"
                            "最后更新：%s" % (h["total"], dist, h["no_question"], h["no_answer"],
                                          h["dup_questions"], h["dup_keys"], latest))

    def show_help(self):
        messagebox.showinfo(APP + " · 帮助",
                            "常用操作\n"
                            "  Ctrl+O 打开　Ctrl+S 保存　Ctrl+Shift+S 另存为\n"
                            "  Ctrl+E 导出当前筛选结果　Ctrl+A 全选　Delete 删除选中\n"
                            "  Ctrl+Z 撤销（20 步）　Ctrl+F 搜索　F5 刷新　Esc 取消选中\n\n"
                            "怎么用\n"
                            "  · 搜索框支持多个关键词（空格分开，全中才显示）；题型/来源可下拉筛\n"
                            "  · 点表头排序（▼降序 ▲升序），点「#」回到原始顺序\n"
                            "  · 选中一行 → 下方详情看全题干、直接改答案；双击某行也能直接答题\n"
                            "  · 没答案的行会压暗显示，一眼看出还缺哪些\n"
                            "  · 导出选中 = 另存选中的题；导出筛选结果 = 把当前筛出来的都另存\n"
                            "  · 拆分（6 种）可以点「预览」先看会拆成几份，再落盘\n"
                            "  · 表格/详情之间的横条可以拖着调高度\n\n"
                            "题库里没有班级字段：带题干的（q…）能按内容分；\n"
                            "只有哈希的（i…/d…/p…）只能按「整卷图片」把同一套卷子聚在一起。\n\n"
                            "保存前会自动备份成 <名字>.<时间戳>.bak，改坏能回去。\n"
                            "有改动没保存时，标题和状态栏会亮 ●；这时关窗会先问你要不要存。")


def main():
    args = sys.argv[1:]
    dark = "--dark" in args
    args = [a for a in args if not a.startswith("--")]
    app = BankEditor(args[0] if args else None, dark=dark)
    app.mainloop()


if __name__ == "__main__":
    main()
