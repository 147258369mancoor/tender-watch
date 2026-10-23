# -*- coding: utf-8 -*-
"""
关键词订阅匹配 + 多渠道推送（邮件 / Telegram / 企业微信 webhook）
配置从 config.json 读，没有配置就只输出到本地文件，不报错。

用法：
  python push.py                 # 用默认关键词跑一遍，打印将推送的内容
  python push.py --dry-run       # 只匹配不发送
"""
import argparse
import json
import os
import smtplib
import ssl
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

HERE = Path(__file__).parent
DATA = HERE / "data"
CFG = HERE / "config.json"


def load_cfg():
    if CFG.exists():
        try:
            return json.loads(CFG.read_text(encoding="utf-8"))
        except Exception as e:                       # noqa: BLE001
            print(f"[warn] config.json 解析失败：{e}，按空配置运行")
    return {}


def money_cn(v):
    if not v:
        return "—"
    if v >= 1e8:
        return f"{v/1e8:.2f} 亿"
    if v >= 1e4:
        return f"{v/1e4:.1f} 万"
    return f"{v:.0f} 元"


def days_left(dl):
    if not dl:
        return None
    try:
        y, m, d = map(int, dl.split("-"))
        return (date(y, m, d) - date.today()).days
    except Exception:
        return None


def match(item, subs):
    """返回命中的订阅名列表。subs: [{name, keywords, min_budget, max_days_left}]"""
    hits = []
    text = " ".join(str(item.get(k) or "") for k in
                    ("title", "category", "agency", "region", "body_head"))
    budget = item.get("budget_cny") or 0
    left = days_left(item.get("deadline"))

    for s in subs:
        kws = s.get("keywords") or []
        if kws and not any(k.lower() in text.lower() for k in kws):
            continue
        if s.get("min_budget") and budget < s["min_budget"]:
            continue
        if s.get("max_budget") and budget > s["max_budget"]:
            continue
        if s.get("max_days_left") is not None and left is not None:
            if left < 0 or left > s["max_days_left"]:
                continue
        hits.append(s.get("name", "未命名订阅"))
    return hits


def build_digest(matched, cfg):
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"【招标日报】{today}　共 {len(matched)} 条命中订阅", ""]
    for sub, items in matched.items():
        lines.append(f"■ {sub}（{len(items)} 条）")
        for r in items:
            left = days_left(r.get("deadline"))
            left_s = f"剩{left}天" if left is not None and left >= 0 else ""
            lines.append(f"  · {r.get('title')}")
            lines.append(f"    预算 {money_cn(r.get('budget_cny'))} | 截止 {r.get('deadline') or '—'} {left_s}")
            lines.append(f"    {r.get('agency') or '—'} | {r.get('url')}")
        lines.append("")
    return "\n".join(lines)


def send_telegram(text, cfg):
    tk = os.getenv("TG_BOT_TOKEN") or cfg.get("telegram_bot_token")
    chat = os.getenv("TG_CHAT_ID") or cfg.get("telegram_chat_id")
    if not (tk and chat):
        return "skipped(未配置)"
    try:
        r = requests.post(f"https://api.telegram.org/bot{tk}/sendMessage",
                          json={"chat_id": chat, "text": text[:4000],
                                "disable_web_page_preview": True}, timeout=20)
        return f"telegram {r.status_code}"
    except Exception as e:                           # noqa: BLE001
        return f"telegram FAIL {e}"


def send_wecom(text, cfg):
    url = os.getenv("WECOM_WEBHOOK") or cfg.get("wecom_webhook")
    if not url:
        return "skipped(未配置)"
    try:
        r = requests.post(url, json={"msgtype": "text", "text": {"content": text[:2000]}},
                          timeout=20)
        return f"wecom {r.status_code}"
    except Exception as e:                           # noqa: BLE001
        return f"wecom FAIL {e}"


def send_email(text, cfg, subject):
    host = cfg.get("smtp_host")
    user = cfg.get("smtp_user")
    pwd = os.getenv("SMTP_PASS") or cfg.get("smtp_pass")
    to = cfg.get("mail_to")
    if not (host and user and pwd and to):
        return "skipped(未配置)"
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = user
        msg["To"] = ", ".join(to) if isinstance(to, list) else to
        msg.attach(MIMEText(text, "plain", "utf-8"))
        port = int(cfg.get("smtp_port", 465))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=25) as s:
            s.login(user, pwd)
            s.sendmail(user, to if isinstance(to, list) else [to], msg.as_string())
        return "email ok"
    except Exception as e:                           # noqa: BLE001
        return f"email FAIL {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_cfg()
    subs = cfg.get("subscriptions") or [
        {"name": "默认（信息化/软件/设备）",
         "keywords": ["软件", "信息系统", "服务器", "数据", "网络安全", "平台", "系统集成"],
         "min_budget": 500000, "max_days_left": 30}
    ]

    src = json.loads((DATA / "latest.json").read_text(encoding="utf-8"))
    items = [r for r in src.get("items", []) if r.get("kind") == "招标公告"]

    matched = {}
    for r in items:
        for sub in match(r, subs):
            matched.setdefault(sub, []).append(r)
    for k in matched:
        matched[k].sort(key=lambda x: (x.get("budget_cny") or 0), reverse=True)

    digest = build_digest(matched, cfg)
    total = sum(len(v) for v in matched.values())
    print(f"[match] 招标 {len(items)} 条，命中订阅 {total} 条次")
    for sub, its in matched.items():
        print(f"  - {sub}: {len(its)} 条")

    out = HERE / "out" / f"digest_{datetime.now():%Y%m%d_%H%M}.txt"
    out.parent.mkdir(exist_ok=True)
    out.write_text(digest, encoding="utf-8")
    print(f"[out] {out}")

    if args.dry_run:
        print("\n--- 摘要预览 ---\n" + digest[:1200])
        return 0

    if total:
        print("[send]", send_telegram(digest, cfg))
        print("[send]", send_wecom(digest, cfg))
        print("[send]", send_email(digest, cfg,
                                   f"招标日报 {datetime.now():%Y-%m-%d} · {total} 条"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
