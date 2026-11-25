"""Modern customtkinter UI for CadenceLibHub."""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import threading

import customtkinter as ctk

import cadence_lib as core

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

LCSC_STEPS = [
    ("搜索器件", "输入型号或 C 编号（例如 C165948），来源选 LCSC，点“搜索”。"),
    (
        "添加到库",
        "在结果卡片点“添加”；已知编号可直接点“按编号添加”。",
    ),
    (
        "Capture 导入",
        "File → Import → Library XML\n"
        "XML：_staging\\<库名>\\<库名>.xml\n"
        "OLB：Symbols\\<库名>.olb",
    ),
    (
        "Allegro 生成",
        'Allegro 命令窗口执行：\nskill load("<library-root>/_staging/<库名>/<库名>_allegro/<库名>.il")',
    ),
    (
        "生成封装",
        "回到本工具点“生成封装”，输入库名，自动复制到 Footprints。",
    ),
    (
        "检查匹配",
        "点“检查匹配”，确认符号 / 封装 / 3D 全部 yes。",
    ),
]

SAMACSYS_STEPS = [
    ("搜索下载", "来源选 SamacSys，点“搜索”，在网页或 Library Loader 中登录并下载。"),
    ("同步下载", "下载完成后点“同步下载”，自动归档到 MyLib。"),
    ("检查匹配", "点“检查匹配”，确认符号 / 封装 / 3D 全部 yes。"),
]

BUTTON_ORDER = "搜索  →  添加  →  Capture 导入  →  Allegro 生成  →  生成封装  →  检查匹配"


class InputDialog(ctk.CTkToplevel):
    """Small rounded prompt with Chinese buttons."""

    def __init__(self, text: str, title: str = "输入") -> None:
        super().__init__()
        self.title(title)
        self.geometry("400x210")
        self.resizable(False, False)
        self.result: str | None = None

        ctk.CTkLabel(
            self,
            text=text,
            font=("Segoe UI", 12),
            wraplength=340,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=24, pady=(22, 10))

        self.entry = ctk.CTkEntry(
            self,
            corner_radius=10,
            height=38,
            font=("Segoe UI", 12),
        )
        self.entry.pack(fill="x", padx=24)
        self.entry.bind("<Return>", lambda _e: self._ok())
        self.bind("<Escape>", lambda _e: self._cancel())

        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(fill="x", padx=24, pady=(18, 16))
        ctk.CTkButton(
            buttons,
            text="取消",
            command=self._cancel,
            height=34,
            corner_radius=10,
            fg_color="#3a4350",
            hover_color="#2b323c",
            font=("Segoe UI", 12),
        ).pack(side="left", expand=True, fill="x", padx=(0, 6))
        ctk.CTkButton(
            buttons,
            text="确定",
            command=self._ok,
            height=34,
            corner_radius=10,
            font=("Segoe UI", 12, "bold"),
        ).pack(side="left", expand=True, fill="x", padx=(6, 0))

        self.after(10, self._center)
        self.entry.focus_set()

    def _center(self) -> None:
        self.update_idletasks()
        try:
            self.transient(self.master)
        except Exception:
            pass
        master = self.master
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.lift()
        self.focus_force()
        try:
            self.grab_set()
        except Exception:
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
    def __init__(self) -> None:
        self.root = ctk.CTk()
        self.root.title("CadenceLibHub")
        self.root.geometry("1280x820")
        self.root.minsize(1120, 700)

        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(3, weight=1)

        self.results: list[dict] = []
        self.status_rows: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel]] = {}

        self._build_header()
        self._build_search()
        self._build_actions()
        self._build_content()
        self._build_statusbar()
        self.populate([])

        self.query.focus_set()

    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 8))
        header.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(
            header,
            text="CadenceLibHub",
            font=("Segoe UI", 28, "bold"),
        ).grid(row=0, column=0, sticky="w")

        ctk.CTkLabel(
            header,
            text="LCSC 推荐顺序：搜索 → 添加 → Capture 导入 → Allegro 生成 → 生成封装 → 检查匹配",
            font=("Segoe UI", 12),
            text_color=("#9aa4b2", "#8b95a5"),
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 0))

        self.progress = ctk.CTkProgressBar(
            header,
            mode="indeterminate",
            width=150,
            height=8,
            corner_radius=4,
        )
        self.progress.grid(row=0, column=2, sticky="e")
        self.progress.set(0)
        self.progress.grid_remove()

    def _build_search(self) -> None:
        card = ctk.CTkFrame(self.root, corner_radius=18, fg_color=("#20242b", "#171a20"))
        card.grid(row=1, column=0, sticky="ew", padx=24, pady=(4, 8))
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="搜索", font=("Segoe UI", 13, "bold")).grid(
            row=0, column=0, padx=(18, 10), pady=14
        )
        self.query = ctk.CTkEntry(
            card,
            placeholder_text="输入型号或 LCSC 编号，例如 C165948 / TYPE-C 16P",
            corner_radius=12,
            height=40,
            font=("Segoe UI", 13),
        )
        self.query.grid(row=0, column=1, sticky="ew", pady=14)
        self.query.bind("<Return>", lambda e: self.do_search())

        self.source = ctk.CTkSegmentedButton(
            card,
            values=["LCSC", "SamacSys", "全部"],
            corner_radius=10,
            height=36,
            font=("Segoe UI", 12),
        )
        self.source.set("LCSC")
        self.source.grid(row=0, column=2, padx=10)

        self.search_btn = ctk.CTkButton(
            card,
            text="搜索",
            corner_radius=12,
            height=40,
            width=96,
            font=("Segoe UI", 13, "bold"),
            command=self.do_search,
        )
        self.search_btn.grid(row=0, column=3, padx=(0, 16))

    def _build_actions(self) -> None:
        card = ctk.CTkFrame(self.root, corner_radius=18, fg_color="transparent")
        card.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 8))

        actions = [
            ("按编号添加", self.do_add_id, "#2563eb", "#1d4ed8"),
            ("生成封装", self.do_promote, "#0891b2", "#0e7490"),
            ("同步下载", self.do_sync, "#d97706", "#b45309"),
            ("检查匹配", self.do_check, "#059669", "#047857"),
        ]
        for i, (text, cmd, color, hover) in enumerate(actions):
            btn = ctk.CTkButton(
                card,
                text=text,
                command=cmd,
                corner_radius=12,
                height=40,
                font=("Segoe UI", 12, "bold"),
                fg_color=color,
                hover_color=hover,
            )
            btn.grid(row=0, column=i, sticky="ew", padx=4, pady=1)
        for c in range(4):
            card.grid_columnconfigure(c, weight=1)

    def _build_content(self) -> None:
        content = ctk.CTkFrame(self.root, fg_color="transparent")
        content.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 6))
        self.content = content
        # Keep the guide column at a fixed width: reflowing dozens of rounded
        # cards on every resize is what made window dragging feel sluggish.
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0, minsize=580)
        content.grid_rowconfigure(0, weight=1)

        # ---- results card ----
        left = ctk.CTkFrame(content, corner_radius=18, fg_color=("#20242b", "#171a20"))
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.left_panel = left
        left.grid_rowconfigure(1, weight=1)
        left.grid_columnconfigure(0, weight=1)

        title_row = ctk.CTkFrame(left, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 6))
        ctk.CTkLabel(
            title_row,
            text="搜索结果",
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left")
        self.count_label = ctk.CTkLabel(
            title_row,
            text="",
            font=("Segoe UI", 11),
            text_color=("#9aa4b2", "#8b95a5"),
        )
        self.count_label.pack(side="right")

        self.results_box = ctk.CTkScrollableFrame(
            left,
            corner_radius=12,
            fg_color=("#15181d", "#111318"),
            scrollbar_button_color=("#8b95a5", "#5b6472"),
            scrollbar_button_hover_color=("#6b7483", "#8b95a5"),
        )
        self.results_box.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))

        # ---- guide / status / log ----
        right = ctk.CTkFrame(content, corner_radius=18, fg_color=("#20242b", "#171a20"))
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.right_panel = right
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        self.tabs = ctk.CTkTabview(
            right,
            corner_radius=14,
            segmented_button_selected_color="#2563eb",
            segmented_button_selected_hover_color="#1d4ed8",
            segmented_button_unselected_color=("#2a2f37", "#22262d"),
            segmented_button_font=("Segoe UI", 12, "bold"),
        )
        self.tabs.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.tab_guide = self.tabs.add("操作指引")
        self.tab_status = self.tabs.add("运行状态")
        self.tab_log = self.tabs.add("日志")
        self.tabs.set("操作指引")

        self.guide_box = ctk.CTkScrollableFrame(
            self.tab_guide,
            corner_radius=12,
            fg_color=("#15181d", "#111318"),
            scrollbar_button_color=("#8b95a5", "#5b6472"),
            scrollbar_button_hover_color=("#6b7483", "#8b95a5"),
        )
        self.guide_box.pack(fill="both", expand=True, padx=6, pady=(6, 4))
        self._build_guide(self.guide_box)

        order_card = ctk.CTkFrame(
            self.tab_guide,
            corner_radius=10,
            fg_color=("#1e3a5f", "#172c4a"),
        )
        order_card.pack(fill="x", padx=6, pady=(0, 6))
        order_label = ctk.CTkLabel(
            order_card,
            text=f"按钮顺序：{BUTTON_ORDER}",
            font=("Segoe UI", 11, "bold"),
            text_color=("#dbeafe", "#bfdbfe"),
            anchor="w",
            justify="left",
            wraplength=530,
        )
        order_label.pack(fill="x", padx=12, pady=9)

        self.status_box = ctk.CTkScrollableFrame(
            self.tab_status,
            corner_radius=12,
            fg_color=("#15181d", "#111318"),
            scrollbar_button_color=("#8b95a5", "#5b6472"),
            scrollbar_button_hover_color=("#6b7483", "#8b95a5"),
        )
        self.status_box.pack(fill="both", expand=True, padx=6, pady=6)

        self.log_box = ctk.CTkTextbox(
            self.tab_log,
            corner_radius=12,
            font=("Consolas", 11),
            wrap="word",
        )
        self.log_box.pack(fill="both", expand=True, padx=6, pady=6)

    # ------------------------------------------------------------------
    def _build_guide(self, parent) -> None:
        self._guide_section(parent, "LCSC 流程（推荐）")
        for index, (title, detail) in enumerate(LCSC_STEPS, 1):
            self._guide_step(parent, index, title, detail)

        self._guide_section(parent, "SamacSys 流程")
        for index, (title, detail) in enumerate(SAMACSYS_STEPS, 1):
            self._guide_step(parent, index, title, detail)

    def _guide_section(self, parent, title: str) -> None:
        ctk.CTkLabel(
            parent,
            text=title,
            font=("Segoe UI", 13, "bold"),
            anchor="w",
        ).pack(fill="x", padx=6, pady=(12, 4))

    def _guide_step(self, parent, index: int, title: str, detail: str) -> None:
        card = ctk.CTkFrame(parent, corner_radius=10, fg_color=("#262b33", "#1d2128"))
        card.pack(fill="x", padx=2, pady=3)

        ctk.CTkLabel(
            card,
            text=str(index),
            width=26,
            height=26,
            corner_radius=13,
            fg_color="#2563eb",
            font=("Segoe UI", 11, "bold"),
        ).pack(side="left", padx=(10, 10))

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(side="left", fill="x", expand=True, padx=(0, 12), pady=8)

        ctk.CTkLabel(
            body,
            text=title,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).pack(fill="x")

        detail_label = ctk.CTkLabel(
            body,
            text=detail,
            font=("Segoe UI", 11),
            text_color=("#9aa4b2", "#8b95a5"),
            anchor="w",
            justify="left",
            wraplength=410,
        )
        detail_label.pack(fill="x", pady=(2, 0))

    def _build_statusbar(self) -> None:
        bar = ctk.CTkFrame(self.root, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 10))
        ctk.CTkLabel(
            bar,
            text=f"Root: {core.ROOT}",
            font=("Segoe UI", 10),
            text_color=("#7c8796", "#6b7483"),
        ).pack(side="left")

        tools = ctk.CTkFrame(bar, corner_radius=12, fg_color=("#e9edf2", "#1b1e24"))
        tools.pack(side="right")
        for text, cmd in [
            ("启动 Loader", self.do_open_loader),
            ("打开 MyLib", self.do_open_lib),
            ("清空日志", self.do_clear_log),
        ]:
            ctk.CTkButton(
                tools,
                text=text,
                command=cmd,
                width=80,
                height=24,
                corner_radius=8,
                fg_color="transparent",
                hover_color=("#d9dfe7", "#2b313a"),
                text_color=("#5f6b7a", "#9aa4b2"),
                font=("Segoe UI", 10),
            ).pack(side="left", padx=2, pady=3)

    # ------------------------------------------------------------------
    def log(self, text: str) -> None:
        if not text:
            return
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")

    def clear_status(self) -> None:
        for row in self.status_box.winfo_children():
            row.destroy()
        self.status_rows.clear()

    def set_status(self, text: str, state: str = "running") -> None:
        labels = {"running": "进行中", "done": "完成", "error": "失败"}
        colors = {"running": "#60a5fa", "done": "#34d399", "error": "#f87171"}

        def apply() -> None:
            label = labels.get(state, state)
            color = colors.get(state, "#9aa4b2")
            if text in self.status_rows:
                frame, status_label = self.status_rows[text]
                status_label.configure(text=label, text_color=color)
                return
            frame = ctk.CTkFrame(
                self.status_box,
                corner_radius=8,
                fg_color=("#22262d", "#1b1e24"),
            )
            frame.pack(fill="x", padx=4, pady=4)
            ctk.CTkLabel(
                frame,
                text=text,
                anchor="w",
                font=("Segoe UI", 12),
            ).pack(side="left", padx=10, pady=7)
            status_label = ctk.CTkLabel(
                frame,
                text=label,
                text_color=color,
                font=("Segoe UI", 12, "bold"),
            )
            status_label.pack(side="right", padx=10)
            self.status_rows[text] = (frame, status_label)

        self.root.after(0, apply)

    def run_bg(self, fn) -> None:
        self.clear_status()
        self.tabs.set("运行状态")
        core.set_progress_callback(self.set_status)
        self.progress.grid()
        self.progress.start()

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
                self.progress.stop()
                self.progress.grid_remove()
                self.log(text)

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def populate(self, results: list[dict]) -> None:
        for child in self.results_box.winfo_children():
            child.destroy()
        self.results = results
        self.count_label.configure(text=f"{len(results)} 条" if results else "")

        if not results:
            empty = ctk.CTkFrame(self.results_box, fg_color="transparent")
            empty.pack(pady=90)
            ctk.CTkLabel(
                empty,
                text="还没有搜索结果",
                text_color=("#c3cad4", "#aeb7c4"),
                font=("Segoe UI", 15, "bold"),
            ).pack()
            ctk.CTkLabel(
                empty,
                text="输入型号或 LCSC 编号，点右上角“搜索”",
                text_color=("#7c8796", "#6b7483"),
                font=("Segoe UI", 11),
            ).pack(pady=(6, 0))
            return

        for r in results:
            self._add_result_card(r)
        self.results_box._parent_canvas.yview_moveto(0)

    def _add_result_card(self, r: dict) -> None:
        row = ctk.CTkFrame(
            self.results_box,
            corner_radius=12,
            fg_color=("#262b33", "#1d2128"),
        )
        row.pack(fill="x", padx=4, pady=4)
        row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            row,
            text=str(r["idx"]),
            width=28,
            height=28,
            corner_radius=14,
            fg_color=("#1e3a5f", "#172c4a"),
            text_color="#93c5fd",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(12, 10), pady=12)

        ctk.CTkLabel(
            row,
            text=r["mpn"],
            anchor="w",
            font=("Segoe UI", 13, "bold"),
        ).grid(row=0, column=1, sticky="ew", pady=(11, 0))

        model = "3D 可用" if str(r.get("model", "")).lower() == "yes" else "3D 无"
        meta = f"LCSC {r['lcsc']}    ·    {r['mfr']}    ·    {model}"
        ctk.CTkLabel(
            row,
            text=meta,
            anchor="w",
            font=("Segoe UI", 10),
            text_color=("#9aa4b2", "#8b95a5"),
        ).grid(row=1, column=1, sticky="ew", pady=(2, 11))

        ctk.CTkButton(
            row,
            text="添加",
            width=58,
            height=30,
            corner_radius=9,
            font=("Segoe UI", 11, "bold"),
            command=lambda item=r: self.add_result(item),
        ).grid(row=0, column=2, rowspan=2, padx=(10, 12))

    # ------------------------------------------------------------------
    def do_search(self) -> None:
        kw = self.query.get().strip()
        if not kw:
            return
        label = self.source.get()
        src = {"LCSC": "lcsc", "SamacSys": "samacsys", "全部": "all"}.get(label, "lcsc")
        self.clear_status()
        self.tabs.set("运行状态")
        core.set_progress_callback(self.set_status)
        self.progress.grid()
        self.progress.start()
        self.log(f"搜索 [{label}]: {kw}")

        if src in ("samacsys", "all"):
            core.search_samacsys(kw)

        if src in ("lcsc", "all"):

            def worker() -> None:
                code, out, results = core.lcsc_search(kw, 30)

                def finish() -> None:
                    core.set_progress_callback(None)
                    self.progress.stop()
                    self.progress.grid_remove()
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
            self.progress.stop()
            self.progress.grid_remove()

    def add_result(self, item: dict) -> None:
        dialog = InputDialog(
            text=f"{item['mpn']}\n库名（留空使用 {item['lcsc']}）：",
            title="添加到库",
        )
        name = dialog.get_input()
        if name is None:
            return
        name = name.strip() or item["lcsc"]
        self.run_bg(lambda: core.export_lcsc(item["lcsc"], name, stage_only=False))

    def do_add_id(self) -> None:
        dialog = InputDialog(
            text="输入立创编号，例如 C165948：",
            title="按编号添加 LCSC 器件",
        )
        cid = dialog.get_input()
        if not cid:
            return
        dialog2 = InputDialog(
            text="库名（留空使用编号）：",
            title="库名",
        )
        name = dialog2.get_input()
        if name is None:
            return
        self.run_bg(
            lambda: core.export_lcsc(cid.strip(), (name.strip() or cid.strip()), stage_only=False)
        )

    def do_promote(self) -> None:
        dialog = InputDialog(
            text="输入库名（例如 C165948 或 TypeC16P）：",
            title="生成封装并归档",
        )
        name = dialog.get_input()
        if not name:
            return
        self.run_bg(lambda: core.promote_stage(name.strip(), clean=True))

    def do_sync(self) -> None:
        self.run_bg(
            lambda: core.cmd_sync(argparse.Namespace(what="all", days=7, move=False))
        )

    def do_check(self) -> None:
        self.run_bg(lambda: core.cmd_check(argparse.Namespace()))

    def do_open_loader(self) -> None:
        core.cmd_open_loader(argparse.Namespace())

    def do_open_lib(self) -> None:
        os.startfile(str(core.ROOT))

    def do_clear_log(self) -> None:
        self.log_box.delete("1.0", "end")

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run() -> int:
    return App().run()
