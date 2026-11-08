import argparse
import sys
import time
from dataclasses import dataclass

import agent


@dataclass(frozen=True)
class DrillCase:
    key: str
    title: str
    body: str
    level: str = "timeSensitive"


def _now():
    return agent.now_str()


def build_cases():
    """User-facing notification catalogue.

    These are synthetic messages only. They intentionally do not call the
    detector functions that could mutate production state or touch MAA/MuMu.
    """
    return [
        DrillCase(
            "agent_started",
            "MAA Agent 已启动",
            f"时间：{_now()}\n初始状态：\nMAA：MAA1运行 / MAA2运行\nMuMu：已运行\n内存：42%（已用 27.2 GB / 可用 36.8 GB）\n系统代理：已检测到系统代理",
            "active",
        ),
        DrillCase(
            "duplicate_start",
            "MAA Agent 未重复启动",
            "检测到已有一个 MAA Agent 正在运行，本次启动已自动退出。\n正在运行：D:\\MAA-Agent_0920\\agent.py",
            "active",
        ),
        DrillCase(
            "agent_stopped",
            "MAA Agent 已停止",
            f"时间：{_now()}\n这是你手动请求停止后的正常退出。",
            "active",
        ),
        DrillCase(
            "start_bat_missing",
            "MAA 自动启动脚本不可用",
            "MAA 的自动启动脚本没有找到。\n你要做什么：检查 Agent 配置中的 MAA 启动脚本路径。",
        ),
        DrillCase(
            "start_bat_failed",
            "MAA 自动启动失败",
            "MAA 的自动启动脚本执行失败。\n你要做什么：远程确认启动脚本和 MAA 状态。\n详细错误已记录到本机 agent.log。",
        ),
        DrillCase(
            "maa_instance_missing",
            "MAA1 已退出",
            "发生了什么：当前没有检测到 MAA1 的 MAA 进程。\n大概率原因：对应 MAA 被关掉、崩溃，或没有被启动脚本拉起。\n你要做什么：如果不是你主动关闭的，现在值得远程看一眼。",
        ),
        DrillCase(
            "maa_count_missing",
            "有 MAA 进程掉了",
            "发生了什么：应有 2 个 MAA，目前只检测到 1 个。\n无法可靠判断具体是 MAA1 还是 MAA2，因此不点名实例。\n你要做什么：如果不是手动关闭，远程检查两个 MAA。",
        ),
        DrillCase(
            "hung_window",
            "MAA/MuMu 持续未响应",
            "发生了什么：窗口“MAA”长时间没有恢复响应。\n大概率原因：MAA/MuMu 界面或进程可能卡住。\n你要做什么：如果不是正在进行明显的重负载操作，远程确认对应程序。",
            "critical",
        ),
        DrillCase(
            "disk_low",
            "备用机磁盘空间很低",
            "发生了什么：D:\\ 当前只剩 8.6GB。\n你要做什么：建议在下一次方便时清理，避免 MAA/模拟器/游戏更新因磁盘满失败。",
        ),
        DrillCase(
            "memory_pressure",
            "备用机内存持续吃紧",
            "发生了什么：系统内存持续吃紧，当前 96%，已用 62.8GB，可用 1.2GB。\n可能原因：MuMu 合计占用偏高（10.4GB）。\n当前内存大户：MuMuVMMHeadless.exe 8.1GB；GTA5.exe 6.2GB。\n你要做什么：如果这不是你主动运行游戏等重负载造成的，检查是否有程序卡死或异常占用。",
        ),
        DrillCase(
            "login_conflict",
            "MAA1：游戏账号疑似被顶/登录失效",
            "发生了什么：启动执行时登录状态异常。\n大概率原因：账号在另一台设备登录、登录态过期，或游戏把当前会话踢下线。\n你要做什么：如果你刚在别处登录过可忽略；否则远程看游戏登录界面。",
        ),
        DrillCase(
            "game_update",
            "MAA1：版本更新/维护阻断任务",
            "发生了什么：启动遇到版本更新或维护信号。\n大概率原因：官方更新、强制资源下载或维护。\n你要做什么：完成更新或等待维护结束；观察下一轮。",
        ),
        DrillCase(
            "maa_internal",
            "MAA1：MAA 初始化异常",
            "发生了什么：启动出现 MAA 初始化异常。\n大概率原因：MAA 自身、连接初始化或运行环境异常。\n你要做什么：现在值得远程检查 MAA 与 MuMu。",
        ),
        DrillCase(
            "crash",
            "MAA1：MAA / 游戏疑似崩溃",
            "发生了什么：自动清理智附近出现崩溃信号。\n大概率原因：MAA、MuMu 或游戏异常退出。\n你要做什么：远程确认对应进程状态。",
            "critical",
        ),
        DrillCase(
            "adb_connection",
            "MAA1：MuMu / ADB 连接持续异常",
            "发生了什么：自动清理智附近出现 MuMu/ADB 控制连接失败，之后一直没有恢复正常进展。\n大概率原因：模拟器连接异常或 MuMu/ADB 卡住。\n你要做什么：远程检查 MuMu/ADB 与游戏状态。",
        ),
        DrillCase(
            "game_network",
            "MAA1：游戏/网络持续异常",
            "发生了什么：自动清理智附近出现网络连接失败，之后一直没有恢复正常进展。\n大概率原因：代理、网络或游戏服务器异常。\n你要做什么：检查网络、代理和游戏当前状态。",
        ),
        DrillCase(
            "task_failed",
            "MAA1：任务持续失败",
            "发生了什么：基建没有正常完成，之后一直没有恢复正常进展。\n大概率原因：界面识别、游戏状态或连接出现异常。\n你要做什么：远程检查当前游戏画面和 MAA 状态。",
        ),
        DrillCase(
            "schedule_missed",
            "计划任务可能没跑｜MAA1",
            "发生了什么：以下计划到现在仍没有观察到实际启动：MAA1 16:00。\n大概率原因：计划没有触发、MAA/MuMu 已退出，或 MAA 当时正忙。\n你要做什么：如果不是你有意跳过，现在值得远程看一眼。",
        ),
        DrillCase(
            "proxy_failure",
            "备用机代理/外网出口异常",
            "发生了什么：系统代理已经无法正常访问外网。\n大概率原因：代理客户端退出、节点失效、本地代理端口失联，或外网出口异常。\n你要做什么：如果这台机本来就应该走代理，现在值得远程检查代理客户端。",
        ),
        DrillCase(
            "copilot_success",
            "MAA1 自动战斗完成",
            f"MAA1 的 Copilot 自动战斗已完成。\n时间：{_now()}",
            "active",
        ),
        DrillCase(
            "copilot_failure",
            "MAA1 自动战斗失败",
            f"MAA1 的 Copilot 自动战斗没有正常完成。\n你要做什么：远程检查当前游戏画面和 MAA 状态。\n时间：{_now()}",
        ),
        DrillCase(
            "agent_error",
            "MAA Agent 自身异常",
            "Agent 自身遇到异常，但仍会继续尝试下一轮监控。\n你要做什么：如果后续仍反复出现，检查本机 logs\\agent.log。",
            "critical",
        ),
        DrillCase(
            "reboot",
            "MAA Agent 准备重启电脑",
            f"原因：测试计划重启\n时间：{_now()}\n30 秒后重启。\n\n【演习说明】这条测试通知不会真的执行 shutdown。",
        ),
        DrillCase(
            "daily_report",
            "MAA Agent 日报｜测试样例",
            "总体：无需处理\n计划：8/8 已确认启动\n资源：RAM P95 58%，峰值 71%；CPU P95 64%\nMAA/MuMu：占用正常\n自动恢复/低价值异常：2 类，共 4 次\n仍未解决：无",
            "passive",
        ),
    ]


def render(case, index=None, total=None):
    prefix = "🧪 [通知演习]"
    if index is not None and total is not None:
        prefix = f"🧪 [通知演习 {index}/{total}]"
    return f"{prefix} {case.title}", (
        "这是测试通知，不代表机器真的发生了对应故障。\n\n" + case.body
    )


def send_case(case, index, total, dry_run=False):
    title, body = render(case, index, total)
    print(f"[{index:02d}/{total:02d}] {case.key}: {case.title}")
    if dry_run:
        print("    DRY-RUN: not sent")
        return True

    payload = agent.bark_payload(title, body, case.level)
    parts = agent.split_payload_if_needed(payload)
    all_ok = True
    for part_no, part in enumerate(parts, 1):
        ok, permanent, err = agent.post_bark_once(part, attempts=1)
        if not ok:
            all_ok = False
            kind = "permanent" if permanent else "temporary"
            print(f"    FAIL part {part_no}/{len(parts)} ({kind}): {err}")
        else:
            print(f"    OK part {part_no}/{len(parts)}")
    return all_ok


def run_split_test(dry_run=False):
    case = DrillCase(
        "split_transport",
        "Bark 长消息自动分段测试",
        "这是一段专门用于验证 Bark 自动分段的测试内容。\n" + ("0123456789 测试分段 ABCDEFGHIJ\n" * 220),
        "active",
    )
    title, body = render(case)
    payload = agent.bark_payload(title, body, case.level)
    parts = agent.split_payload_if_needed(payload)
    print(f"Long-message split produced {len(parts)} Bark payload(s).")
    if dry_run:
        for i, p in enumerate(parts, 1):
            print(f"  part {i}: {agent.payload_size_bytes(p)} bytes")
        return True
    ok_all = True
    for i, p in enumerate(parts, 1):
        ok, permanent, err = agent.post_bark_once(p, attempts=1)
        print(f"  part {i}/{len(parts)}: {'OK' if ok else 'FAIL'} {err or ''}")
        ok_all &= ok
    return ok_all


def parse_args():
    ap = argparse.ArgumentParser(description="MAA Agent Bark notification drill")
    ap.add_argument("--list", action="store_true", help="list notification case IDs")
    ap.add_argument("--dry-run", action="store_true", help="preview without sending Bark")
    ap.add_argument("--only", default="", help="comma-separated case IDs")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between notifications")
    ap.add_argument("--split-test", action="store_true", help="also test Bark long-message splitting")
    return ap.parse_args()


def main():
    args = parse_args()
    cases = build_cases()
    by_key = {c.key: c for c in cases}

    if args.list:
        for c in cases:
            print(f"{c.key:22s} {c.title}")
        return 0

    if args.only:
        requested = [x.strip() for x in args.only.split(",") if x.strip()]
        unknown = [x for x in requested if x not in by_key]
        if unknown:
            print("Unknown case(s): " + ", ".join(unknown), file=sys.stderr)
            return 2
        selected = [by_key[x] for x in requested]
    else:
        selected = cases

    print("=" * 72)
    print("MAA Agent notification drill")
    print(f"Target Bark server: {agent.cfg.get('bark_server', '(missing)')}")
    print(f"Cases: {len(selected)} | dry-run={args.dry_run}")
    print("The drill does NOT stop MAA, fill RAM, break the proxy, reboot Windows,")
    print("write alert cooldowns, or create production incidents.")
    print("=" * 72)

    failed = []
    total = len(selected)
    for i, case in enumerate(selected, 1):
        if not send_case(case, i, total, args.dry_run):
            failed.append(case.key)
        if i < total and args.delay > 0:
            time.sleep(args.delay)

    if args.split_test:
        if not run_split_test(args.dry_run):
            failed.append("split_transport")

    print("=" * 72)
    if failed:
        print("DRILL FAILED: " + ", ".join(failed))
        print("No failed drill message was added to the production Bark retry queue.")
        return 1
    print("DRILL PASSED: all selected notifications were accepted by Bark.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
