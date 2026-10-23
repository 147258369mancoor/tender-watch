# -*- coding: utf-8 -*-
"""
日报生成 + 增量去重 + 变化检测 + 健康自检
输入：data/latest.json（collect.py 产出）
输出：out/report_YYYYMMDD.html / .md，data/seen.json（已推送指纹），data/health.json
"""
import hashlib
import json
import re
from datetime import datetime, date
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / "data"
OUT = HERE / "out"
OUT.mkdir(exist_ok=True)

SEEN = DATA / "seen.json"
HEALTH = DATA / "health.json"


def fp(item):
    """指纹：项目编号优先，否则标题+采购单位"""
    base = item.get("project_no") or (item.get("title", "") + "|" + item.get("agency", ""))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def load_json(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def main():
    src = load_json(DATA / "latest.json", {})
    items = src.get("items", [])
    seen = load_json(SEEN, {})
    today = datetime.now().strftime("%Y%m%d")

    tenders = [r for r in items if r.get("kind") == "招标公告"]

    fresh, changed, expired = [], [], []
    for r in tenders:
        k = fp(r)
        rec = seen.get(k)
        if rec is None:
            fresh.append(r)
            seen[k] = {"first_seen": today, "last_seen": today,
                       "title": r.get("title"), "budget": r.get("budget_cny"),
                       "deadline": r.get("deadline")}
        else:
            rec["last_seen"] = today
            # 变化检测：预算或截止日变了
            if (rec.get("budget") != r.get("budget_cny")
                    or rec.get("deadline") != r.get("deadline")):
                changed.append({"item": r, "was": dict(rec)})
                rec["budget"] = r.get("budget_cny")
                rec["deadline"] = r.get("deadline")
                rec["changed_at"] = today

    # ---- 当日累积：已推送过但今天首次见到的也要留在日报里 ----
    # 同一自然日多次运行时，日报应包含"今天所有新增"，而不是"本次运行新增"。
    today_list = [r for r in tenders if seen.get(fp(r), {}).get("first_seen") == today]
    today_list.sort(key=lambda x: (x.get("budget_cny") or 0), reverse=True)

    # 按预算降序，让高价值排前面
    fresh.sort(key=lambda x: (x.get("budget_cny") or 0), reverse=True)

    # ---- 健康自检 ----
    health = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "list_ok": len(items) > 0,
        "detail_errors": len(src.get("errors", [])),
        "tender_count": len(tenders),
        "fresh_count": len(fresh),
        "budget_fill_rate": round(
            sum(1 for r in tenders if r.get("budget_cny")) / len(tenders), 3) if tenders else 0,
        "deadline_fill_rate": round(
            sum(1 for r in tenders if r.get("deadline")) / len(tenders), 3) if tenders else 0,
    }
    # 告警规则
    alerts = []
    if not health["list_ok"]:
        alerts.append("列表页返回空 —— 源站可能改版或被封")
    if health["tender_count"] == 0:
        alerts.append("招标公告为 0 条 —— 解析规则可能失效")
    if tenders and health["budget_fill_rate"] < 0.6:
        alerts.append(f"预算字段命中率跌到 {health['budget_fill_rate']:.0%} —— 疑似页面结构变化")
    if tenders and health["deadline_fill_rate"] < 0.6:
        alerts.append(f"截止日命中率跌到 {health['deadline_fill_rate']:.0%} —— 疑似页面结构变化")
    if health["detail_errors"] > max(3, len(items) * 0.3):
        alerts.append(f"详情页失败 {health['detail_errors']} 个 —— 需要检查")
    health["alerts"] = alerts
    health["status"] = "ALERT" if alerts else "OK"

    # ---- 生成 Markdown ----
    lines = [f"# 政府采购招标日报 · {today}", ""]
    lines.append(f"数据源：中国政府采购网 中央公告　采集 {len(items)} 条，"
                 f"其中招标类 {len(tenders)} 条，今日新增 {len(today_list)} 条"
                 f"（本次运行新增 {len(fresh)} 条）。")
    lines.append("")
    if alerts:
        lines.append("> ⚠️ 自检告警：" + "；".join(alerts))
        lines.append("")
    if changed:
        lines.append(f"## 有变化 {len(changed)} 条")
        for c in changed:
            it = c["item"]
            lines.append(f"- {it['title'][:60]}：预算 {money_cn(c['was'].get('budget'))} → "
                         f"{money_cn(it.get('budget_cny'))}，截止 {c['was'].get('deadline')} → {it.get('deadline')}")
        lines.append("")

    lines.append(f"## 今日新增招标 {len(today_list)} 条（按预算降序）")
    lines.append("")
    for r in today_list:
        dl = r.get("deadline", "")
        left = days_left(dl)
        left_s = f"（剩 {left} 天）" if left is not None and left >= 0 else ""
        lines.append(f"### {r.get('title') or '（无标题）'}")
        lines.append(f"- 预算：**{money_cn(r.get('budget_cny'))}**")
        lines.append(f"- 截止：{dl or '—'} {left_s}")
        lines.append(f"- 采购单位：{r.get('agency') or '—'}")
        lines.append(f"- 地区/品目：{r.get('region') or '—'} / {r.get('category') or '—'}　编号：{r.get('project_no') or '—'}")
        lines.append(f"- 原文：{r.get('url')}")
        lines.append("")

    md = "\n".join(lines)
    (OUT / f"report_{today}.md").write_text(md, encoding="utf-8")

    # ---- 生成 HTML ----
    rows_html = []
    for r in today_list:
        dl = r.get("deadline", "")
        left = days_left(dl)
        badge = ""
        if left is not None:
            cls = "hot" if left <= 7 else ("warm" if left <= 14 else "cold")
            badge = f'<span class="badge {cls}">剩 {left} 天</span>'
        rows_html.append(f"""
        <tr>
          <td class="budget">{money_cn(r.get('budget_cny'))}</td>
          <td>
            <div class="t"><a href="{r.get('url')}" target="_blank">{r.get('title') or '（无标题）'}</a></div>
            <div class="meta">{r.get('agency') or '—'} · {r.get('region') or '—'} · {r.get('category') or '—'}</div>
          </td>
          <td class="dl">{dl or '—'} {badge}</td>
        </tr>""")

    alert_html = ""
    if alerts:
        alert_html = '<div class="alert">⚠️ 自检告警：' + "；".join(alerts) + "</div>"

    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>政府采购招标日报 {today}</title>
<style>
:root {{ --bg:#0f1115; --card:#171a21; --line:#262b36; --fg:#e6e9ef; --mut:#8b93a7; --acc:#4da3ff; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:32px 20px; background:var(--bg); color:var(--fg);
  font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif; }}
.wrap {{ max-width:960px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 6px; }}
.sub {{ color:var(--mut); font-size:13px; margin-bottom:20px; }}
.alert {{ background:#3a2210; border:1px solid #7a4a1a; color:#ffcf9a;
  padding:10px 14px; border-radius:8px; margin-bottom:18px; font-size:13px; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
table {{ width:100%; border-collapse:collapse; }}
th,td {{ padding:12px 14px; text-align:left; border-bottom:1px solid var(--line); vertical-align:top; }}
th {{ font-size:12px; color:var(--mut); font-weight:600; letter-spacing:.04em; }}
tr:last-child td {{ border-bottom:none; }}
tr:hover td {{ background:#1c2029; }}
.budget {{ white-space:nowrap; font-weight:700; color:#7ee0a0; font-variant-numeric:tabular-nums; }}
.t a {{ color:var(--fg); text-decoration:none; font-weight:600; }}
.t a:hover {{ color:var(--acc); }}
.meta {{ color:var(--mut); font-size:12px; margin-top:4px; }}
.dl {{ white-space:nowrap; color:var(--mut); font-size:13px; }}
.badge {{ display:inline-block; margin-left:6px; padding:1px 7px; border-radius:99px; font-size:11px; }}
.hot {{ background:#4a1d1d; color:#ff9a9a; }}
.warm {{ background:#4a3a1d; color:#ffd39a; }}
.cold {{ background:#1d3a2a; color:#9ae0b8; }}
.empty {{ padding:40px; text-align:center; color:var(--mut); }}
.foot {{ margin-top:16px; color:var(--mut); font-size:12px; }}
</style></head><body><div class="wrap">
<h1>政府采购招标日报</h1>
<div class="sub">{today} · 数据源 中国政府采购网 中央公告 · 采集 {len(items)} 条 / 招标 {len(tenders)} 条 / 新增 {len(fresh)} 条</div>
{alert_html}
<div class="card">
<table><thead><tr><th style="width:110px">预算</th><th>项目</th><th style="width:170px">截止</th></tr></thead>
<tbody>{''.join(rows_html) or '<tr><td colspan="3" class="empty">今天没有新增招标公告</td></tr>'}</tbody></table>
</div>
<div class="foot">只收录公开页面信息，不含任何个人信息。生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}</div>
</div></body></html>"""
    (OUT / f"report_{today}.html").write_text(html, encoding="utf-8")

    SEEN.write_text(json.dumps(seen, ensure_ascii=False, indent=1), encoding="utf-8")
    HEALTH.write_text(json.dumps(health, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[report] 新增 {len(fresh)} / 变化 {len(changed)} / 招标共 {len(tenders)}")
    print(f"[health] {health['status']} 预算命中 {health['budget_fill_rate']:.0%} "
          f"截止命中 {health['deadline_fill_rate']:.0%}")
    for a in alerts:
        print("[ALERT]", a)
    print(f"[out] {OUT / ('report_' + today + '.html')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
