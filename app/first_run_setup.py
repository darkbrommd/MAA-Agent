"""First-run configuration for MAA Agent.

The wizard writes only ``config/config.json`` in the application directory. Existing
advanced settings are preserved when the helper is reused with a base config.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from project_paths import config_path, config_template_path


MAA_INSTALL_URL = "https://docs.maa.plus/zh-cn/manual/install.html"


def configured_maa_labels(config: dict) -> list[str]:
    """Return configured MAA labels in stable human order."""
    labels: set[str] = set()
    for key in ("maa_instance_dirs", "maa_gui_config_paths"):
        value = config.get(key)
        if isinstance(value, dict):
            labels.update(str(label) for label in value if str(label).upper().startswith("MAA"))

    def sort_key(label: str):
        suffix = label[3:]
        return (0, int(suffix)) if suffix.isdigit() else (1, label.casefold())

    return sorted(labels, key=sort_key) or ["MAA1"]


def build_first_run_config(template: dict, maa_directories: list[str | Path], *,
                           enable_bark: bool = False, bark_key: str = "") -> dict:
    """Build a private config for one or two MAA installations."""
    if not 1 <= len(maa_directories) <= 2:
        raise ValueError("请选择一个或两个 MAA 安装目录。")
    config = copy.deepcopy(template)
    instance_dirs: dict[str, str] = {}
    gui_paths: dict[str, str] = {}
    log_paths: list[str] = []
    aliases: dict[str, str] = {}
    launch_paths = dict(config.get("launch_paths") or {})
    autostart = dict(config.get("auto_start_apps_with_agent") or {})

    launch_paths = {key: value for key, value in launch_paths.items() if not str(key).upper().startswith("MAA")}
    autostart = {key: value for key, value in autostart.items() if not str(key).upper().startswith("MAA")}
    for index, raw_path in enumerate(maa_directories, start=1):
        label = f"MAA{index}"
        directory = Path(raw_path).expanduser().resolve()
        instance_dirs[label] = str(directory)
        gui_paths[label] = str(directory / "config" / "gui.new.json")
        log_paths.append(str(directory / "debug" / "asst.log"))
        aliases[label] = ""
        launch_paths[label] = str(directory / "MAA.exe")
        autostart[label] = False

    config["maa_instance_dirs"] = instance_dirs
    config["maa_gui_config_paths"] = gui_paths
    config["maa_log_paths"] = log_paths
    config["maa_instance_aliases"] = aliases
    config["launch_paths"] = launch_paths
    config["auto_start_apps_with_agent"] = autostart
    config["enable_bark"] = bool(enable_bark)
    config["bark_key"] = bark_key.strip() if enable_bark else ""
    config["setup_completed"] = True
    return config


def save_config(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    json.loads(temporary.read_text(encoding="utf-8"))
    temporary.replace(path)


class FirstRunWizard:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.template_path = config_template_path(self.base_dir)
        self.config_path = config_path(self.base_dir)
        self.completed = False
        self.root = tk.Tk()
        self.root.title("欢迎使用 MAA Agent")
        self.root.geometry("760x620")
        self.root.minsize(700, 560)
        self.root.configure(background="#f3f6fa")
        self.instance_count = tk.IntVar(value=1)
        self.maa1_path = tk.StringVar()
        self.maa2_path = tk.StringVar()
        self.enable_bark = tk.BooleanVar(value=False)
        self.bark_key = tk.StringVar()
        self._build()

    def _build(self):
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Wizard.TFrame", background="#f3f6fa")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("Title.TLabel", background="#f3f6fa", foreground="#152238", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Body.TLabel", background="#f3f6fa", foreground="#526174", font=("Microsoft YaHei UI", 10))
        style.configure("Card.TLabel", background="#ffffff", foreground="#253247")
        style.configure("Link.TLabel", background="#f3f6fa", foreground="#1769aa")

        body = ttk.Frame(self.root, style="Wizard.TFrame", padding=28)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="第一次使用，只需要几步", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            body,
            text="先告诉我 MAA 在哪里。MAA Agent 会自动准备其余基础配置，之后可随时在设置页调整。",
            style="Body.TLabel",
            wraplength=690,
        ).pack(anchor="w", pady=(8, 6))
        maa_link = ttk.Label(body, text="还没有安装 MAA？打开 MAA 官方安装说明 →", style="Link.TLabel", cursor="hand2")
        maa_link.pack(anchor="w", pady=(0, 18))
        maa_link.bind("<Button-1>", lambda _event: webbrowser.open(MAA_INSTALL_URL))

        card = ttk.Frame(body, style="Card.TFrame", padding=18)
        card.pack(fill="x")
        ttk.Label(card, text="你要管理几个 MAA？", style="Card.TLabel", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(card, text="一个（推荐）", variable=self.instance_count, value=1, command=self._toggle_second).grid(row=1, column=0, sticky="w", pady=(10, 12))
        ttk.Radiobutton(card, text="两个", variable=self.instance_count, value=2, command=self._toggle_second).grid(row=1, column=1, sticky="w", pady=(10, 12))
        self._path_row(card, 2, "MAA 安装目录", self.maa1_path)
        self.second_label = ttk.Label(card, text="第二个 MAA 目录", style="Card.TLabel")
        self.second_entry = ttk.Entry(card, textvariable=self.maa2_path)
        self.second_button = ttk.Button(card, text="浏览…", command=lambda: self._browse(self.maa2_path))
        card.columnconfigure(1, weight=1)

        bark = ttk.Frame(body, style="Card.TFrame", padding=18)
        bark.pack(fill="x", pady=(14, 0))
        ttk.Checkbutton(bark, text="使用 Bark 接收手机提醒（可选）", variable=self.enable_bark, command=self._toggle_bark).grid(row=0, column=0, columnspan=2, sticky="w")
        self.bark_label = ttk.Label(bark, text="Bark Key", style="Card.TLabel")
        self.bark_entry = ttk.Entry(bark, textvariable=self.bark_key, show="•")
        bark.columnconfigure(1, weight=1)

        ttk.Label(body, text="高级配置仍可在程序的“设置 / 全部配置”中修改。", style="Body.TLabel").pack(anchor="w", pady=(14, 8))
        actions = ttk.Frame(body, style="Wizard.TFrame")
        actions.pack(fill="x", side="bottom")
        ttk.Button(actions, text="高级：手动编辑 config.json", command=self._manual_setup).pack(side="left")
        ttk.Button(actions, text="保存并开始使用", command=self._finish).pack(side="right")
        self._toggle_second()
        self._toggle_bark()

    def _path_row(self, master, row: int, label: str, variable: tk.StringVar):
        ttk.Label(master, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Entry(master, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=10, pady=6)
        ttk.Button(master, text="浏览…", command=lambda: self._browse(variable)).grid(row=row, column=2, pady=6)

    def _browse(self, variable: tk.StringVar):
        selected = filedialog.askdirectory(parent=self.root, title="选择包含 MAA.exe 的文件夹")
        if selected:
            variable.set(selected)

    def _toggle_second(self):
        if self.instance_count.get() == 2:
            self.second_label.grid(row=3, column=0, sticky="w", pady=6)
            self.second_entry.grid(row=3, column=1, sticky="ew", padx=10, pady=6)
            self.second_button.grid(row=3, column=2, pady=6)
        else:
            self.second_label.grid_remove()
            self.second_entry.grid_remove()
            self.second_button.grid_remove()

    def _toggle_bark(self):
        if self.enable_bark.get():
            self.bark_label.grid(row=1, column=0, sticky="w", pady=(12, 0))
            self.bark_entry.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=(12, 0))
        else:
            self.bark_label.grid_remove()
            self.bark_entry.grid_remove()

    def _selected_directories(self) -> list[Path]:
        values = [self.maa1_path.get().strip()]
        if self.instance_count.get() == 2:
            values.append(self.maa2_path.get().strip())
        directories = []
        for value in values:
            directory = Path(value) if value else Path()
            if not value or not directory.is_dir() or not (directory / "MAA.exe").is_file():
                raise ValueError(f"请选择包含 MAA.exe 的有效目录：{value or '尚未选择'}")
            directories.append(directory)
        return directories

    def _finish(self):
        try:
            directories = self._selected_directories()
            if self.enable_bark.get() and not self.bark_key.get().strip():
                raise ValueError("启用 Bark 时需要填写 Bark Key。")
            template = json.loads(self.template_path.read_text(encoding="utf-8"))
            config = build_first_run_config(
                template,
                directories,
                enable_bark=self.enable_bark.get(),
                bark_key=self.bark_key.get(),
            )
            save_config(self.config_path, config)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror("还差一点", str(exc), parent=self.root)
            return
        self.completed = True
        self.root.destroy()

    def _manual_setup(self):
        try:
            if not self.config_path.exists():
                shutil.copy2(self.template_path, self.config_path)
            subprocess.Popen(["notepad.exe", str(self.config_path)])
        except OSError as exc:
            messagebox.showerror("无法打开配置", str(exc), parent=self.root)
            return
        messagebox.showinfo(
            "已打开手动配置",
            "保存 config.json 后，请重新双击 MAA-Agent-GUI.bat。",
            parent=self.root,
        )
        self.root.destroy()

    def run(self) -> bool:
        self.root.mainloop()
        return self.completed


def run_first_setup(base_dir: Path) -> bool:
    return FirstRunWizard(base_dir).run()
