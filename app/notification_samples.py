from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationSample:
    label: str
    kind: str
    target: str
    title: str
    body: str
    level: str


SAMPLES = (
    NotificationSample(
        "MAA1 未运行", "maa_missing", "MAA1", "【测试】MAA1 未运行",
        "发生了什么：没有找到 MAA1。\n你要做什么：确认程序是否已经打开。", "timeSensitive",
    ),
    NotificationSample(
        "MAA2 未运行", "maa_missing", "MAA2", "【测试】MAA2 未运行",
        "发生了什么：没有找到 MAA2。\n你要做什么：确认第二个 MAA 是否已经打开。", "timeSensitive",
    ),
    NotificationSample(
        "Bark 通知", "bark_transport", "", "【测试】Bark 通知",
        "如果你收到了这条消息，说明 Bark 和手机通知可以正常使用。", "active",
    ),
    NotificationSample(
        "内存不足", "memory", "", "【测试】内存不足",
        "发生了什么：系统内存持续吃紧，当前 96%，可用 1.2 GB。\n"
        "你要做什么：如果不是主动运行重负载程序，请检查异常占用。", "timeSensitive",
    ),
    NotificationSample(
        "磁盘空间不足", "disk", "", "【测试】磁盘空间不足",
        "发生了什么：磁盘剩余空间已经很少。\n你要做什么：清理不再需要的文件。", "timeSensitive",
    ),
    NotificationSample(
        "代理连接异常", "proxy", "", "【测试】代理连接异常",
        "发生了什么：通过系统代理访问外部网站失败。\n"
        "你要做什么：检查代理软件是否正在运行，当前节点是否可用。", "timeSensitive",
    ),
    NotificationSample(
        "任务未按时开始", "schedule", "", "【测试】任务未按时开始",
        "发生了什么：MAA1 16:00 的计划到了时间仍没有开始。\n"
        "你要做什么：检查 MAA、MuMu 和定时任务状态。", "timeSensitive",
    ),
    NotificationSample(
        "窗口未响应", "hung", "", "【测试】窗口未响应",
        "发生了什么：MAA 或 MuMu 窗口持续未响应。\n你要做什么：确认对应程序是否卡住。", "critical",
    ),
    NotificationSample(
        "Agent 出错", "agent_error", "", "【测试】MAA Agent 出错",
        "发生了什么：MAA Agent 没有正常完成本轮检查。\n你要做什么：打开日志中心查看最近记录。", "critical",
    ),
    NotificationSample(
        "官网维护", "news", "", "【测试】官网维护",
        "发生了什么：官网发布了新的维护公告。\n你要做什么：打开公告中心查看详细内容。", "timeSensitive",
    ),
)

SAMPLES_BY_LABEL = {sample.label: sample for sample in SAMPLES}
SAMPLES_BY_CASE = {(sample.kind, sample.target): sample for sample in SAMPLES}


def sample_for_case(kind: str, target: str = "") -> NotificationSample:
    key = (str(kind), str(target or "") if kind == "maa_missing" else "")
    return SAMPLES_BY_CASE[key]


def general_samples() -> tuple[NotificationSample, ...]:
    return tuple(sample for sample in SAMPLES if sample.kind not in {"maa_missing", "bark_transport"})
