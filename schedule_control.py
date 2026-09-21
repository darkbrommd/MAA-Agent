import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

from schedule_utils import (
    add_override,
    base_occurrences,
    effective_occurrences,
    load_overrides,
    remove_override,
)

BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def parse_dt(text):
    text = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError("时间格式应为 YYYY-MM-DD HH:MM")


def parse_move_target(text, original_dt):
    text = str(text).strip()
    if len(text) == 5 and ":" in text:
        h, m = [int(x) for x in text.split(":", 1)]
        return original_dt.replace(hour=h, minute=m, second=0, microsecond=0)
    return parse_dt(text)


def validate_base_occurrence(cfg, label, original_dt):
    start = original_dt - timedelta(minutes=1)
    end = original_dt + timedelta(minutes=1)
    rows, _ = base_occurrences(BASE_DIR, cfg, start, end)
    return any(x["label"] == label and x["original_datetime"] == original_dt for x in rows)


def cmd_list(args):
    cfg = load_config()
    now = datetime.now()
    start = now - timedelta(hours=2) if args.all else now
    end = now + timedelta(hours=args.hours)
    rows, meta = effective_occurrences(BASE_DIR, cfg, start, end, include_skipped=True)
    print("MAA Agent 一次性计划覆盖（仅调整 Agent 的监督/日报预期）")
    print("实际 MAA 定时来自 MAA 自己的 gui.new.json；若你在 MAA 里改时间，Agent 会自动跟随。\n")
    for label in sorted(meta):
        m = meta[label]
        print(f"{label}: source={m.get('source')} path={m.get('path') or '-'}")
    print()
    if not rows:
        print("未来窗口内没有启用的计划。")
        return
    for row in rows:
        ov = row.get("override")
        tag = ""
        if ov:
            if ov.get("action") == "skip":
                tag = f"  [跳过本次 / override={ov.get('id')}]"
            elif ov.get("action") == "move":
                tag = f"  [原 {row['original_datetime'].strftime('%m-%d %H:%M')} -> 改到本次 / override={ov.get('id')}]"
        src = "MAA" if str(row.get("source", "")).startswith("maa_gui") else "fallback"
        when = row["datetime"].strftime("%Y-%m-%d %H:%M")
        print(f"{row['label']:4} {when}  ({src}){tag}")


def cmd_skip(args):
    cfg = load_config()
    original = parse_dt(args.original)
    if not validate_base_occurrence(cfg, args.label, original):
        raise SystemExit(f"找不到这个默认计划：{args.label} {original:%Y-%m-%d %H:%M}。先运行 list 查看实际 MAA 定时。")
    row = add_override(BASE_DIR, cfg, args.label, original, "skip", note=args.note or "")
    print(f"已记录：{args.label} {original:%Y-%m-%d %H:%M} 本次不纳入 Agent 漏跑判断。override={row['id']}")


def cmd_move(args):
    cfg = load_config()
    original = parse_dt(args.original)
    target = parse_move_target(args.new, original)
    if not validate_base_occurrence(cfg, args.label, original):
        raise SystemExit(f"找不到这个默认计划：{args.label} {original:%Y-%m-%d %H:%M}。先运行 list 查看实际 MAA 定时。")
    row = add_override(BASE_DIR, cfg, args.label, original, "move", new_dt=target, note=args.note or "")
    print(f"已记录：{args.label} 本次监督从 {original:%Y-%m-%d %H:%M} 改为 {target:%Y-%m-%d %H:%M}。override={row['id']}")


def cmd_clear(args):
    cfg = load_config()
    removed = remove_override(BASE_DIR, cfg, override_id=args.id)
    if removed:
        print(f"已撤销 override={args.id}")
    else:
        print(f"没有找到 override={args.id}")


def run_gui():
    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog, ttk
    except Exception as exc:
        print(f"Tkinter GUI 不可用：{exc}")
        print("可使用 CLI：python schedule_control.py list")
        return 2

    cfg = load_config()
    root = tk.Tk()
    root.title("MAA Agent - 本次计划例外")
    root.geometry("900x520")
    root.minsize(760, 430)

    title = ttk.Label(root, text="一次性计划例外", font=("Microsoft YaHei UI", 14, "bold"))
    title.pack(anchor="w", padx=14, pady=(12, 2))
    note = ttk.Label(
        root,
        text=("这里修改的是 Agent 对‘这一轮是否应该发生’的监督预期，不会偷偷改 MAA 本体。"
              "\n默认时刻会优先从每个 MAA 的 config\\gui.new.json 自动读取；你在 MAA 自己的‘定时执行’里改默认时间后，Agent 会自动跟随。"),
        wraplength=850,
        justify="left",
    )
    note.pack(anchor="w", padx=14, pady=(0, 10))

    columns = ("label", "original", "effective", "source", "exception")
    tree = ttk.Treeview(root, columns=columns, show="headings", selectmode="browse")
    headings = {
        "label": "实例", "original": "默认本次", "effective": "Agent 当前预期", "source": "默认来源", "exception": "一次性例外"
    }
    widths = {"label": 75, "original": 165, "effective": 165, "source": 105, "exception": 270}
    for col in columns:
        tree.heading(col, text=headings[col])
        tree.column(col, width=widths[col], anchor="center" if col != "exception" else "w")
    tree.pack(fill="both", expand=True, padx=14, pady=4)

    status_var = tk.StringVar(value="")
    status = ttk.Label(root, textvariable=status_var)
    status.pack(anchor="w", padx=14, pady=(2, 4))

    row_cache = {}

    def refresh():
        nonlocal cfg
        cfg = load_config()
        for item in tree.get_children():
            tree.delete(item)
        row_cache.clear()
        now = datetime.now()
        rows, meta = effective_occurrences(BASE_DIR, cfg, now - timedelta(hours=2), now + timedelta(hours=72), include_skipped=True)
        for idx, row in enumerate(rows):
            ov = row.get("override")
            original = row["original_datetime"].strftime("%Y-%m-%d %H:%M")
            if ov and ov.get("action") == "skip":
                effective = "— 本次跳过监督 —"
                exc = f"跳过本次  ({ov.get('id')})"
            else:
                effective = row["datetime"].strftime("%Y-%m-%d %H:%M")
                exc = ""
                if ov and ov.get("action") == "move":
                    exc = f"改到 {effective}  ({ov.get('id')})"
            src = "MAA 实时配置" if str(row.get("source", "")).startswith("maa_gui") else "config fallback"
            iid = f"r{idx}"
            tree.insert("", "end", iid=iid, values=(row["label"], original, effective, src, exc))
            row_cache[iid] = row
        source_text = "；".join(f"{k}={v.get('source')}" for k, v in sorted(meta.items()))
        status_var.set(f"显示未来 72 小时。默认来源：{source_text or '无'}")

    def selected_row():
        sel = tree.selection()
        if not sel:
            messagebox.showinfo("MAA Agent", "先选中一条计划。")
            return None
        return row_cache.get(sel[0])

    def skip_once():
        row = selected_row()
        if not row:
            return
        original = row["original_datetime"]
        if original < datetime.now() - timedelta(hours=2):
            messagebox.showwarning("MAA Agent", "这条默认计划已经过去超过 2 小时，不再提供事后跳过。")
            return
        add_override(BASE_DIR, cfg, row["label"], original, "skip")
        refresh()

    def move_once():
        row = selected_row()
        if not row:
            return
        original = row["original_datetime"]
        if original < datetime.now() - timedelta(hours=2):
            messagebox.showwarning("MAA Agent", "这条默认计划已经过去超过 2 小时，不再提供事后改时。")
            return
        answer = simpledialog.askstring(
            "修改这一次",
            "输入新时间。\n同一天可只写 HH:MM；跨天写 YYYY-MM-DD HH:MM。",
            initialvalue=original.strftime("%H:%M"),
            parent=root,
        )
        if not answer:
            return
        try:
            target = parse_move_target(answer, original)
        except Exception as exc:
            messagebox.showerror("时间格式错误", str(exc))
            return
        if target < datetime.now() - timedelta(minutes=1):
            messagebox.showerror("时间无效", "新时间不能已经过去。")
            return
        add_override(BASE_DIR, cfg, row["label"], original, "move", new_dt=target)
        refresh()

    def clear_once():
        row = selected_row()
        if not row:
            return
        ov = row.get("override")
        if not ov:
            messagebox.showinfo("MAA Agent", "这条计划没有一次性例外。")
            return
        remove_override(BASE_DIR, cfg, override_id=ov.get("id"))
        refresh()

    buttons = ttk.Frame(root)
    buttons.pack(fill="x", padx=14, pady=(4, 12))
    ttk.Button(buttons, text="跳过这一次（Agent 不判漏跑）", command=skip_once).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text="修改这一次预期时间", command=move_once).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text="撤销一次性例外", command=clear_once).pack(side="left", padx=(0, 8))
    ttk.Button(buttons, text="刷新", command=refresh).pack(side="right")

    refresh()
    root.mainloop()
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="MAA Agent 一次性计划覆盖")
    sub = parser.add_subparsers(dest="cmd")
    p = sub.add_parser("list", help="列出未来有效计划")
    p.add_argument("--hours", type=int, default=72)
    p.add_argument("--all", action="store_true", help="同时显示过去两小时")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("skip", help="让 Agent 跳过某个本次计划的漏跑判断")
    p.add_argument("label")
    p.add_argument("original", help='例如 "2026-09-20 22:00"')
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_skip)

    p = sub.add_parser("move", help="修改某个本次计划的 Agent 预期时间")
    p.add_argument("label")
    p.add_argument("original")
    p.add_argument("new", help='HH:MM 或 "YYYY-MM-DD HH:MM"')
    p.add_argument("--note", default="")
    p.set_defaults(func=cmd_move)

    p = sub.add_parser("clear", help="撤销一次性覆盖")
    p.add_argument("id")
    p.set_defaults(func=cmd_clear)
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not args.cmd:
        return run_gui()
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
