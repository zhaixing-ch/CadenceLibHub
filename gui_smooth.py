"""Canvas-rendered CadenceLibHub UI.

The layout is painted on a single Tk canvas. Only the search entry, the three
scrollable lists and the log text are real widgets, so resizing the window does
not trigger a full CTk widget re-layout for every card.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import threading
import time
import tkinter as tk
import urllib.parse
import urllib.request
import webbrowser
from tkinter import ttk

import cairosvg
import cadence_lib as core
from PIL import Image, ImageTk


def load_component_preview(image_url: str) -> bytes:
    if str(image_url).startswith("//"):
        image_url = "https:" + str(image_url)
    request = urllib.request.Request(
        image_url,
        headers={"User-Agent": "Mozilla/5.0 CadenceLibHub/1.0"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        photo = Image.open(io.BytesIO(response.read())).convert("RGBA")
    photo.thumbnail((360, 360), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    photo.save(output, format="PNG")
    return output.getvalue()


def load_footprint_preview(component: str) -> bytes:
    svg = core.lcsc_footprint_svg(component)
    return cairosvg.svg2png(
        bytestring=svg, output_width=360, background_color="#202733"
    )


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------
BG = "#101319"
PANEL = "#1b202a"
CARD = "#242a36"
FIELD = "#171d27"
TEXT = "#edf1f7"
MUTED = "#b7c0cf"
DIM = "#818da0"
ACCENT = "#9ce5e1"
ON_ACCENT = "#102c32"
ACCENT_HOVER = "#b4eeea"
ACCENT_CONTAINER = "#29434a"
OUTLINE = "#46515e"
SURFACE_LOW = "#171c25"
SURFACE_HIGH = "#29313e"
CYAN = "#b8d6ff"
AMBER = "#f4ca91"
GREEN = "#a8e1c2"
ERROR = "#f3b8bd"
GLASS_EDGE = "#738394"
GLASS_SHEEN = "#526071"

F_TITLE = ("Segoe UI", 24, "bold")
F_SUB = ("Segoe UI", 10)
F_H2 = ("Segoe UI", 14, "bold")
F_SECTION = ("Segoe UI", 13, "bold")
F_CARD = ("Segoe UI", 12, "bold")
F_BODY = ("Segoe UI", 10)
F_SMALL = ("Segoe UI", 9)
F_BTN = ("Segoe UI", 11, "bold")
F_NUM = ("Segoe UI", 10, "bold")
F_CODE = ("Consolas", 9)

LCSC_STEPS = [
    ("搜索器件", "输入型号或 C 编号（例如 C165948），来源选 LCSC，点“搜索”。"),
    ("添加到库", "在结果卡片点“添加”；已知编号可直接点“按编号添加”。"),
    ("Capture 导入", "File → Import → Library XML\nXML：_staging\\<库名>\\<库名>.xml\nOLB：Symbols\\<库名>.olb"),
    ("Allegro 生成", 'Allegro 命令窗口执行：\nskill load("<library-root>/_staging/<库名>/<库名>_allegro/<库名>.il")'),
    ("生成封装", "回到本工具点“生成封装”，输入库名，自动复制到 Footprints。"),
    ("检查匹配", "点“检查匹配”，确认符号 / 封装 / 3D 全部 yes。"),
]

SAMACSYS_STEPS = [
    ("搜索下载", "来源选 SamacSys，点“搜索”，在网页或 Library Loader 中登录并下载。"),
    ("同步下载", "下载完成后点“同步下载”，自动归档到 MyLib。"),
    ("检查匹配", "点“检查匹配”，确认符号 / 封装 / 3D 全部 yes。"),
]

BUTTON_ORDER = "搜索  →  添加  →  Capture 导入  →  Allegro 生成  →  生成封装  →  检查匹配"


def round_rect(canvas: tk.Canvas, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle drawn as one smooth polygon."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [
        x1 + r, y1,
        x1 + r, y1,
        x2 - r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=18, **kw)


class InputDialog(tk.Toplevel):
    def __init__(self, text: str, title: str = "输入") -> None:
        super().__init__()
        self.title(title)
        self.configure(bg=BG)
        self.geometry("420x210")
        self.resizable(False, False)
        self.result: str | None = None

        tk.Label(
            self,
            text=text,
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 11),
            wraplength=370,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=24, pady=(22, 10))

        self.entry = tk.Entry(
            self,
            bg=FIELD,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 12),
        )
        self.entry.pack(fill="x", padx=24, ipady=9)
        self.entry.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())

        buttons = tk.Frame(self, bg=BG)
        buttons.pack(fill="x", padx=24, pady=(18, 16))
        tk.Button(
            buttons,
            text="取消",
            command=self._cancel,
            bg=SURFACE_HIGH,
            fg=TEXT,
            activebackground=OUTLINE,
            activeforeground=TEXT,
            relief="flat",
            bd=0,
            font=("Segoe UI", 11),
            cursor="hand2",
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        tk.Button(
            buttons,
            text="确定",
            command=self._ok,
            bg=ACCENT,
            fg=ON_ACCENT,
            activebackground=ACCENT_HOVER,
            activeforeground=ON_ACCENT,
            relief="flat",
            bd=0,
            font=("Segoe UI", 11, "bold"),
            cursor="hand2",
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

        self.entry.focus_set()
        self.after(20, self._center)

    def _center(self) -> None:
        self.update_idletasks()
        master = self.master
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _ok(self) -> None:
        self.result = self.entry.get()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self.destroy()

    def get_input(self) -> str | None:
        self.wait_window()
        return self.result


class App:
    PLACEHOLDER = "输入型号或 LCSC 编号，例如 C165948 / TYPE-C 16P"
    PREVIEW_CACHE_TTL = 7 * 24 * 60 * 60
    PREVIEW_CACHE_LIMIT = 64
    PREVIEW_CACHE_DIR = Path(core.HERE) / ".preview_cache"

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("CadenceLibHub")
        self.root.geometry("1280x820")
        self.root.minsize(1120, 700)
        self.root.configure(bg=BG)

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self._resize_job = None
        self._results_job = None
        self._guide_job = None
        self._status_job = None
        self._progress_job = None
        self._phase = 0.0
        self.busy = False
        self.tab = "guide"
        self.source = "LCSC"
        self.results: list[dict] = []
        self.preview_item: dict | None = None
        self.preview_request = 0
        self._preview_jobs: set[tuple[str, str]] = set()
        self._preview_pending: set[str] = set()
        self._preview_failed = False
        self.component_photo = None
        self.footprint_photo = None
        self.preview_cache: dict[str, dict[str, object]] = {}
        self.PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._purge_disk_preview_cache()
        self.status_rows: list[dict] = []
        self.hits: list[tuple[float, float, float, float, callable]] = []
        self._wins: dict[str, tuple[int, int, int, int]] = {}
        self._res_select_hits: list[tuple[float, float, float, float, dict]] = []

        self._build_widgets()
        self._bind_events()
        self.root.after_idle(self._draw)
        self.root.after(50, self._first_guide)
        self.root.after(60, self.query_entry.focus_set)

    # ------------------------------------------------------------------
    # widget setup
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        try:
            style.layout(
                "Dark.Vertical.TScrollbar",
                [
                    (
                        "Vertical.Scrollbar.trough",
                        {
                            "sticky": "ns",
                            "children": [
                                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})
                            ],
                        },
                    )
                ],
            )
        except tk.TclError:
            pass
        style.configure(
            "Dark.Vertical.TScrollbar",
            background="#65736e",
            troughcolor=SURFACE_LOW,
            bordercolor=SURFACE_LOW,
            arrowcolor=MUTED,
            relief="flat",
            width=10,
        )
        style.map("Dark.Vertical.TScrollbar", background=[("active", ACCENT)])

        # search entry --------------------------------------------------
        self.query_var = tk.StringVar(value=self.PLACEHOLDER)
        self.query_entry = tk.Entry(
            self.canvas,
            textvariable=self.query_var,
            bg=FIELD,
            fg=DIM,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Segoe UI", 11),
        )
        self._win_entry = self.canvas.create_window(0, 0, window=self.query_entry, anchor="nw")
        self.query_entry.bind("<Return>", lambda _e: self.do_search())
        self.query_entry.bind("<FocusIn>", self._entry_focus_in)
        self.query_entry.bind("<FocusOut>", self._entry_focus_out)

        # result list ---------------------------------------------------
        self.results_frame = tk.Frame(self.canvas, bg=SURFACE_LOW, bd=0, highlightthickness=0)
        self.results_canvas = tk.Canvas(
            self.results_frame, bg=SURFACE_LOW, highlightthickness=0, bd=0, yscrollincrement=20
        )
        self.results_sb = ttk.Scrollbar(
            self.results_frame, orient="vertical", command=self.results_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.results_canvas.configure(yscrollcommand=self.results_sb.set)
        self.results_canvas.pack(side="left", fill="both", expand=True)
        self.results_sb.pack(side="right", fill="y")
        self._win_results = self.canvas.create_window(0, 0, window=self.results_frame, anchor="nw")
        self.results_canvas.bind("<Configure>", lambda _e: self._schedule_results())
        self.results_canvas.bind("<Button-1>", self._results_click)
        self.results_canvas.bind("<Motion>", self._results_motion)
        self._bind_wheel(self.results_canvas)
        self._res_hits: list[tuple[float, float, float, float, callable]] = []

        # guide list ----------------------------------------------------
        self.guide_frame = tk.Frame(self.canvas, bg=SURFACE_LOW, bd=0, highlightthickness=0)
        self.guide_canvas = tk.Canvas(
            self.guide_frame, bg=SURFACE_LOW, highlightthickness=0, bd=0, yscrollincrement=20
        )
        self.guide_sb = ttk.Scrollbar(
            self.guide_frame, orient="vertical", command=self.guide_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.guide_canvas.configure(yscrollcommand=self.guide_sb.set)
        self.guide_canvas.pack(side="left", fill="both", expand=True)
        self.guide_sb.pack(side="right", fill="y")
        self._win_guide = self.canvas.create_window(0, 0, window=self.guide_frame, anchor="nw")
        self.guide_canvas.bind("<Configure>", self._guide_configure)
        self._bind_wheel(self.guide_canvas)
        self._guide_width = 0

        # status list ---------------------------------------------------
        self.status_frame = tk.Frame(self.canvas, bg=SURFACE_LOW, bd=0, highlightthickness=0)
        self.status_canvas = tk.Canvas(
            self.status_frame, bg=SURFACE_LOW, highlightthickness=0, bd=0, yscrollincrement=20
        )
        self.status_sb = ttk.Scrollbar(
            self.status_frame, orient="vertical", command=self.status_canvas.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.status_canvas.configure(yscrollcommand=self.status_sb.set)
        self.status_canvas.pack(side="left", fill="both", expand=True)
        self.status_sb.pack(side="right", fill="y")
        self._win_status = self.canvas.create_window(0, 0, window=self.status_frame, anchor="nw")
        self.status_canvas.bind("<Configure>", lambda _e: self._schedule_status())
        self._bind_wheel(self.status_canvas)

        # component preview --------------------------------------------
        self.preview_frame = tk.Frame(self.canvas, bg=SURFACE_LOW, bd=0, highlightthickness=0)
        self.preview_title = tk.Label(
            self.preview_frame, text="选择器件", bg=SURFACE_LOW, fg=TEXT,
            font=F_H2, anchor="w",
        )
        self.preview_title.pack(fill="x", padx=8, pady=(8, 2))
        self.preview_details = tk.Label(
            self.preview_frame, text="点击左侧搜索结果查看 LCSC 图片和 PCB 封装",
            bg=SURFACE_LOW, fg=MUTED, font=F_SMALL, anchor="w", justify="left",
            wraplength=380,
        )
        self.preview_details.pack(fill="x", padx=8, pady=(0, 10))

        preview_cards = tk.Frame(self.preview_frame, bg=SURFACE_LOW)
        preview_cards.pack(fill="both", expand=True, padx=4)
        preview_cards.grid_rowconfigure(0, weight=1)
        preview_cards.grid_columnconfigure(0, weight=1, uniform="preview")
        preview_cards.grid_columnconfigure(1, weight=1, uniform="preview")
        self.component_image_label = self._make_preview_card(
            preview_cards, 0, "元件图片", "尚未选择器件"
        )
        self.footprint_image_label = self._make_preview_card(
            preview_cards, 1, "PCB 封装", "尚未选择器件"
        )

        self.preview_hint = tk.Label(
            self.preview_frame, text="图片和封装图由 LCSC 提供",
            bg=SURFACE_LOW, fg=DIM, font=F_SMALL, anchor="w",
        )
        self.preview_hint.pack(fill="x", padx=8, pady=(6, 2))
        tk.Button(
            self.preview_frame, text="在浏览器打开 LCSC 封装图",
            command=self.open_lcsc_footprint, bg=SURFACE_HIGH, fg=ACCENT,
            activebackground=OUTLINE, activeforeground=ACCENT,
            relief="flat", bd=0, font=F_SMALL, cursor="hand2",
        ).pack(anchor="e", padx=8, pady=(2, 8), ipadx=8, ipady=5)
        self._win_preview = self.canvas.create_window(0, 0, window=self.preview_frame, anchor="nw")

        # log -----------------------------------------------------------
        self.log_frame = tk.Frame(self.canvas, bg=SURFACE_LOW, bd=0, highlightthickness=0)
        self.log_text = tk.Text(
            self.log_frame,
            bg=SURFACE_LOW,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Consolas", 9),
            wrap="word",
            padx=10,
            pady=8,
        )
        self.log_sb = ttk.Scrollbar(
            self.log_frame, orient="vertical", command=self.log_text.yview,
            style="Dark.Vertical.TScrollbar",
        )
        self.log_text.configure(yscrollcommand=self.log_sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        self.log_sb.pack(side="right", fill="y")
        self._win_log = self.canvas.create_window(0, 0, window=self.log_frame, anchor="nw")
        self._bind_wheel(self.log_text)

    def _make_preview_card(self, parent, column: int, title: str, placeholder: str):
        card = tk.Frame(
            parent, bg=CARD, highlightthickness=1, highlightbackground=GLASS_EDGE,
        )
        card.grid(row=0, column=column, sticky="nsew", padx=4, pady=2)
        tk.Label(
            card, text=title, bg=CARD, fg=TEXT, font=("Segoe UI", 10, "bold"),
            anchor="w",
        ).pack(fill="x", padx=8, pady=(8, 6))
        image_label = tk.Label(
            card, text=placeholder, bg="#202733", fg="#b4c0d0",
            font=F_SMALL, justify="center", wraplength=140,
            width=18, height=10,
        )
        image_label.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        return image_label

    def _bind_events(self) -> None:
        self.root.bind("<Configure>", self._root_configure)
        self.canvas.bind("<Button-1>", self._main_click)
        self.canvas.bind("<Motion>", self._main_motion)

    def _bind_wheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._on_wheel, add="+")

    def _on_wheel(self, event) -> None:
        widget = event.widget
        try:
            widget.yview_scroll(int(-event.delta / 120) * 3, "units")
        except Exception:
            pass

    # ------------------------------------------------------------------
    # entry placeholder
    # ------------------------------------------------------------------
    def _entry_focus_in(self, _e=None) -> None:
        if self.query_var.get() == self.PLACEHOLDER:
            self.query_var.set("")
            self.query_entry.configure(fg=TEXT)

    def _entry_focus_out(self, _e=None) -> None:
        if not self.query_var.get().strip():
            self.query_var.set(self.PLACEHOLDER)
            self.query_entry.configure(fg=DIM)

    def _query_text(self) -> str:
        value = self.query_var.get().strip()
        return "" if value == self.PLACEHOLDER else value

    # ------------------------------------------------------------------
    # scheduling / resize
    # ------------------------------------------------------------------
    def _root_configure(self, event) -> None:
        if event.widget is not self.root:
            return
        if self._resize_job is None:
            self._resize_job = self.root.after_idle(self._apply_draw)

    def _apply_draw(self) -> None:
        self._resize_job = None
        self._draw()

    def _schedule_results(self) -> None:
        if self._results_job is None:
            self._results_job = self.root.after_idle(self._draw_results_now)

    def _draw_results_now(self) -> None:
        self._results_job = None
        self._draw_results()

    def _schedule_status(self) -> None:
        if self._status_job is None:
            self._status_job = self.root.after_idle(self._draw_status_now)

    def _draw_status_now(self) -> None:
        self._status_job = None
        self._draw_status()

    def _guide_configure(self, _e=None) -> None:
        width = self.guide_canvas.winfo_width()
        if width != self._guide_width:
            self._guide_width = width
            if self._guide_job is None:
                self._guide_job = self.root.after_idle(self._draw_guide_now)

    def _draw_guide_now(self) -> None:
        self._guide_job = None
        self._draw_guide()

    def _first_guide(self) -> None:
        self._draw()
        self._draw_guide()

    # ------------------------------------------------------------------
    # main layout
    # ------------------------------------------------------------------
    def _draw(self) -> None:
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w < 100 or h < 100:
            return

        c = self.canvas
        c.delete("chrome")
        self.hits.clear()

        margin = 24
        # header ---------------------------------------------------------
        c.create_text(margin, 18, text="CadenceLibHub", anchor="nw", font=F_TITLE,
                      fill=TEXT, tags="chrome")
        c.create_text(margin, 56, anchor="nw", tags="chrome",
                      text="元件库管理  ·  LCSC / SamacSys",
                      font=F_SUB, fill=MUTED)

        if self.busy:
            self._draw_progress(w)

        # search card ----------------------------------------------------
        sy = 84
        sh = 68
        round_rect(c, margin, sy, w - margin, sy + sh, 16, fill=PANEL,
                   outline=OUTLINE, tags="chrome")
        c.create_text(margin + 18, sy + sh / 2, text="搜索", anchor="w",
                      font=("Segoe UI", 12, "bold"), fill=TEXT, tags="chrome")

        src_w = 196
        search_w = 96
        btn_x = w - margin - 16 - search_w
        src_x = btn_x - 10 - src_w
        entry_x = margin + 64
        entry_w = max(140, src_x - 10 - entry_x)
        entry_y = sy + 15
        entry_h = 38
        round_rect(c, entry_x, entry_y, entry_x + entry_w, entry_y + entry_h, 10,
                   fill=FIELD, outline="", tags="chrome")

        self._place_window(self._win_entry, entry_x + 9, entry_y + 1, entry_w - 18, entry_h - 2)

        # source chips
        pieces = [("LCSC", 56), ("SamacSys", 82), ("全部", 50)]
        x = src_x
        for name, cw in pieces:
            fill = ACCENT_CONTAINER if name == self.source else SURFACE_HIGH
            round_rect(c, x, sy + 16, x + cw, sy + 52, 18, fill=fill,
                       outline="", tags="chrome")
            c.create_text(x + cw / 2, sy + 34, text=name, font=("Segoe UI", 10),
                          fill=ACCENT if name == self.source else MUTED, tags="chrome")
            self._hit(x, sy + 16, x + cw, sy + 52,
                      lambda n=name: self._set_source(n))
            x += cw + 4

        # search button
        round_rect(c, btn_x, sy + 14, btn_x + search_w, sy + 54, 20,
                   fill=ACCENT, outline="", tags="chrome")
        c.create_text(btn_x + search_w / 2, sy + 34, text="搜索", font=F_BTN,
                      fill=ON_ACCENT, tags="chrome")
        self._hit(btn_x, sy + 14, btn_x + search_w, sy + 54, self.do_search)

        # action buttons -------------------------------------------------
        ay = 160
        ah = 40
        gap = 8
        bw = (w - 2 * margin - 3 * gap) / 4
        actions = [
            ("按编号添加", self.do_add_id, ACCENT, ON_ACCENT),
            ("生成封装", self.do_promote, "#293332", CYAN),
            ("同步下载", self.do_sync, "#353126", AMBER),
            ("检查匹配", self.do_check, "#29362e", GREEN),
        ]
        for i, (label, cmd, color, text_color) in enumerate(actions):
            bx = margin + i * (bw + gap)
            self._button(bx, ay, bw, ah, label, color, cmd, radius=20,
                         text_fill=text_color, outline=OUTLINE if i else "")

        # content panels -------------------------------------------------
        cy = 212
        bottom_h = 40
        ch = h - cy - bottom_h
        if ch < 120:
            return
        right_w = min(600, max(440, int((w - 2 * margin) * 0.42)))
        right_x = w - margin - right_w
        left_x = margin
        left_w = right_x - 12 - left_x
        left_h = ch

        round_rect(c, left_x, cy, left_x + left_w, cy + left_h, 16,
                   fill=PANEL, outline=OUTLINE, tags="chrome")
        round_rect(c, right_x, cy, right_x + right_w, cy + left_h, 16,
                   fill=PANEL, outline=OUTLINE, tags="chrome")
        for px, pw in ((left_x, left_w), (right_x, right_w)):
            c.create_line(px + 20, cy + 1, px + pw - 20, cy + 1,
                          fill=GLASS_EDGE, tags="chrome")
            c.create_line(px + 22, cy + 3, px + pw - 22, cy + 3,
                          fill=GLASS_SHEEN, tags="chrome")

        # left title
        c.create_text(left_x + 16, cy + 14, text="搜索结果", anchor="nw",
                      font=F_H2, fill=TEXT, tags="chrome")
        count = f"{len(self.results)} 条" if self.results else ""
        c.create_text(left_x + left_w - 16, cy + 18, text=count, anchor="ne",
                      font=F_SMALL, fill=MUTED, tags="chrome")

        # right tabs
        tab_specs = [
            ("guide", "操作指引", 80),
            ("status", "运行状态", 80),
            ("preview", "预览", 56),
            ("log", "日志", 56),
        ]
        tab_gap = 4
        total_tw = sum(t[2] for t in tab_specs) + tab_gap * 2
        tx = right_x + right_w - 12 - total_tw
        ty = cy + 12
        for key, label, tw in tab_specs:
            active = key == self.tab
            round_rect(c, tx, ty, tx + tw, ty + 30, 15,
                       fill=ACCENT_CONTAINER if active else SURFACE_HIGH,
                       outline="", tags="chrome")
            c.create_text(tx + tw / 2, ty + 15, text=label, font=("Segoe UI", 10, "bold"),
                          fill=ACCENT if active else MUTED, tags="chrome")
            self._hit(tx, ty, tx + tw, ty + 30, lambda k=key: self._set_tab(k))
            tx += tw + tab_gap

        # child content rects
        content_x = right_x + 12
        content_y = cy + 50
        content_w = right_w - 24
        content_h = left_h - 62
        results_x = left_x + 12
        results_y = cy + 42
        results_w = left_w - 24
        results_h = left_h - 54

        # guide pinned order banner
        banner_h = 38
        self._place_window(self._win_guide, content_x, content_y,
                           content_w, content_h - banner_h - 8)
        banner_y = content_y + content_h - banner_h
        round_rect(c, content_x, banner_y, content_x + content_w, banner_y + banner_h,
                   16, fill=ACCENT_CONTAINER, outline="", tags="chrome")
        c.create_text(content_x + 12, banner_y + banner_h / 2,
                      text=f"按钮顺序：{BUTTON_ORDER}", anchor="w",
                      font=("Segoe UI", 9, "bold"), fill=ACCENT,
                      width=content_w - 24, tags="chrome")

        self._place_window(self._win_results, results_x, results_y, results_w, results_h)
        self._place_window(self._win_status, content_x, content_y, content_w, content_h)
        self._place_window(self._win_preview, content_x, content_y, content_w, content_h)
        self._place_window(self._win_log, content_x, content_y, content_w, content_h)

        # show only active tab
        c.itemconfigure(self._win_guide, state="normal" if self.tab == "guide" else "hidden")
        c.itemconfigure(self._win_status, state="normal" if self.tab == "status" else "hidden")
        c.itemconfigure(self._win_preview, state="normal" if self.tab == "preview" else "hidden")
        c.itemconfigure(self._win_log, state="normal" if self.tab == "log" else "hidden")

        # bottom bar -----------------------------------------------------
        by = h - 34
        c.create_text(margin, by + 10, text=f"Root: {core.ROOT}", anchor="w",
                      font=F_SMALL, fill=DIM, tags="chrome")
        tools = [("启动 Loader", self.do_open_loader, 88),
                 ("打开 MyLib", self.do_open_lib, 84),
                 ("清空日志", self.do_clear_log, 78)]
        group_w = sum(t[2] for t in tools) + 8 * 2 + 12
        gx = w - margin - group_w
        round_rect(c, gx, by - 2, gx + group_w, by + 24, 14,
                   fill=SURFACE_HIGH, outline="", tags="chrome")
        tx = gx + 8
        for label, cmd, tw in tools:
            c.create_text(tx + tw / 2, by + 11, text=label, font=F_SMALL,
                          fill=MUTED, tags="chrome")
            self._hit(tx, by, tx + tw, by + 22, cmd)
            tx += tw + 6

        # keep windows above painted chrome
        for wid in (self._win_entry, self._win_results, self._win_guide,
                    self._win_status, self._win_preview, self._win_log):
            c.tag_raise(wid)

        if self.tab == "status":
            self._schedule_status()

    def _draw_progress(self, w: int) -> None:
        x1 = w - 24 - 150
        y1 = 30
        track = round_rect(self.canvas, x1, y1, x1 + 150, y1 + 8, 4,
                           fill=SURFACE_HIGH, outline="", tags=("chrome", "prog"))
        self._prog = (x1, y1)
        if self._progress_job is None:
            self._animate_progress()

    def _animate_progress(self) -> None:
        self._progress_job = None
        if not self.busy:
            return
        x1, y1 = getattr(self, "_prog", (0, 0))
        if x1:
            self._phase = (self._phase + 0.055) % 1.0
            seg = 56
            x = x1 + (150 - seg) * self._phase
            self.canvas.delete("progfill")
            round_rect(self.canvas, x, y1, x + seg, y1 + 8, 4,
                       fill=ACCENT, outline="", tags=("chrome", "progfill"))
        self._progress_job = self.root.after(55, self._animate_progress)

    def _place_window(self, wid: int, x: int, y: int, w: int, h: int) -> None:
        self.canvas.coords(wid, x, y)
        cached = self._wins.get(wid)
        if cached != (int(w), int(h)):
            self.canvas.itemconfigure(wid, width=int(w), height=int(h))
            self._wins[wid] = (int(w), int(h))

    def _button(self, x, y, w, h, text, fill, command, radius: int = 12,
                text_fill: str = TEXT, outline: str = "") -> None:
        round_rect(self.canvas, x, y, x + w, y + h, radius, fill=fill,
                   outline=outline, tags="chrome")
        self.canvas.create_text(x + w / 2, y + h / 2, text=text, font=F_BTN,
                               fill=text_fill, tags="chrome")
        self._hit(x, y, x + w, y + h, command)

    def _hit(self, x1, y1, x2, y2, callback) -> None:
        self.hits.append((x1, y1, x2, y2, callback))

    def _main_click(self, event) -> None:
        for x1, y1, x2, y2, cb in reversed(self.hits):
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                cb()
                return

    def _main_motion(self, event) -> None:
        for x1, y1, x2, y2, _cb in reversed(self.hits):
            if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                self.canvas.configure(cursor="hand2")
                return
        self.canvas.configure(cursor="")

    # ------------------------------------------------------------------
    # tabs / source
    # ------------------------------------------------------------------
    def _set_source(self, name: str) -> None:
        self.source = name
        self._draw()

    def _set_tab(self, name: str) -> None:
        self.tab = name
        self._draw()
        if name == "guide":
            self._draw_guide()
        elif name == "status":
            self._draw_status()
        elif name == "log":
            self.log_text.see("end")

    # ------------------------------------------------------------------
    # results rendering
    # ------------------------------------------------------------------
    def populate(self, results: list[dict]) -> None:
        self.results = results
        self.preview_request += 1
        self.preview_item = None
        self.component_photo = None
        self.footprint_photo = None
        self.preview_title.configure(text="选择器件")
        self.preview_details.configure(text="点击左侧搜索结果查看 LCSC 图片和 PCB 封装")
        self.component_image_label.configure(image="", text="尚未选择器件")
        self.footprint_image_label.configure(image="", text="尚未选择器件")
        self.preview_hint.configure(text="图片和封装图由 LCSC 提供")
        self.results_canvas.yview_moveto(0)
        self._draw_results()
        self._draw()
        if results:
            self._prefetch_preview(results[0])

    def _prefetch_preview(self, item: dict) -> None:
        component = str(item.get("lcsc", "")).strip().upper()
        if not component:
            return
        self._purge_preview_cache()
        cached = self.preview_cache.get(component, {})
        disk_cached = self._disk_preview_entry(component)
        if "footprint" not in cached and "footprint" not in disk_cached:
            self._request_preview(component, "footprint")
        image_url = item.get("image_url")
        if image_url and "component" not in cached and "component" not in disk_cached:
            self._request_preview(component, "component", image_url)

    def _request_preview(self, component: str, kind: str, image_url: str = "") -> None:
        key = (component, kind)
        if key in self._preview_jobs:
            return
        self._preview_jobs.add(key)

        def worker() -> None:
            try:
                if kind == "footprint":
                    data = load_footprint_preview(component)
                else:
                    data = load_component_preview(image_url)
            except Exception:
                data = None
            try:
                self.root.after(0, self._finish_preview, component, kind, data)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _finish_preview(self, component: str, kind: str, data: bytes | None) -> None:
        self._preview_jobs.discard((component, kind))
        if data:
            self.preview_cache.setdefault(component, {}).update(
                {kind: data, "cached_at": time.time()}
            )
            while len(self.preview_cache) > self.PREVIEW_CACHE_LIMIT:
                oldest = min(self.preview_cache, key=lambda code: self.preview_cache[code]["cached_at"])
                del self.preview_cache[oldest]
            self._save_disk_preview(component, kind, data)
            self._purge_disk_preview_cache()
        selected = str((self.preview_item or {}).get("lcsc", "")).strip().upper()
        if self.preview_item is not None and component == selected:
            self._show_preview_image(kind, data)
            self._preview_pending.discard(kind)
            self._preview_failed |= data is None
            if not self._preview_pending:
                selected_item = self.preview_item or {}
                if self._preview_failed and not selected_item.get("image_url"):
                    hint = "封装已加载 · 该器件暂无商品图片"
                elif self._preview_failed:
                    hint = "部分预览加载失败，请检查网络后重试"
                else:
                    hint = "元件图片和封装已加载"
                self.preview_hint.configure(text=hint)

    def _show_scrollbar(self, bar: ttk.Scrollbar, canvas: tk.Canvas, show: bool) -> None:
        mapped = bool(bar.winfo_ismapped())
        if show and not mapped:
            bar.pack(side="right", fill="y", before=canvas)
        elif not show and mapped:
            bar.pack_forget()

    def _draw_results(self) -> None:
        c = self.results_canvas
        c.delete("all")
        self._res_hits.clear()
        self._res_select_hits.clear()
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 20:
            width = 400
        else:
            width = w

        if not self.results:
            self._show_scrollbar(self.results_sb, c, False)
            c.create_text(width / 2, max(90, h / 2 - 18), text="还没有搜索结果",
                          font=("Segoe UI", 14, "bold"), fill=TEXT)
            c.create_text(width / 2, max(120, h / 2 + 8), text="输入型号或 LCSC 编号开始搜索",
                          font=F_SMALL, fill=DIM)
            c.configure(scrollregion=(0, 0, width, h))
            return

        y = 4
        for r in self.results:
            card_h = 64
            card_w = width - 8
            selected = (
                self.preview_item is not None
                and self.preview_item.get("lcsc") == r.get("lcsc")
            )
            round_rect(c, 4, y, 4 + card_w, y + card_h, 12,
                       fill=ACCENT_CONTAINER if selected else CARD,
                       outline=ACCENT if selected else OUTLINE)
            c.create_oval(14, y + 18, 42, y + 46, fill=ACCENT_CONTAINER, outline="")
            c.create_text(28, y + 32, text=str(r["idx"]), font=F_NUM, fill=ACCENT)
            c.create_text(54, y + 12, text=r["mpn"], anchor="nw",
                          font=("Segoe UI", 12, "bold"), fill=TEXT,
                          width=card_w - 150)
            model = "3D 可用" if str(r.get("model", "")).lower() == "yes" else "3D 无"
            c.create_text(54, y + 36, anchor="nw", font=F_SMALL, fill=MUTED,
                          text=f"LCSC {r['lcsc']}    ·    {r['mfr']}    ·    {model}")
            bx1 = 4 + card_w - 70
            by1 = y + 17
            round_rect(c, bx1, by1, bx1 + 58, by1 + 30, 15, fill=ACCENT, outline="")
            c.create_text(bx1 + 29, by1 + 15, text="添加", font=("Segoe UI", 10, "bold"),
                          fill=ON_ACCENT)
            self._res_hits.append((bx1, by1, bx1 + 58, by1 + 30,
                                   lambda item=r: self.add_result(item)))
            self._res_select_hits.append((4, y, 4 + card_w, y + card_h, r))
            y += card_h + 8

        c.configure(scrollregion=(0, 0, width, y + 6))
        self._show_scrollbar(self.results_sb, c, (y + 6) > h)

    def _results_click(self, event) -> None:
        y = self.results_canvas.canvasy(event.y)
        x = self.results_canvas.canvasx(event.x)
        for x1, y1, x2, y2, cb in self._res_hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                cb()
                return
        for x1, y1, x2, y2, item in self._res_select_hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.select_preview(item)
                return

    def _results_motion(self, event) -> None:
        y = self.results_canvas.canvasy(event.y)
        x = self.results_canvas.canvasx(event.x)
        for x1, y1, x2, y2, _cb in self._res_hits:
            if x1 <= x <= x2 and y1 <= y <= y2:
                self.results_canvas.configure(cursor="hand2")
                return
        self.results_canvas.configure(cursor="")

    def _purge_preview_cache(self) -> None:
        now = time.time()
        expired = [
            component
            for component, entry in self.preview_cache.items()
            if not isinstance(entry.get("cached_at"), (int, float))
            or now - entry["cached_at"] >= self.PREVIEW_CACHE_TTL
        ]
        for component in expired:
            self.preview_cache.pop(component, None)

    def _disk_preview_entry(self, component: str) -> dict[str, bytes]:
        folder = self.PREVIEW_CACHE_DIR / component
        if not folder.is_dir():
            return {}
        try:
            if time.time() - folder.stat().st_mtime >= self.PREVIEW_CACHE_TTL:
                return {}
            values: dict[str, bytes] = {}
            for kind in ("component", "footprint"):
                path = folder / f"{kind}.png"
                if path.exists():
                    values[kind] = path.read_bytes()
            return values
        except OSError:
            return {}

    def _save_disk_preview(self, component: str, kind: str, data: bytes) -> None:
        try:
            folder = self.PREVIEW_CACHE_DIR / component
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{kind}.png").write_bytes(data)
        except OSError:
            pass

    def _purge_disk_preview_cache(self) -> None:
        try:
            now = time.time()
            folders = [p for p in self.PREVIEW_CACHE_DIR.iterdir() if p.is_dir()]
            for folder in folders:
                if now - folder.stat().st_mtime >= self.PREVIEW_CACHE_TTL:
                    shutil.rmtree(folder, ignore_errors=True)
            folders = sorted(
                [p for p in self.PREVIEW_CACHE_DIR.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
            )
            for folder in folders[:-self.PREVIEW_CACHE_LIMIT]:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass

    def select_preview(self, item: dict) -> None:
        self._purge_preview_cache()
        self.preview_item = item
        self.preview_request += 1
        component = str(item.get("lcsc", "")).strip().upper()
        cached = self.preview_cache.get(component, {})
        if component and ("footprint" not in cached or "component" not in cached):
            cached = {**self._disk_preview_entry(component), **cached}
        self.component_photo = None
        self.footprint_photo = None
        self.preview_title.configure(text=item.get("mpn") or "LCSC 器件")
        details = [f"LCSC {item.get('lcsc', '')}", item.get("mfr", "")]
        if item.get("footprint_name"):
            details.append(item["footprint_name"])
        self.preview_details.configure(text="  ·  ".join(part for part in details if part))
        self.component_image_label.configure(image="", text="正在加载图片…")
        self.footprint_image_label.configure(image="", text="正在加载封装…")
        self._preview_pending.clear()
        self._preview_failed = False
        image_url = item.get("image_url")
        for kind in ("component", "footprint"):
            if kind in cached:
                self._show_preview_image(kind, cached[kind])
            elif not component or (kind == "component" and not image_url):
                missing_text = (
                    "该器件暂无商品图片" if kind == "component" and not image_url
                    else None
                )
                self._show_preview_image(kind, None, missing_text)
                self._preview_failed = True
            else:
                self._preview_pending.add(kind)
                self._request_preview(component, kind, image_url or "")
        if self._preview_pending:
            hint = "正在加载预览…"
        elif self._preview_failed:
            hint = "部分预览不可用"
        else:
            hint = "预览已从缓存载入"
        self.preview_hint.configure(text=hint)
        self._draw_results()
        self._set_tab("preview")

    def _show_preview_image(
        self, kind: str, data: bytes | None, unavailable_text: str | None = None
    ) -> None:
        if kind == "component":
            label = self.component_image_label
            unavailable = "元件图片不可用"
        else:
            label = self.footprint_image_label
            unavailable = "封装图不可用"
        if not data:
            label.configure(image="", text=unavailable_text or unavailable)
            return

        image = Image.open(io.BytesIO(data)).convert("RGBA")
        image.thumbnail((176, 150), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(image)
        if kind == "component":
            self.component_photo = photo
        else:
            self.footprint_photo = photo
        label.configure(image=photo, text="")

    def open_lcsc_footprint(self) -> None:
        if self.preview_item is None:
            return
        component = str(self.preview_item.get("lcsc", ""))
        if not component:
            return
        request_id = self.preview_request
        self.preview_hint.configure(text="正在打开立创商城商品页…")

        def worker() -> None:
            try:
                url = core.lcsc_product_url(component)
                error = ""
            except Exception as exc:
                url = ""
                error = str(exc)

            def finish() -> None:
                if request_id != self.preview_request:
                    return
                if url:
                    webbrowser.open(url)
                    self.preview_hint.configure(text="已打开立创商城国内站商品详情")
                else:
                    self.preview_hint.configure(text=f"无法打开商品页：{error[:120]}")

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # guide / status rendering
    # ------------------------------------------------------------------
    def _draw_guide(self) -> None:
        c = self.guide_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 60:
            return
        y = 6
        text_w = w - 72

        def section(title: str) -> None:
            nonlocal y
            y += 8
            c.create_text(8, y, text=title, anchor="nw", font=F_SECTION, fill=TEXT)
            y += 30

        def step(index: int, title: str, detail: str) -> None:
            nonlocal y
            title_id = c.create_text(56, y + 9, text=title, anchor="nw",
                                     font=F_CARD, fill=TEXT)
            detail_id = c.create_text(56, y + 30, text=detail, anchor="nw",
                                      font=F_BODY, fill=MUTED, width=text_w,
                                      justify="left")
            bbox = c.bbox(detail_id) or (0, 0, 0, y + 50)
            detail_h = bbox[3] - bbox[1]
            card_h = max(54, 30 + detail_h + 10)
            bg = round_rect(c, 2, y, w - 6, y + card_h, 12,
                            fill=CARD, outline=OUTLINE)
            c.tag_lower(bg)
            cy = y + card_h / 2
            c.create_oval(14, cy - 13, 40, cy + 13, fill=ACCENT_CONTAINER, outline="")
            c.create_text(27, cy, text=str(index), font=F_NUM, fill=ACCENT)
            y += card_h + 6

        section("LCSC 流程（推荐）")
        for i, (t, d) in enumerate(LCSC_STEPS, 1):
            step(i, t, d)

        section("SamacSys 流程")
        for i, (t, d) in enumerate(SAMACSYS_STEPS, 1):
            step(i, t, d)

        c.configure(scrollregion=(0, 0, w, y + 6))
        self._show_scrollbar(self.guide_sb, c, (y + 6) > h)

    def _draw_status(self) -> None:
        c = self.status_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 40:
            return
        if not self.status_rows:
            self._show_scrollbar(self.status_sb, c, False)
            c.create_text(w / 2, 60, text="运行操作后，这里显示每一步的状态。",
                          font=F_SMALL, fill=DIM)
            c.configure(scrollregion=(0, 0, w, 120))
            return

        colors = {"running": CYAN, "done": GREEN, "error": ERROR}
        labels = {"running": "进行中", "done": "完成", "error": "失败"}
        y = 4
        for row in self.status_rows:
            state = row["state"]
            text_id = c.create_text(14, y + 10, text=row["text"], anchor="nw",
                                    font=("Segoe UI", 10), fill=TEXT, width=w - 130)
            bbox = c.bbox(text_id) or (0, 0, 0, y + 24)
            row_h = max(44, bbox[3] - bbox[1] + 20)
            c.delete(text_id)
            round_rect(c, 2, y, w - 6, y + row_h, 12,
                       fill=SURFACE_LOW, outline=OUTLINE)
            c.create_text(14, y + row_h / 2, text=row["text"], anchor="w",
                          font=("Segoe UI", 10), fill=TEXT, width=w - 130)
            c.create_text(w - 16, y + row_h / 2, text=labels.get(state, state), anchor="e",
                          font=("Segoe UI", 10, "bold"), fill=colors.get(state, MUTED))
            y += row_h + 6
        c.configure(scrollregion=(0, 0, w, y + 4))
        self._show_scrollbar(self.status_sb, c, (y + 4) > h)

    # ------------------------------------------------------------------
    # status / log / progress
    # ------------------------------------------------------------------
    def clear_status(self) -> None:
        self.status_rows = []
        if self.tab == "status":
            self._draw_status()

    def set_status(self, text: str, state: str = "running") -> None:
        def apply() -> None:
            for row in self.status_rows:
                if row["text"] == text:
                    row["state"] = state
                    break
            else:
                self.status_rows.append({"text": text, "state": state})
            if self.tab == "status":
                self._draw_status()
                self.status_canvas.yview_moveto(1.0)

        self.root.after(0, apply)

    def log(self, text: str) -> None:
        if not text:
            return
        self.log_text.insert("end", text + "\n")
        self.log_text.see("end")

    def _start_busy(self) -> None:
        self.busy = True
        self._phase = 0.0
        self._draw()

    def _stop_busy(self) -> None:
        self.busy = False
        if self._progress_job is not None:
            self.root.after_cancel(self._progress_job)
            self._progress_job = None
        self.canvas.delete("prog")
        self.canvas.delete("progfill")
        self._draw()

    # ------------------------------------------------------------------
    # background work
    # ------------------------------------------------------------------
    def run_bg(self, fn) -> None:
        if self.busy:
            return
        self.clear_status()
        self._set_tab("status")
        core.set_progress_callback(self.set_status)
        self._start_busy()

        def worker() -> None:
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                    fn()
            except Exception as exc:
                buf.write(f"\n[error] {exc}\n")
            text = buf.getvalue().strip()

            def finish() -> None:
                core.set_progress_callback(None)
                self._stop_busy()
                self.log(text)

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # search / actions
    # ------------------------------------------------------------------
    def do_search(self) -> None:
        if self.busy:
            return
        kw = self._query_text()
        if not kw:
            self.query_entry.focus_set()
            return
        label = self.source
        src = {"LCSC": "lcsc", "SamacSys": "samacsys", "全部": "all"}.get(label, "lcsc")
        self.clear_status()
        self._set_tab("status")
        core.set_progress_callback(self.set_status)
        self.log(f"搜索 [{label}]: {kw}")

        if src in ("samacsys", "all"):
            core.search_samacsys(kw)

        if src in ("lcsc", "all"):
            self._start_busy()

            def worker() -> None:
                code, out, results = core.lcsc_search(kw, 30)

                def finish() -> None:
                    core.set_progress_callback(None)
                    self._stop_busy()
                    self.populate(results)
                    if results:
                        self.log(f"{len(results)} 条结果。")
                    else:
                        self.log(out or "(没有结果)")
                    if code != 0:
                        self.log(f"[error] LCSC search exited with {code}")

                self.root.after(0, finish)

            threading.Thread(target=worker, daemon=True).start()
        else:
            core.set_progress_callback(None)

    def add_result(self, item: dict) -> None:
        if self.busy:
            return
        dialog = InputDialog(f"{item['mpn']}\n库名（留空使用 {item['lcsc']}）：", "添加到库")
        name = dialog.get_input()
        if name is None:
            return
        name = name.strip() or item["lcsc"]
        self.run_bg(lambda: core.export_lcsc(item["lcsc"], name, stage_only=False))

    def do_add_id(self) -> None:
        if self.busy:
            return
        dialog = InputDialog("输入立创编号，例如 C165948：", "按编号添加 LCSC 器件")
        cid = dialog.get_input()
        if not cid or not cid.strip():
            return
        cid = cid.strip()
        dialog2 = InputDialog("库名（留空使用编号）：", "库名")
        name = dialog2.get_input()
        if name is None:
            return
        self.run_bg(lambda: core.export_lcsc(cid, name.strip() or cid, stage_only=False))

    def do_promote(self) -> None:
        if self.busy:
            return
        dialog = InputDialog("输入库名（例如 C165948 或 TypeC16P）：", "生成封装并归档")
        name = dialog.get_input()
        if not name or not name.strip():
            return
        self.run_bg(lambda: core.promote_stage(name.strip(), clean=True))

    def do_sync(self) -> None:
        self.run_bg(lambda: core.cmd_sync(argparse.Namespace(what="all", days=7, move=False)))

    def do_check(self) -> None:
        self.run_bg(lambda: core.cmd_check(argparse.Namespace()))

    def do_open_loader(self) -> None:
        core.cmd_open_loader(argparse.Namespace())

    def do_open_lib(self) -> None:
        os.startfile(str(core.ROOT))

    def do_clear_log(self) -> None:
        self.log_text.delete("1.0", "end")

    # ------------------------------------------------------------------
    def run(self) -> int:
        self.root.mainloop()
        return 0


def run() -> int:
    return App().run()
