import json
import requests
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE_DIR / "config" / "config.json"
if not CONFIG_PATH.exists():
    CONFIG_PATH = BASE_DIR / "config.json"

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    cfg = json.load(f)

url = cfg["bark_server"].rstrip("/") + "/push"

payload = {
    "device_key": cfg["bark_key"],
    "title": "MAA Agent 测试",
    "body": "Bark 推送正常。之后正常启动不会频繁通知，只有异常和每日汇报会通知。",
    "group": "MAA-Agent",
    "level": "active"
}

r = requests.post(url, json=payload, timeout=10)
print(r.status_code)
print(r.text)
