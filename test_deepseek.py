import json
from pathlib import Path
from openai import OpenAI


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    cfg = load_config()

    api_key = cfg.get("deepseek_api_key", "").strip()
    if not api_key:
        raise RuntimeError("config.json 里 deepseek_api_key 还是空的，请先填入 DeepSeek API Key。")

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.deepseek.com"
    )

    # 这是一段假的 MAA 异常日志，用来测试模型是否能按格式归类。
    fake_log = """
[2026-06-12 21:14:03][ERR] asst::Controller::connect failed
[2026-06-12 21:14:04][ERR] ADB connect timeout: 127.0.0.1:16384
[2026-06-12 21:14:05][WRN] task stopped because controller is unavailable
[2026-06-12 21:14:06][ERR] SubTaskError: StartUp task failed
"""

    system_prompt = (
        "你是 Windows 上的 MAA 明日方舟自动化运维日志分析器。"
        "你只能根据本次输入的日志判断，不允许编造日志中没有的信息。"
        "你不能输出排查长文，不能输出 Markdown。"
        "你必须只输出一个合法 JSON 对象。"
    )

    user_prompt = f"""
请分析下面这段 MAA 日志，并只返回 JSON。

JSON 格式必须是：
{{
  "level": "低|中|高",
  "category": "模拟器或ADB连接失败|MAA或游戏版本问题|作业失败|疑似干员或练度问题|理智耗尽|网络问题|窗口卡死或未响应|未知",
  "summary": "一句话说明最可能原因",
  "action": "一句话说明建议动作",
  "need_human": true,
  "confidence": 0.0
}}

规则：
1. 不要使用历史上下文。
2. 不要假设日志里没有出现的内容。
3. confidence 必须是 0 到 1 的数字。
4. 如果不确定，category 用“未知”，confidence 不要超过 0.5。
5. need_human 必须是 true 或 false。
6. 只输出 JSON，不要解释。

日志：
{fake_log}
"""

    response = client.chat.completions.create(
        model=cfg.get("deepseek_model", "deepseek-v4-flash"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0,
        max_tokens=180,
        extra_body={
            "thinking": {"type": "disabled"}
        }
    )

    content = response.choices[0].message.content.strip()

    print("===== DeepSeek 原始返回 =====")
    print(content)

    print("\n===== 尝试解析 JSON =====")
    try:
        data = json.loads(content)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        print("\n✅ 测试成功：API 可用，返回内容是合法 JSON。")
    except json.JSONDecodeError:
        print("❌ 测试失败：模型返回的不是合法 JSON。可以再运行一次，或把返回内容发我看。")

    print("\n===== Token 用量 =====")
    usage = getattr(response, "usage", None)
    if usage:
        print(usage)
    else:
        print("这次响应没有返回 usage 字段。")


if __name__ == "__main__":
    main()