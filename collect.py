# -*- coding: utf-8 -*-
"""
中国政府采购网 中央公告 采集器
只抓公开页面，不登录、不绕验证码、不碰个人信息。
输出结构化 JSON：项目名 / 采购单位 / 预算 / 截止时间 / 开标时间 / 联系人 / 正文摘要。
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

BASE = "http://www.ccgp.gov.cn"
LIST_PATH = "/cggg/zygg/"          # 中央公告列表
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

OUT_DIR = Path(__file__).parent / "data"
OUT_DIR.mkdir(exist_ok=True)


def get(url, referer=None, retries=3):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=h, timeout=25)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as e:          # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def parse_list(html, list_url):
    """列表页 -> [(标题, 绝对URL)]，相对链接按列表页所在目录解析"""
    soup = BeautifulSoup(html, "lxml")
    items = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        title = a.get_text(strip=True)
        if not re.search(r"\.htm$", href):
            continue
        if len(title) < 8:
            continue
        if href.startswith("http"):
            url = href
        elif href.startswith("/"):
            url = BASE + href
        elif href.startswith("./"):
            url = urljoin(list_url, href[2:])
        else:
            url = urljoin(list_url, href)
        items.append((title, url))
    # 去重保序
    seen, out = set(), []
    for t, u in items:
        if u in seen:
            continue
        seen.add(u)
        out.append((t, u))
    return out


def _txt(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).replace("\xa0", " ").strip()


def classify(url, title):
    """公告类型：招标/磋商/谈判=商机；中标/成交/结果=已结束；废标/更正/其他=参考"""
    path = url.lower()
    t = title or ""
    if re.search(r"/(zbgg|cjgg|jggg)/", path) or re.search(r"(中标|成交|结果)公告", t):
        return "结果公告"
    if re.search(r"/(gkzb|jzxcs|jzxtp|jzxdy|xjgg|xjtp)/", path) or re.search(
            r"(招标公告|竞争性磋商|竞争性谈判|询价公告|邀请招标)", t):
        return "招标公告"
    if re.search(r"(废标|终止|更正|澄清|修改)公告", t):
        return "更正废标"
    return "其他"


DATE_RE = re.compile(r"(20\d{2})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?")
# 正文里“截止时间：2026年10月30日 14点30分”这类写法（表格里没有时兜底）
BODY_DEADLINE_RE = re.compile(
    r"(?:截止时间|递交截止|提交截止|响应截止)\s*[:：]\s*"
    r"(20\d{2}\s*[年\-/]\s*\d{1,2}\s*[月\-/]\s*\d{1,2}\s*日?)")


def norm_date(s):
    """从一段文字里抽出第一个合法日期，返回 ISO 日期；抽不到返回空串"""
    if not s:
        return ""
    m = DATE_RE.search(s)
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_detail(html, url):
    """详情页 -> 结构化字段"""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # 公告概要表：政府采购网详情页是 <table> 键值对
    kv = {}
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            if len(cells) < 2:
                continue
            for i in range(0, len(cells) - 1, 2):
                k = _txt(cells[i]).rstrip("：:")
                v = _txt(cells[i + 1])
                if k and v and k not in kv and len(k) <= 20:
                    kv[k] = v

    body = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))

    def pick(*keys):
        for k in keys:
            if kv.get(k):
                return kv[k]
        for k in keys:
            m = re.search(re.escape(k) + r"\s*[:：]\s*([^\n]{1,80})", body)
            if m:
                return m.group(1).strip()
        return ""

    def money(s):
        """￥1105.130000万元 -> 11051300.0 元；无有效金额返回 None"""
        if not s:
            return None
        s = s.replace(",", "").replace("，", "")
        m = re.search(r"([\d.]+)\s*(万元|元)", s)
        if not m:
            return None
        try:
            v = float(m.group(1))
        except ValueError:
            return None
        if v <= 0:
            return None
        return round(v * 10000 if m.group(2) == "万元" else v, 2)

    title = pick("采购项目名称", "项目名称")
    if not title:
        h = soup.find(["h1", "h2"])
        title = _txt(h) if h else ""

    kind = classify(url, title)

    budget_raw = pick("预算金额", "采购预算", "项目预算", "最高限价")
    win_raw = pick("中标金额", "成交金额", "中标（成交）金额", "总中标金额")
    deadline_raw = pick("投标截止时间", "响应文件递交截止时间", "递交响应文件截止时间",
                        "提交投标文件截止时间", "响应文件提交截止时间",
                        "开标时间", "响应文件开启时间", "开启时间", "报价截止时间")
    if not deadline_raw:
        m = BODY_DEADLINE_RE.search(body)
        if m:
            deadline_raw = m.group(1)

    return {
        "url": url,
        "kind": kind,
        "title": title,
        "agency": pick("采购单位", "采购人", "招标人"),
        "agency_addr": pick("采购单位地址"),
        "agency_contact": pick("采购单位联系方式", "联系方式"),
        "agent": pick("代理机构名称", "采购代理机构"),
        "agent_contact": pick("代理机构联系方式"),
        "budget_raw": budget_raw,
        "budget_cny": money(budget_raw),
        "win_raw": win_raw,
        "win_cny": money(win_raw),
        "winner": pick("中标供应商", "成交供应商", "中标（成交）供应商", "供应商名称"),
        "deadline_raw": deadline_raw,
        "deadline": norm_date(deadline_raw),
        "open_time": pick("开标时间"),
        "open_place": pick("开标地点"),
        "doc_price": pick("招标文件售价"),
        "region": pick("行政区域"),
        "category": pick("品目"),
        "publish_time": pick("公告时间"),
        "project_no": pick("项目编号"),
        "body_len": len(body),
        "body_head": body[:600],
    }


def main(pages=1, limit=None):
    started = datetime.now(timezone.utc).isoformat()
    rows, errors = [], []

    for p in range(pages):
        url = BASE + LIST_PATH if p == 0 else f"{BASE}{LIST_PATH}index_{p}.htm"
        try:
            lst = parse_list(get(url), url)
        except Exception as e:                      # noqa: BLE001
            errors.append({"stage": "list", "url": url, "error": str(e)})
            continue

        for title, durl in lst:
            if limit and len(rows) >= limit:
                break
            try:
                d = parse_detail(get(durl, referer=url), durl)
                d["list_title"] = title
                d["fetched_at"] = datetime.now(timezone.utc).isoformat()
                rows.append(d)
            except Exception as e:                  # noqa: BLE001
                errors.append({"stage": "detail", "url": durl, "error": str(e)})
            time.sleep(0.8)                         # 礼貌限速

    result = {
        "source": "中国政府采购网 中央公告",
        "source_url": BASE + LIST_PATH,
        "collected_at": started,
        "count": len(rows),
        "errors": errors,
        "items": rows,
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"ccgp_{stamp}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[ok] 采集 {len(rows)} 条，错误 {len(errors)} 个 -> {out}")
    tenders = [r for r in rows if r["kind"] == "招标公告"]
    filled = sum(1 for r in tenders if r["budget_cny"])
    dl = sum(1 for r in tenders if r["deadline"])
    print(f"[stat] 招标公告 {len(tenders)} 条；预算命中 {filled}/{len(tenders)}；截止日命中 {dl}/{len(tenders)}")
    for r in rows[:6]:
        print(f"  - [{r['kind']}] {r['title'][:34]} | 预算 {r['budget_cny']} | 截止 {r['deadline']}")
    if errors:
        for e in errors[:5]:
            print("[err]", e["url"], e["error"][:80])
    return 0 if rows else 1


if __name__ == "__main__":
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    raise SystemExit(main(pages, limit))
