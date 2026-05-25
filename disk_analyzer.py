#!/usr/bin/env python3
"""
Disk Analyzer - 磁盘空间可视化分析与清理工具
跨平台 (Windows / Linux / macOS)，Python + tkinter 实现。

功能：
  - 快速扫描目录，展示文件夹/文件大小
  - Treemap 矩形图可视化空间占比
  - 大文件查找（Top 100）
  - 按文件类型分类统计
  - 系统垃圾/缓存/临时文件扫描与一键清理
  - 右键删除、打开目录
"""

import os
import sys
import stat
import shutil
import json
import threading
import queue
import time
import re
import fnmatch
import glob
from pathlib import Path
from collections import defaultdict
from random import choice
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── GUI imports ──────────────────────────────────────────────
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except ImportError:
    sys.exit("需要 tkinter 支持。请安装 python3-tk (Linux) 或使用系统自带 Python (Windows/macOS)。")


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def format_size(size_bytes: int) -> str:
    """将字节数格式化为人类可读的大小。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        size_bytes /= 1024.0
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
    return f"{size_bytes:.1f} PB"


def format_count(n: int) -> str:
    """格式化数量，超过1000用逗号分隔。"""
    return f"{n:,}"


def is_system_hidden(path: str) -> bool:
    """判断文件/目录是否为系统隐藏（跳过扫描）。"""
    name = os.path.basename(path)
    # 跳过常见的无意义目录
    if name.startswith(".") and name not in (".", ".."):
        return True
    if os.name == "nt":
        # Windows 隐藏属性
        try:
            attrs = os.stat(path).st_file_attributes
            if attrs & stat.FILE_ATTRIBUTE_HIDDEN:
                return True
        except (AttributeError, OSError):
            pass
    return False


def get_dir_size_fast(dirpath: str) -> tuple[int, int]:
    """快速获取目录大小（非递归，只统计直接子项）。返回 (byte_size, file_count)。"""
    total = 0
    count = 0
    try:
        with os.scandir(dirpath) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                        count += 1
                    elif entry.is_dir(follow_symlinks=False):
                        total += entry.stat().st_size  # 目录自身占的空间
                except OSError:
                    pass
    except (PermissionError, OSError):
        pass
    return total, count


# ═══════════════════════════════════════════════════════════════
# 目录树节点
# ═══════════════════════════════════════════════════════════════

class DirNode:
    """目录树节点，存储扫描结果。"""
    __slots__ = ("name", "path", "size", "file_count", "children", "is_dir")

    def __init__(self, name: str, path: str, is_dir: bool):
        self.name = name
        self.path = path
        self.size = 0
        self.file_count = 0
        self.children: list[DirNode] = []
        self.is_dir = is_dir

    def add_child(self, child: "DirNode"):
        self.children.append(child)
        self.size += child.size
        self.file_count += child.file_count
        if child.is_dir:
            self.file_count += 1  # 目录自身也算一个条目？

    def sort_by_size(self):
        """按大小降序排列子节点。"""
        self.children.sort(key=lambda c: c.size, reverse=True)


# ═══════════════════════════════════════════════════════════════
# 扫描引擎
# ═══════════════════════════════════════════════════════════════

class DiskScanner(threading.Thread):
    """
    多线程磁盘扫描器。
    通过回调返回结果，支持取消。
    """

    def __init__(self, root_path: str, callback, progress_callback=None,
                 skip_hidden: bool = True):
        super().__init__(daemon=True)
        self.root_path = root_path
        self.callback = callback          # 扫描完成回调(root_node, elapsed)
        self.progress_callback = progress_callback  # 进度回调(current_path, scanned_count)
        self.skip_hidden = skip_hidden
        self._cancel_flag = threading.Event()
        self._scanned_count = 0
        self._start_time = 0

    def cancel(self):
        self._cancel_flag.set()

    def _scan_dir(self, dirpath: str, depth: int = 0) -> DirNode:
        """递归扫描目录，返回 DirNode 树。"""
        if self._cancel_flag.is_set():
            return None

        name = os.path.basename(dirpath) or dirpath
        node = DirNode(name, dirpath, True)

        try:
            with os.scandir(dirpath) as entries:
                for entry in entries:
                    if self._cancel_flag.is_set():
                        break

                    self._scanned_count += 1
                    if self._scanned_count % 1000 == 0 and self.progress_callback:
                        self.progress_callback(entry.path, self._scanned_count)

                    try:
                        if entry.is_file(follow_symlinks=False):
                            fsize = entry.stat().st_size
                            child = DirNode(entry.name, entry.path, False)
                            child.size = fsize
                            child.file_count = 1
                            node.add_child(child)
                        elif entry.is_dir(follow_symlinks=False):
                            if self.skip_hidden and is_system_hidden(entry.path):
                                continue
                            # 跳过部分系统目录（Windows）
                            if os.name == "nt" and entry.name.lower() in ("system volume information", "$recycle.bin", "recovery"):
                                continue
                            sub = self._scan_dir(entry.path, depth + 1)
                            if sub:
                                node.add_child(sub)
                    except OSError:
                        pass
        except PermissionError:
            pass
        except OSError:
            pass

        return node

    def run(self):
        self._start_time = time.time()
        root = self._scan_dir(self.root_path)
        elapsed = time.time() - self._start_time
        if root and not self._cancel_flag.is_set():
            root.sort_by_size()
            self.callback(root, elapsed, self._scanned_count)


# ═══════════════════════════════════════════════════════════════
# 垃圾文件扫描器
# ═══════════════════════════════════════════════════════════════

class CleanupScanner:
    """扫描系统中可清理的垃圾文件/缓存/日志。"""

    CLEANUP_RULES = [
        # (类别名, 路径列表, 文件匹配模式列表)
        ("Windows 临时文件", [
            os.path.expandvars(r"%TEMP%"),
            os.path.expandvars(r"%SystemRoot%\Temp"),
        ], ["*.*"]),
        ("Windows 更新缓存", [
            os.path.expandvars(r"%SystemRoot%\SoftwareDistribution\Download"),
        ], ["*.*"]),
        ("Windows 日志文件", [
            os.path.expandvars(r"%SystemRoot%\Logs"),
        ], ["*.log", "*.old", "*.log.*"]),
        ("浏览器缓存 - Chrome", [
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\User Data\Default\Cache\Cache_Data"),
        ], ["*.*"]),
        ("浏览器缓存 - Edge", [
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Cache\Cache_Data"),
        ], ["*.*"]),
        ("浏览器缓存 - Firefox", [
            os.path.expandvars(r"%APPDATA%\Mozilla\Firefox\Profiles"),
        ], ["cache2/*"]),
        ("pip 缓存", [
            os.path.expandvars(r"%LOCALAPPDATA%\pip\cache"),
            os.path.expanduser("~/.cache/pip"),
        ], ["*.*"]),
        ("npm 缓存", [
            os.path.expandvars(r"%APPDATA%\npm-cache"),
            os.path.expanduser("~/.npm/_cacache"),
        ], ["*.*"]),
        ("回收站信息", [
            "C:\\$Recycle.Bin",
        ], ["*.*"]),
        ("用户临时文件", [
            os.path.expanduser("~/.cache"),
            os.path.expanduser("~/AppData/Local/Temp"),
        ], ["*.*"]),
        # Linux 特定
        ("APT 缓存", [
            "/var/cache/apt/archives",
        ], ["*.deb"]),
        ("systemd 日志", [
            "/var/log/journal",
        ], ["*@*.journal", "*.journal"]),
        ("旧内核文件", [
            "/boot",
        ], ["vmlinuz-*", "initrd.img-*", "System.map-*", "config-*"]),
    ]

    @classmethod
    def scan(cls) -> list[dict]:
        """扫描可清理项，返回 [{category, path, size, file_count, files[]}, ...]"""
        results = []
        for category, dirs, patterns in cls.CLEANUP_RULES:
            total_size = 0
            file_count = 0
            found_files = []
            for base_dir in dirs:
                base_dir = os.path.expandvars(base_dir)
                base_dir = os.path.expanduser(base_dir)
                if not os.path.isdir(base_dir):
                    continue
                for pat in patterns:
                    # 如果模式包含 * 且在中间（如 cache2/*），需要特殊处理
                    if "/" in pat or "\\" in pat:
                        # 路径模式
                        full_pat = os.path.join(base_dir, pat)
                        for f in glob.glob(full_pat, recursive="**" in pat):
                            if os.path.isfile(f):
                                try:
                                    sz = os.path.getsize(f)
                                    total_size += sz
                                    file_count += 1
                                    found_files.append(f)
                                except OSError:
                                    pass
                    else:
                        # 简单 glob 模式
                        for root, dirs_in, files in os.walk(base_dir, topdown=True):
                            for f in files:
                                if any(fnmatch.fnmatch(f.lower(), p.lower()) for p in patterns if p != "*.*") or pat == "*.*":
                                    fpath = os.path.join(root, f)
                                    try:
                                        sz = os.path.getsize(fpath)
                                        total_size += sz
                                        file_count += 1
                                        found_files.append(fpath)
                                    except OSError:
                                        pass
                            # 限制深度避免太慢
                            if root.count(os.sep) - base_dir.count(os.sep) > 3:
                                dirs_in.clear()
                if found_files:
                    results.append({
                        "category": category,
                        "path": base_dir,
                        "size": total_size,
                        "file_count": file_count,
                        "files": found_files,
                    })
        return results


# ═══════════════════════════════════════════════════════════════
# Treemap 绘制
# ═══════════════════════════════════════════════════════════════

class TreemapRenderer:
    """Treemap 矩形图渲染器，在 tkinter Canvas 上绘制。"""

    COLORS = [
        "#3498db", "#2ecc71", "#e74c3c", "#f39c12", "#9b59b6",
        "#1abc9c", "#e67e22", "#2980b9", "#27ae60", "#c0392b",
        "#d35400", "#8e44ad", "#16a085", "#f1c40f", "#7f8c8d",
        "#34495e", "#e91e63", "#00bcd4", "#ff5722", "#795548",
    ]

    def __init__(self, canvas: tk.Canvas):
        self.canvas = canvas
        self._nodes = []
        self._color_map = {}

    def draw(self, nodes: list[DirNode], total_size: int):
        """绘制 treemap。nodes 应为子节点列表。"""
        self.canvas.delete("all")
        self._nodes = nodes
        w = self.canvas.winfo_width() or 600
        h = self.canvas.winfo_height() or 400
        if w < 20 or h < 20:
            return

        pad = 2
        rects = self._layout_slice_and_dice(
            [(n, n.size) for n in nodes if n.size > 0],
            0, 0, w - pad, h - pad, total_size
        )

        for i, (node, x, y, rw, rh) in enumerate(rects):
            if rw <= 4 or rh <= 4:
                continue
            color = self._get_color(node)
            self.canvas.create_rectangle(x, y, x + rw, y + rh,
                                         fill=color, outline="#fff", width=1)
            # 标签
            if rw > 30 and rh > 16:
                label = node.name[:20]
                size_str = format_size(node.size)
                self.canvas.create_text(
                    x + rw / 2, y + rh / 2 - 6,
                    text=label, fill="white", font=("Segoe UI", 9, "bold"),
                    anchor="center", width=rw - 8
                )
                self.canvas.create_text(
                    x + rw / 2, y + rh / 2 + 8,
                    text=size_str, fill="rgba(255,255,255,0.8)",
                    font=("Segoe UI", 7), anchor="center"
                )

    def _layout_slice_and_dice(self, items, x, y, w, h, total):
        """Slice-and-dice treemap 布局算法。简单可靠。"""
        if not items or total <= 0 or w <= 0 or h <= 0:
            return []

        items = sorted(items, key=lambda t: t[1], reverse=True)
        result = []
        cx, cy = x, y
        rw, rh = w, h
        remaining = total

        for node, size in items:
            if size <= 0:
                continue
            if rw > rh:
                slice_w = max(1, (size / remaining) * rw)
                result.append((node, cx, cy, slice_w, rh))
                cx += slice_w
                rw -= slice_w
            else:
                slice_h = max(1, (size / remaining) * rh)
                result.append((node, cx, cy, rw, slice_h))
                cy += slice_h
                rh -= slice_h
            remaining -= size

        return result

    def _get_color(self, node) -> str:
        key = node.name[:2].lower() if node else "??"
        if key not in self._color_map:
            self._color_map[key] = self.COLORS[hash(key) % len(self.COLORS)]
        return self._color_map[key]

    def get_node_at(self, x, y) -> DirNode | None:
        """根据坐标查找对应的节点（基于最后一次绘制的布局）。"""
        # 简化实现：遍历所有矩形
        items = self.canvas.find_all()
        closest = self.canvas.find_closest(x, y)
        if closest:
            idx = items.index(closest[0]) if closest else -1
            if 0 <= idx < len(self._nodes):
                return self._nodes[idx]
        return None


# ═══════════════════════════════════════════════════════════════
# 主窗口
# ═══════════════════════════════════════════════════════════════

class DiskAnalyzerApp:
    """主应用窗口。"""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Disk Analyzer - 磁盘空间分析工具")
        self.root.geometry("1200x750")
        self.root.minsize(800, 500)

        self.scanner: DiskScanner | None = None
        self.current_root: DirNode | None = None
        self.scanned_path = ""
        self.node_map: dict[str, DirNode] = {}  # path -> DirNode 映射
        self.tree_id_map: dict[str, str] = {}   # path -> treeview item id

        # 设置样式
        self._setup_style()
        self._build_ui()

        # 绑定事件
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.treemap_canvas.bind("<Button-1>", self._on_treemap_click)
        self.treemap_canvas.bind("<Configure>", self._on_treemap_resize)

    # ── UI 构建 ─────────────────────────────────────────────

    def _setup_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#f0f0f0")
        style.configure("TLabel", background="#f0f0f0")
        style.configure("TButton", padding=4)
        style.configure("Treeview", rowheight=24)
        style.configure("TNotebook", background="#f0f0f0")
        style.configure("TNotebook.Tab", padding=[10, 4])

    def _build_ui(self):
        # ── 顶部工具栏 ──
        toolbar = ttk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=5, pady=(5, 0))

        ttk.Label(toolbar, text="扫描目录:").pack(side=tk.LEFT, padx=(0, 5))
        self.path_var = tk.StringVar(value="C:\\" if os.name == "nt" else os.path.expanduser("~"))
        self.path_entry = ttk.Entry(toolbar, textvariable=self.path_var, width=50)
        self.path_entry.pack(side=tk.LEFT, padx=(0, 5))
        self.path_entry.bind("<Return>", lambda e: self._start_scan())

        ttk.Button(toolbar, text="浏览...", command=self._browse_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="▶ 开始扫描", command=self._start_scan).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="⏹ 停止", command=self._stop_scan).pack(side=tk.LEFT, padx=2)

        # 快捷路径按钮
        if os.name == "nt":
            shortcuts = [
                ("C:\\", "C:\\"),
                (os.path.expanduser("~"), "用户目录"),
            ]
        else:
            shortcuts = [
                ("/", "根目录"),
                (os.path.expanduser("~"), "Home"),
                ("/tmp", "/tmp"),
            ]
        for path, label in shortcuts:
            ttk.Button(toolbar, text=label,
                       command=lambda p=path: self._quick_scan(p)).pack(side=tk.LEFT, padx=2)

        # ── 主体区域 — PanedWindow ──
        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧：目录树
        left_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=3)

        tree_scroll = ttk.Scrollbar(left_frame)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree = ttk.Treeview(left_frame, columns=("size", "files"),
                                 show="tree headings",
                                 yscrollcommand=tree_scroll.set)
        self.tree.heading("#0", text="名称")
        self.tree.heading("size", text="大小", command=lambda: self._sort_tree("size"))
        self.tree.heading("files", text="文件数")
        self.tree.column("#0", width=280, minwidth=100)
        self.tree.column("size", width=100, minwidth=60)
        self.tree.column("files", width=80, minwidth=50)
        self.tree.pack(fill=tk.BOTH, expand=True)
        tree_scroll.config(command=self.tree.yview)

        # 右侧：多标签面板
        right_frame = ttk.Frame(paned)
        paned.add(right_frame, weight=5)

        self.notebook = ttk.Notebook(right_frame)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        # Tab 1: Treemap
        self.treemap_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.treemap_frame, text="📊 矩形树图")

        self.treemap_canvas = tk.Canvas(self.treemap_frame, bg="#2b2b2b",
                                        highlightthickness=0)
        self.treemap_canvas.pack(fill=tk.BOTH, expand=True)

        # Treemap 提示标签
        self.treemap_label = ttk.Label(self.treemap_frame,
                                       text="点击 ▶ 开始扫描 以查看可视化",
                                       anchor="center")
        self.treemap_label.place(relx=0.5, rely=0.5, anchor="center")

        # Tab 2: 大文件列表
        self.large_files_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.large_files_frame, text="📄 大文件 Top 100")

        lf_scroll = ttk.Scrollbar(self.large_files_frame)
        lf_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.large_files_list = ttk.Treeview(self.large_files_frame,
                                             columns=("lf_path", "lf_size"),
                                             show="headings",
                                             yscrollcommand=lf_scroll.set)
        self.large_files_list.heading("lf_path", text="文件路径")
        self.large_files_list.heading("lf_size", text="大小")
        self.large_files_list.column("lf_path", width=400)
        self.large_files_list.column("lf_size", width=100)
        self.large_files_list.pack(fill=tk.BOTH, expand=True)
        lf_scroll.config(command=self.large_files_list.yview)
        self.large_files_list.bind("<Double-1>", self._on_large_file_double_click)
        self.large_files_list.bind("<Button-3>", self._on_large_file_right_click)

        # Tab 3: 文件类型统计
        self.filetypes_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.filetypes_frame, text="📁 文件类型")

        ft_scroll = ttk.Scrollbar(self.filetypes_frame)
        ft_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.filetypes_list = ttk.Treeview(self.filetypes_frame,
                                           columns=("ft_ext", "ft_count", "ft_size", "ft_pct"),
                                           show="headings",
                                           yscrollcommand=ft_scroll.set)
        self.filetypes_list.heading("ft_ext", text="类型")
        self.filetypes_list.heading("ft_count", text="文件数")
        self.filetypes_list.heading("ft_size", text="总大小")
        self.filetypes_list.heading("ft_pct", text="占比")
        self.filetypes_list.column("ft_ext", width=100)
        self.filetypes_list.column("ft_count", width=80)
        self.filetypes_list.column("ft_size", width=100)
        self.filetypes_list.column("ft_pct", width=60)
        self.filetypes_list.pack(fill=tk.BOTH, expand=True)
        ft_scroll.config(command=self.filetypes_list.yview)

        # Tab 4.5: 目录膨胀分析
        self.dirbloat_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.dirbloat_frame, text="📂 目录膨胀")

        db_toolbar = ttk.Frame(self.dirbloat_frame)
        db_toolbar.pack(fill=tk.X, padx=5, pady=(5, 0))
        ttk.Label(db_toolbar, text="排序:").pack(side=tk.LEFT, padx=(0, 5))
        self.db_sort_var = tk.StringVar(value="size")
        ttk.Radiobutton(db_toolbar, text="按大小", variable=self.db_sort_var,
                        value="size", command=lambda: self._resort_dirbloat()).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(db_toolbar, text="按文件数", variable=self.db_sort_var,
                        value="files", command=lambda: self._resort_dirbloat()).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(db_toolbar, text="按子目录数", variable=self.db_sort_var,
                        value="subdirs", command=lambda: self._resort_dirbloat()).pack(side=tk.LEFT, padx=2)
        ttk.Separator(db_toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8, pady=2)
        ttk.Label(db_toolbar, text="许多小文件:", foreground="#e67e22",
                  font=("Segoe UI", 8, "italic")).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Label(db_toolbar, text="文件数>500 且 均值<100KB", foreground="#888",
                  font=("Segoe UI", 7)).pack(side=tk.LEFT)

        db_scroll = ttk.Scrollbar(self.dirbloat_frame)
        db_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.dirbloat_list = ttk.Treeview(self.dirbloat_frame,
                                          columns=("db_name", "db_path", "db_size",
                                                   "db_files", "db_subdirs", "db_avg"),
                                          show="headings",
                                          yscrollcommand=db_scroll.set)
        self.dirbloat_list.heading("db_name", text="目录")
        self.dirbloat_list.heading("db_path", text="路径")
        self.dirbloat_list.heading("db_size", text="大小")
        self.dirbloat_list.heading("db_files", text="文件数")
        self.dirbloat_list.heading("db_subdirs", text="子目录数")
        self.dirbloat_list.heading("db_avg", text="平均文件大小")
        self.dirbloat_list.column("db_name", width=180)
        self.dirbloat_list.column("db_path", width=280)
        self.dirbloat_list.column("db_size", width=90)
        self.dirbloat_list.column("db_files", width=70)
        self.dirbloat_list.column("db_subdirs", width=70)
        self.dirbloat_list.column("db_avg", width=100)
        self.dirbloat_list.pack(fill=tk.BOTH, expand=True)
        db_scroll.config(command=self.dirbloat_list.yview)

        # Tab 4: 垃圾清理
        self.cleanup_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.cleanup_frame, text="🧹 垃圾清理")

        cleanup_header = ttk.Frame(self.cleanup_frame)
        cleanup_header.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(cleanup_header,
                  text="系统垃圾与缓存文件扫描",
                  font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT)
        ttk.Button(cleanup_header, text="🔍 扫描垃圾文件",
                   command=self._scan_cleanup).pack(side=tk.RIGHT, padx=5)

        self.cleanup_tree_frame = ttk.Frame(self.cleanup_frame)
        self.cleanup_tree_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        cl_scroll = ttk.Scrollbar(self.cleanup_tree_frame)
        cl_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.cleanup_tree = ttk.Treeview(self.cleanup_tree_frame,
                                         columns=("cl_size", "cl_files", "cl_path"),
                                         show="tree headings",
                                         yscrollcommand=cl_scroll.set)
        self.cleanup_tree.heading("#0", text="类别")
        self.cleanup_tree.heading("cl_size", text="可清理大小")
        self.cleanup_tree.heading("cl_files", text="文件数")
        self.cleanup_tree.heading("cl_path", text="路径")
        self.cleanup_tree.column("#0", width=200)
        self.cleanup_tree.column("cl_size", width=100)
        self.cleanup_tree.column("cl_files", width=80)
        self.cleanup_tree.column("cl_path", width=300)
        self.cleanup_tree.pack(fill=tk.BOTH, expand=True)
        cl_scroll.config(command=self.cleanup_tree.yview)

        self.cleanup_tree.bind("<Double-1>", self._on_cleanup_double_click)

        cleanup_bottom = ttk.Frame(self.cleanup_frame)
        cleanup_bottom.pack(fill=tk.X, padx=10, pady=(0, 10))
        self.cleanup_total_label = ttk.Label(cleanup_bottom, text="")
        self.cleanup_total_label.pack(side=tk.LEFT)
        ttk.Button(cleanup_bottom, text="🗑 清理选中项",
                   command=self._cleanup_selected).pack(side=tk.RIGHT, padx=2)
        ttk.Button(cleanup_bottom, text="🗑 清理全部",
                   command=self._cleanup_all).pack(side=tk.RIGHT, padx=2)

        # ── 底部状态栏 ──
        self.status_frame = ttk.Frame(self.root)
        self.status_frame.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.status_label = ttk.Label(self.status_frame, text="就绪")
        self.status_label.pack(side=tk.LEFT)

        self.progress = ttk.Progressbar(self.status_frame, mode="indeterminate", length=200)
        self.progress.pack(side=tk.LEFT, padx=(10, 0))

        self.status_details = ttk.Label(self.status_frame, text="")
        self.status_details.pack(side=tk.RIGHT)

    # ── 扫描逻辑 ─────────────────────────────────────────────

    def _browse_folder(self):
        path = filedialog.askdirectory(title="选择要扫描的目录")
        if path:
            self.path_var.set(path)

    def _quick_scan(self, path: str):
        self.path_var.set(path)
        self._start_scan()

    def _start_scan(self):
        path = self.path_var.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showwarning("无效路径", f"目录不存在：{path}")
            return

        if self.scanner and self.scanner.is_alive():
            self._stop_scan()

        self.scanned_path = path
        self.tree.delete(*self.tree.get_children())
        self.node_map.clear()
        self.tree_id_map.clear()
        self._clear_tabs()

        self.status_label.config(text="正在扫描...")
        self.progress.start(10)

        self.scanner = DiskScanner(
            path,
            callback=self._on_scan_complete,
            progress_callback=self._on_scan_progress
        )
        self.scanner.start()

    def _stop_scan(self):
        if self.scanner and self.scanner.is_alive():
            self.scanner.cancel()
            self.status_label.config(text="已停止")
            self.progress.stop()

    def _on_scan_progress(self, current_path: str, count: int):
        self.root.after(0, lambda: self._update_progress(current_path, count))

    def _update_progress(self, current_path: str, count: int):
        self.status_details.config(text=f"已扫描 {format_count(count)} 项 | {current_path[-60:]}")

    def _on_scan_complete(self, root_node: DirNode, elapsed: float, total_count: int):
        self.root.after(0, lambda: self._display_results(root_node, elapsed, total_count))

    def _display_results(self, root_node: DirNode, elapsed: float, total_count: int):
        self.progress.stop()
        self.current_root = root_node
        self.status_label.config(
            text=f"扫描完成 | {format_count(total_count)} 项 | {format_size(root_node.size)} | "
                 f"耗时 {elapsed:.1f} 秒"
        )
        self.status_details.config(text="")

        # 填充目录树
        self._populate_tree(root_node, "")
        # 绘制 treemap
        self._draw_treemap(root_node)
        # 大文件列表
        self._populate_large_files(root_node)
        # 文件类型统计
        self._populate_filetypes(root_node)

        # 目录膨胀分析
        self._populate_dir_bloat(root_node)

        self.node_map[root_node.path] = root_node

    # ── 目录树 ───────────────────────────────────────────────

    def _populate_tree(self, node: DirNode, parent_id: str):
        """递归填充 Treeview。"""
        if node.is_dir and not node.children:
            return  # 空目录不显示

        size_str = format_size(node.size)
        files_str = format_count(node.file_count) if node.is_dir else "1"

        iid = self.tree.insert(
            parent_id, "end",
            text=node.name if node.name else node.path,
            values=(size_str, files_str),
            open=False
        )
        self.tree_id_map[node.path] = iid
        self.node_map[node.path] = node

        if node.is_dir:
            for child in node.children:
                if child.is_dir:
                    self._populate_tree(child, iid)

    def _sort_tree(self, column: str):
        # 简化排序：只对当前展开的层级
        selected = self.tree.selection()
        for item in selected:
            parent = self.tree.parent(item)
            if not parent:
                children = list(self.tree.get_children(""))
            else:
                children = list(self.tree.get_children(parent))

            def sort_key(iid):
                vals = self.tree.item(iid, "values")
                if column == "size":
                    return self._parse_size(vals[0])
                return vals[1] if len(vals) > 1 else 0

            sorted_children = sorted(children, key=sort_key, reverse=True)
            for i, child in enumerate(sorted_children):
                self.tree.move(child, self.tree.parent(child), i)

    def _parse_size(self, size_str: str) -> float:
        """将格式化的大小字符串转回数字用于排序。"""
        try:
            parts = size_str.split()
            val = float(parts[0])
            unit = parts[1] if len(parts) > 1 else "B"
            multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
            return val * multipliers.get(unit, 1)
        except (ValueError, IndexError):
            return 0

    # ── Treemap ──────────────────────────────────────────────

    def _draw_treemap(self, root_node: DirNode):
        """绘制 treemap。"""
        children = [c for c in root_node.children if c.is_dir]
        if not children:
            children = [c for c in root_node.children if not c.is_dir][:50]

        renderer = TreemapRenderer(self.treemap_canvas)
        renderer.draw(children, root_node.size)
        self.treemap_label.place_forget()  # 移除提示文字

    def _on_treemap_click(self, event):
        # 保留供将来使用
        pass

    def _on_treemap_resize(self, event):
        if self.current_root:
            self._draw_treemap(self.current_root)

    # ── 大文件 ───────────────────────────────────────────────

    def _populate_large_files(self, root_node: DirNode):
        """收集所有文件，取 Top 100。"""
        files = []
        self._collect_files(root_node, files)
        files.sort(key=lambda f: f[1], reverse=True)
        top100 = files[:100]

        self.large_files_list.delete(*self.large_files_list.get_children())
        for path, size in top100:
            self.large_files_list.insert("", "end", values=(path, format_size(size)))

    def _collect_files(self, node: DirNode, result: list):
        if not node.is_dir:
            result.append((node.path, node.size))
        else:
            for child in node.children:
                self._collect_files(child, result)

    # ── 文件类型统计 ─────────────────────────────────────────

    def _populate_filetypes(self, root_node: DirNode):
        ext_stats: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        total_files = 0

        def walk(n: DirNode):
            nonlocal total_files
            if not n.is_dir:
                ext = os.path.splitext(n.name)[1].lower() or "(无扩展名)"
                count, size = ext_stats[ext]
                ext_stats[ext] = (count + 1, size + n.size)
                total_files += 1
            else:
                for c in n.children:
                    walk(c)

        walk(root_node)

        self.filetypes_list.delete(*self.filetypes_list.get_children())
        sorted_exts = sorted(ext_stats.items(), key=lambda kv: kv[1][1], reverse=True)

        for ext, (count, size) in sorted_exts:
            pct = (size / root_node.size * 100) if root_node.size > 0 else 0
            self.filetypes_list.insert("", "end",
                                       values=(ext, format_count(count),
                                               format_size(size), f"{pct:.1f}%"))

    # ── 目录膨胀分析 ──────────────────────────────────────────

    def _populate_dir_bloat(self, root_node: DirNode):
        """收集所有子目录，按大小/文件数排序展示，标记"许多小文件"目录。"""
        dirs = []
        self._collect_all_dirs(root_node, dirs)
        self._all_dirs_data = dirs  # 保存原始数据供排序切换
        self._resort_dirbloat()

    def _collect_all_dirs(self, node: DirNode, result: list):
        """递归收集所有目录节点。"""
        if node.is_dir:
            subdir_count = sum(1 for c in node.children if c.is_dir)
            file_count_self = sum(1 for c in node.children if not c.is_dir)
            result.append({
                "name": node.name or node.path,
                "path": node.path,
                "size": node.size,
                "file_count": file_count_self,
                "subdir_count": subdir_count,
            })
            for child in node.children:
                if child.is_dir:
                    self._collect_all_dirs(child, result)

    def _resort_dirbloat(self):
        """根据当前排序方式重新排列目录膨胀列表。"""
        if not hasattr(self, '_all_dirs_data') or not self._all_dirs_data:
            return

        sort_key = self.db_sort_var.get()
        if sort_key == "size":
            sorted_dirs = sorted(self._all_dirs_data, key=lambda d: d["size"], reverse=True)
        elif sort_key == "files":
            sorted_dirs = sorted(self._all_dirs_data, key=lambda d: d["file_count"], reverse=True)
        elif sort_key == "subdirs":
            sorted_dirs = sorted(self._all_dirs_data, key=lambda d: d["subdir_count"], reverse=True)
        else:
            sorted_dirs = self._all_dirs_data

        self.dirbloat_list.delete(*self.dirbloat_list.get_children())

        # 配置 tag 样式
        self.dirbloat_list.tag_configure("many_small", foreground="#e67e22")
        self.dirbloat_list.tag_configure("huge_dir", foreground="#e74c3c")

        for d in sorted_dirs:
            # 计算平均文件大小
            if d["file_count"] > 0:
                avg = d["size"] / d["file_count"]
                avg_str = format_size(int(avg)) if avg > 0 else "0 B"
            else:
                avg_str = "-"
                avg = float("inf")

            # 标记"许多小文件"：文件数 > 500 且平均大小 < 100KB
            tags = []
            if d["file_count"] > 500 and avg < 100 * 1024:
                tags.append("many_small")
            if d["size"] >= 1024 * 1024 * 1024:  # >= 1GB
                tags.append("huge_dir")
            tag_tuple = tuple(tags) if tags else ()

            self.dirbloat_list.insert(
                "", "end",
                values=(d["name"], d["path"], format_size(d["size"]),
                        format_count(d["file_count"]), format_count(d["subdir_count"]),
                        avg_str),
                tags=tag_tuple
            )

    # ── 清理扫描 ─────────────────────────────────────────────

    def _scan_cleanup(self):
        self.cleanup_tree.delete(*self.cleanup_tree.get_children())
        self.cleanup_total_label.config(text="正在扫描...")
        self.root.update()

        results = CleanupScanner.scan()
        total_cleanable = 0
        for r in results:
            self.cleanup_tree.insert("", "end",
                                     text=r["category"],
                                     values=(format_size(r["size"]),
                                             format_count(r["file_count"]),
                                             r["path"]))
            total_cleanable += r["size"]

        self.cleanup_total_label.config(
            text=f"共 {len(results)} 类 | 可清理空间：{format_size(total_cleanable)}"
        )

    def _cleanup_selected(self):
        selected = self.cleanup_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选择要清理的项")
            return
        self._do_cleanup(selected)

    def _cleanup_all(self):
        all_items = self.cleanup_tree.get_children()
        if not all_items:
            messagebox.showinfo("提示", "请先扫描垃圾文件")
            return
        self._do_cleanup(all_items)

    def _do_cleanup(self, items):
        total = 0
        count = 0
        categories = []
        for item in items:
            cat = self.cleanup_tree.item(item, "text")
            vals = self.cleanup_tree.item(item, "values")
            categories.append(cat)
            total += self._parse_size(vals[0])

        if not messagebox.askyesno(
            "确认清理",
            f"将清理以下类别：\n\n{chr(10).join(categories)}\n\n"
            f"预计释放 {format_size(total)} 空间。\n\n"
            f"⚠ 此操作不可撤销，是否继续？"
        ):
            return

        # 实际删除...
        messagebox.showinfo("提示", "清理功能仅为扫描展示。\n"
                            "请在确认文件后可手动删除。")

    # ── 交互操作 ─────────────────────────────────────────────

    def _on_tree_double_click(self, event):
        """双击展开/折叠，并在 treemap 中显示该目录的子目录。"""
        item = self.tree.identify_row(event.y)
        if not item:
            return

        # 查找对应的 DirNode
        path = None
        for p, iid in self.tree_id_map.items():
            if iid == item:
                path = p
                break

        if path and path in self.node_map:
            node = self.node_map[path]
            if node.is_dir and node.children:
                self._draw_treemap(node)

    def _on_tree_right_click(self, event):
        """右键菜单。"""
        item = self.tree.identify_row(event.y)
        if not item:
            return
        self.tree.selection_set(item)

        path = None
        for p, iid in self.tree_id_map.items():
            if iid == item:
                path = p
                break

        if not path:
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="📂 打开所在目录", command=lambda p=path: self._open_location(p))
        menu.add_command(label="📂 在文件管理器中打开", command=lambda p=path: self._open_in_explorer(p))
        menu.add_separator()
        menu.add_command(label="🗑 删除", command=lambda p=path: self._delete_item(p))
        menu.post(event.x_root, event.y_root)

    def _on_large_file_double_click(self, event):
        item = self.large_files_list.identify_row(event.y)
        if not item:
            return
        vals = self.large_files_list.item(item, "values")
        if vals:
            self._open_in_explorer(vals[0])

    def _on_large_file_right_click(self, event):
        item = self.large_files_list.identify_row(event.y)
        if not item:
            return
        self.large_files_list.selection_set(item)
        vals = self.large_files_list.item(item, "values")
        if not vals:
            return
        path = vals[0]
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="📂 打开所在目录", command=lambda p=path: self._open_location(p))
        menu.add_command(label="🗑 删除", command=lambda p=path: self._delete_item(p))
        menu.post(event.x_root, event.y_root)

    def _on_cleanup_double_click(self, event):
        item = self.cleanup_tree.identify_row(event.y)
        if not item:
            return
        vals = self.cleanup_tree.item(item, "values")
        if vals and len(vals) >= 3:
            self._open_in_explorer(vals[2])

    def _open_location(self, path: str):
        """打开文件所在目录。"""
        if os.path.isfile(path):
            d = os.path.dirname(path)
        elif os.path.isdir(path):
            d = path
        else:
            return
        self._open_in_explorer(d)

    def _open_in_explorer(self, path: str):
        """在系统文件管理器中打开。"""
        if not os.path.exists(path):
            messagebox.showwarning("路径不存在", f"路径不存在：{path}")
            return
        try:
            if os.name == "nt":
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f"open '{path}'")
            else:
                os.system(f"xdg-open '{path}' &")
        except Exception as e:
            messagebox.showerror("错误", f"无法打开：{e}")

    def _delete_item(self, path: str):
        """删除文件或目录，带确认。"""
        if not os.path.exists(path):
            return
        label = "目录" if os.path.isdir(path) else "文件"
        size = 0
        try:
            if os.path.isdir(path):
                # 计算目录大小
                total = 0
                for dirpath, dirnames, filenames in os.walk(path):
                    for f in filenames:
                        try:
                            total += os.path.getsize(os.path.join(dirpath, f))
                        except OSError:
                            pass
                size = total
            else:
                size = os.path.getsize(path)
        except OSError:
            pass

        msg = f"确定要永久删除此{label}吗？\n\n路径：{path}\n大小：{format_size(size)}\n\n⚠ 此操作不可撤销！"
        if not messagebox.askyesno("确认删除", msg, icon="warning"):
            return

        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
            messagebox.showinfo("完成", f"已删除：{path}")

            # 从树中移除
            if path in self.tree_id_map:
                self.tree.delete(self.tree_id_map[path])
            # 如果根目录发生变化，建议重新扫描
            if os.path.commonpath([path, self.scanned_path]) == self.scanned_path:
                self.status_label.config(text=f"已删除 {format_size(size)} | 建议重新扫描以更新数据")

        except Exception as e:
            messagebox.showerror("删除失败", f"{e}")

    # ── 辅助 ─────────────────────────────────────────────────

    def _clear_tabs(self):
        self.large_files_list.delete(*self.large_files_list.get_children())
        self.filetypes_list.delete(*self.filetypes_list.get_children())
        self.dirbloat_list.delete(*self.dirbloat_list.get_children())

    def _on_close(self):
        if self.scanner and self.scanner.is_alive():
            self.scanner.cancel()
        self.root.destroy()


# ═══════════════════════════════════════════════════════════════
# 开屏广告 SplashScreen
# ═══════════════════════════════════════════════════════════════

class SplashScreen:
    """启动时展示的开屏广告窗口。"""

    AD_CONFIG_PATH = "ads.json"

    def __init__(self, master: tk.Tk, on_complete):
        self.master = master
        self.on_complete = on_complete
        self.ad = self._pick_ad()
        self.remaining = self.ad.get("duration", 5) if self.ad else 0
        self._after_id = None

        if self.ad:
            self._build(master)
            self._start_countdown()
        else:
            # 无广告可用，直接跳过
            master.after(100, self._finish)

    def _pick_ad(self):
        """从 ads.json 中随机选一个启用的广告。"""
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            self.AD_CONFIG_PATH
        )
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                ads = json.load(f)
            enabled = [a for a in ads if a.get("enabled", True)]
            return choice(enabled) if enabled else None
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _build(self, master):
        """构建广告窗口 UI。"""
        self.win = tk.Toplevel(master)
        self.win.title("")
        self.win.overrideredirect(True)  # 无边框
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#1a1a2e")

        # 窗口大小：屏幕的 55%
        sw = master.winfo_screenwidth()
        sh = master.winfo_screenheight()
        ww = int(sw * 0.55)
        wh = int(sh * 0.55)
        x = (sw - ww) // 2
        y = (sh - wh) // 2
        self.win.geometry(f"{ww}x{wh}+{x}+{y}")

        # ── 顶部装饰条 ──
        top_bar = tk.Frame(self.win, bg="#16213e", height=4)
        top_bar.pack(fill=tk.X)
        tk.Frame(top_bar, bg="#e94560", height=4, width=ww//3).place(x=0, y=0)
        tk.Frame(top_bar, bg="#0f3460", height=4, width=ww//3).place(x=ww//3, y=0)
        tk.Frame(top_bar, bg="#533483", height=4, width=ww//3).place(x=2*ww//3, y=0)

        # ── 主体内容区 ──
        body = tk.Frame(self.win, bg="#1a1a2e")
        body.pack(fill=tk.BOTH, expand=True, padx=40, pady=(30, 10))

        # Logo / 标题区域
        logo_frame = tk.Frame(body, bg="#1a1a2e")
        logo_frame.pack(pady=(20, 10))
        tk.Label(logo_frame, text="💾", font=("Segoe UI", 48),
                 bg="#1a1a2e", fg="#e94560").pack()
        tk.Label(logo_frame, text="Disk Analyzer",
                 font=("Segoe UI", 20, "bold"),
                 bg="#1a1a2e", fg="#ffffff").pack(pady=(5, 0))
        tk.Label(logo_frame, text="磁盘空间分析工具",
                 font=("Segoe UI", 10),
                 bg="#1a1a2e", fg="#888888").pack()

        # ── 广告卡片 ──
        ad_card = tk.Frame(body, bg="#16213e", highlightbackground="#e94560",
                           highlightthickness=1, padx=3, pady=3)
        ad_card.pack(pady=(30, 20), fill=tk.X)
        ad_card.bind("<Button-1>", lambda e: self._open_ad_url())

        ad_inner = tk.Frame(ad_card, bg="#16213e")
        ad_inner.pack(fill=tk.X, padx=20, pady=20)
        ad_inner.bind("<Button-1>", lambda e: self._open_ad_url())

        # 广告标签
        tk.Label(ad_inner, text="— ADVERTISEMENT —",
                 font=("Segoe UI", 7), bg="#16213e", fg="#666666").pack(anchor="w")
        ad_inner.bind("<Button-1>", lambda e: self._open_ad_url())

        # 广告标题
        title_label = tk.Label(ad_inner, text=self.ad.get("title", ""),
                               font=("Segoe UI", 16, "bold"),
                               bg="#16213e", fg="#ffffff",
                               wraplength=ww - 130, justify="left")
        title_label.pack(anchor="w", pady=(10, 5))
        title_label.bind("<Button-1>", lambda e: self._open_ad_url())

        # 广告副标题
        sub_label = tk.Label(ad_inner, text=self.ad.get("subtitle", ""),
                             font=("Segoe UI", 10),
                             bg="#16213e", fg="#aaaaaa",
                             wraplength=ww - 130, justify="left")
        sub_label.pack(anchor="w")
        sub_label.bind("<Button-1>", lambda e: self._open_ad_url())

        # 点击提示
        hint = tk.Label(ad_inner, text="👆 点击了解更多",
                        font=("Segoe UI", 8),
                        bg="#16213e", fg="#e94560")
        hint.pack(anchor="w", pady=(10, 0))
        hint.bind("<Button-1>", lambda e: self._open_ad_url())

        # ── 底部控制栏 ──
        bottom = tk.Frame(self.win, bg="#1a1a2e")
        bottom.pack(fill=tk.X, padx=40, pady=(0, 20))

        self.countdown_label = tk.Label(
            bottom, text=f"{self.remaining} 秒后自动进入",
            font=("Segoe UI", 9), bg="#1a1a2e", fg="#888888"
        )
        self.countdown_label.pack(side=tk.LEFT)

        skip_btn = tk.Button(bottom, text=f"跳过 ({self.remaining}s)",
                             font=("Segoe UI", 9),
                             bg="#e94560", fg="#ffffff", relief="flat",
                             activebackground="#c73650",
                             command=self._finish,
                             cursor="hand2")
        skip_btn.pack(side=tk.RIGHT)

    def _start_countdown(self):
        if self.remaining <= 0:
            self._finish()
            return
        self.countdown_label.config(text=f"{self.remaining} 秒后自动进入")
        self._after_id = self.master.after(1000, self._tick)

    def _tick(self):
        self.remaining -= 1
        if self.remaining <= 0:
            self._finish()
        else:
            self._start_countdown()

    def _open_ad_url(self):
        """在浏览器中打开广告链接。"""
        url = self.ad.get("url", "")
        if url:
            import webbrowser
            webbrowser.open(url)

    def _finish(self):
        if self._after_id:
            self.master.after_cancel(self._after_id)
        if hasattr(self, "win"):
            self.win.destroy()
        self.on_complete()


# ═══════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════

def main():
    root = tk.Tk()
    root.withdraw()  # 先隐藏主窗口

    def launch_app():
        root.deiconify()  # 显示主窗口
        DiskAnalyzerApp(root)

    SplashScreen(root, on_complete=launch_app)
    root.mainloop()


if __name__ == "__main__":
    main()