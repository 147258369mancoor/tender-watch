# -*- coding: utf-8 -*-
"""
多源招标公告采集器

数据源：
  - 中央公告  www.ccgp.gov.cn/cggg/zygg/
  - 地方公告  www.ccgp.gov.cn/cggg/dfgg/   ← 覆盖全国 31 省，详情页结构与中央一致

只抓公开页面：不登录、不绕验证码、不碰个人信息。
输出结构化 JSON：项目名 / 采购单位 / 省份 / 预算 / 截止日 / 项目编号 / 原文链接。
"""
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "http://www.ccgp.gov.cn"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}

OUT_DIR = Path(__file__).parent / "data"
OUT_DIR.mkdir(exist_ok=True)

# 数据源定义：name / 列表路径 / 区域标签
SOURCES = [
    {"name": "中央公告", "path": "/cggg/zygg/", "scope": "中央"},
    {"name": "地方公告", "path": "/cggg/dfgg/", "scope": "地方"},
]

# 34 个省级行政区，用于从详情页正文兜底识别省份
PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
    "香港", "澳门", "台湾",
]

# 常见地级市/区县 -> 省级。只覆盖高频项，其余走正文兜底。
CITY_TO_PROVINCE = {
    "石家庄": "河北", "唐山": "河北", "保定": "河北", "邯郸": "河北", "廊坊": "河北",
    "太原": "山西", "大同": "山西", "临汾": "山西", "运城": "山西",
    "呼和浩特": "内蒙古", "包头": "内蒙古", "鄂尔多斯": "内蒙古", "九原区": "内蒙古",
    "沈阳": "辽宁", "大连": "辽宁", "鞍山": "辽宁", "中山区": "辽宁",
    "长春": "吉林", "吉林市": "吉林", "延边": "吉林",
    "哈尔滨": "黑龙江", "齐齐哈尔": "黑龙江", "佳木斯": "黑龙江", "大庆": "黑龙江",
    "南京": "江苏", "苏州": "江苏", "无锡": "江苏", "常州": "江苏", "南通": "江苏",
    "徐州": "江苏", "扬州": "江苏", "射阳县": "江苏", "盐城": "江苏",
    "杭州": "浙江", "宁波": "浙江", "温州": "浙江", "嘉兴": "浙江", "绍兴": "浙江",
    "合肥": "安徽", "芜湖": "安徽", "蚌埠": "安徽", "安庆": "安徽",
    "福州": "福建", "厦门": "福建", "泉州": "福建", "莆田": "福建", "大田县": "福建",
    "南昌": "江西", "赣州": "江西", "九江": "江西", "上饶": "江西", "安义县": "江西",
    "济南": "山东", "青岛": "山东", "烟台": "山东", "潍坊": "山东", "临沂": "山东",
    "枣庄": "山东", "高新区": "山东",
    "郑州": "河南", "洛阳": "河南", "开封": "河南", "新乡": "河南",
    "武汉": "湖北", "宜昌": "湖北", "襄阳": "湖北", "黄石": "湖北",
    "长沙": "湖南", "株洲": "湖南", "湘潭": "湖南", "衡阳": "湖南",
    "广州": "广东", "深圳": "广东", "珠海": "广东", "佛山": "广东", "东莞": "广东",
    "南宁": "广西", "桂林": "广西", "柳州": "广西",
    "海口": "海南", "三亚": "海南",
    "成都": "四川", "绵阳": "四川", "德阳": "四川",
    "贵阳": "贵州", "遵义": "贵州",
    "昆明": "云南", "曲靖": "云南", "红河": "云南", "普洱": "云南",
    "拉萨": "西藏",
    "西安": "陕西", "咸阳": "陕西", "宝鸡": "陕西", "延安": "陕西",
    "岚皋县": "陕西", "榆林": "陕西",
    "兰州": "甘肃", "天水": "甘肃",
    "西宁": "青海",
    "银川": "宁夏", "吴忠": "宁夏",
    "乌鲁木齐": "新疆", "阿克苏": "新疆", "喀什": "新疆", "克拉玛依": "新疆",
    "温宿县": "新疆",
    # 内蒙古旗县
    "化德县": "内蒙古", "商都县": "内蒙古", "兴和县": "内蒙古", "凉城县": "内蒙古",
    "察哈尔": "内蒙古", "锡林郭勒": "内蒙古", "赤峰": "内蒙古", "通辽": "内蒙古",
    "呼伦贝尔": "内蒙古", "乌兰察布": "内蒙古", "巴彦淖尔": "内蒙古", "阿拉善": "内蒙古",
    "市辖区": "",
}


def get(url, referer=None, retries=3):
    h = dict(HEADERS)
    if referer:
        h["Referer"] = referer
    last = None
    for i in range(retries):
        try:
            r = requests.get(url, headers=h, timeout=25)
            r.raise_for_status()
            # 政府采购网全站 UTF-8；个别地方页可能声明 gb2312
            if not r.encoding or r.encoding.lower() in ("iso-8859-1", "ascii"):
                r.encoding = "utf-8"
            return r.text
        except Exception as e:          # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"抓取失败 {url}: {last}")


def parse_list(html, list_url):
    """列表页 -> [(标题, 绝对URL)]"""
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
    seen, out = set(), []
    for t, u in items:
        if u in seen:
            continue
        seen.add(u)
        out.append((t, u))
    return out


def _txt(node):
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).replace("\xa0", " ").strip()


# 路径前缀 -> 公告类型
RESULT_CATS = {"zbgg", "cjgg", "jggg", "gzgg"}          # 中标/成交/结果/更正
TENDER_CATS = {"gkzb", "jzxcs", "jzxtpgg", "jzxtp", "xjgg", "xjtp", "yqzb", "jzxdy"}


def classify(url, title):
    """公告类型：招标类=商机；中标/成交/结果=已结束；更正/废标=参考；新闻稿排除"""
    m = re.search(r"/([a-z]+)/\d+/t\d+_\d+\.htm", url.lower())
    cat = m.group(1) if m else ""
    t = title or ""

    # 新闻稿/政策解读：列表页会混入这些，不是采购公告
    if re.search(r"(新闻|发布会|竞赛|座谈|调研|培训班|专题|解读|评论|综述)", t):
        return "新闻"
    if re.search(r"^[\u4e00-\u9fa5]{2,10}(部|厅|局|委|办|院|会)[:：]?", t) and \
            not re.search(r"(公告|采购|招标|项目)", t):
        return "新闻"
    if re.search(r"(财政部|国务院|国家发展改革委|发改委)(发布|公布|印发|召开)", t):
        return "新闻"
    if not re.search(r"(公告|采购|招标|磋商|谈判|询价|比选|中标|成交|结果|项目)", t) and not cat:
        return "新闻"

    # 标题优先级最高：结果更正公告仍属结果类
    if re.search(r"(中标|成交|结果)", t) and not re.search(r"(招标公告|采购公告)", t):
        return "结果公告"
    if cat in RESULT_CATS:
        return "结果公告"
    if cat in TENDER_CATS:
        return "招标公告"
    if re.search(r"(招标公告|竞争性磋商|竞争性谈判|询价公告|邀请招标|比选公告|采购公告)", t):
        return "招标公告"
    if re.search(r"(废标|终止|更正|澄清|修改)公告", t):
        return "更正废标"
    return "其他"


DATE_RE = re.compile(r"(20\d{2})\s*[年\-/]\s*(\d{1,2})\s*[月\-/]\s*(\d{1,2})\s*日?")
BODY_DEADLINE_RE = re.compile(
    r"(?:截止时间|递交截止|提交截止|响应截止|报价截止)\s*[:：]\s*"
    r"(20\d{2}\s*[年\-/]\s*\d{1,2}\s*[月\-/]\s*\d{1,2}\s*日?)")


def norm_date(s):
    if not s:
        return ""
    m = DATE_RE.search(s)
    if not m:
        return ""
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


def money(s):
    """￥1105.130000万元 -> 11051300.0；'详见公告正文' 等非数值返回 None"""
    if not s:
        return None
    s = s.replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    m = re.search(r"([\d.]+)\s*(亿元|万元|元)", s)
    if not m:
        return None
    try:
        v = float(m.group(1))
    except ValueError:
        return None
    if v <= 0:
        return None
    unit = m.group(2)
    mult = {"亿元": 1e8, "万元": 1e4, "元": 1}[unit]
    return round(v * mult, 2)


def detect_province(kv_region, text, scope="", title=""):
    """省份识别，按可靠性从高到低：
    1) 「行政区域」字段是有效省名（"市辖区"/"省本级" 这类无效值跳过）
    2) 「行政区域」是市/区县，查映射表
    3) 从标题里找（中央公告常把项目所在地写进标题）
    4) 地方公告再从正文兜底
    中央公告不做正文兜底（正文地名不代表项目所在地）。
    """
    INVALID = {"市辖区", "省本级", "县", "市", "区", "本级", "其他"}

    def _match(s):
        if not s:
            return ""
        # 去掉"北京时间""北京时"这类时间表述，避免把时区当成地点
        s = s.replace("北京时间", "").replace("（北京时", "（").replace("(北京时", "(")
        s = re.sub(r"北京\s*时间", "", s)
        for p in PROVINCES:
            if p in s:
                # "北京" 单独出现时要求不是时间语境（上面已处理），再确认一次
                if p == "北京" and re.search(r"北京\s*时", s):
                    continue
                return p
        for city, prov in CITY_TO_PROVINCE.items():
            if city in s and prov:
                return prov
        return ""

    if kv_region and kv_region.strip() not in INVALID:
        hit = _match(kv_region)
        if hit:
            return hit

    hit = _match(title)
    if hit:
        return hit

    if scope == "中央":
        return ""
    return _match(text)


def parse_detail(html, url, scope=""):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style"]):
        tag.decompose()

    # 概要表：<td>键</td><td>值</td> 结构
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

    title = pick("采购项目名称", "项目名称")
    if not title:
        h = soup.find(["h1", "h2"])
        title = _txt(h) if h else ""

    kind = classify(url, title)

    budget_raw = pick("预算金额", "采购预算", "项目预算")
    budget = money(budget_raw)
    # 「详见公告正文」等情况，从正文里再找一次预算数字
    if budget is None:
        m = re.search(r"预算金额\s*[:：]\s*(?:人民币)?\s*([\d,.]+\s*(?:亿元|万元|元))", body)
        if m:
            budget_raw = m.group(1)
            budget = money(budget_raw)

    deadline_raw = pick("投标截止时间", "响应文件递交截止时间", "递交响应文件截止时间",
                        "提交投标文件截止时间", "响应文件提交截止时间",
                        "开标时间", "响应文件开启时间", "开启时间", "报价截止时间")
    if not deadline_raw:
        m = BODY_DEADLINE_RE.search(body)
        if m:
            deadline_raw = m.group(1)

    region_field = pick("行政区域")
    province = detect_province(region_field, body[:800], scope, title)

    return {
        "url": url,
        "kind": kind,
        "title": title,
        "agency": pick("采购单位", "采购人", "招标人"),
        "agent": pick("代理机构名称", "采购代理机构"),
        "agent_contact": pick("代理机构联系方式"),
        "budget_raw": budget_raw,
        "budget_cny": budget,
        "win_raw": pick("中标金额", "成交金额", "中标（成交）金额", "总中标金额"),
        "winner": pick("中标供应商", "成交供应商", "中标（成交）供应商", "供应商名称"),
        "deadline_raw": deadline_raw,
        "deadline": norm_date(deadline_raw),
        "region": region_field,
        "province": province,
        "category": pick("品目"),
        "publish_time": pick("公告时间"),
        "project_no": pick("项目编号"),
        "body_len": len(body),
        "body_head": body[:600],
    }


def crawl_source(src, pages, limit, errors):
    """抓一个数据源的所有列表页 -> 详情"""
    rows = []
    path = src["path"]
    for p in range(pages):
        list_url = BASE + path if p == 0 else f"{BASE}{path}index_{p}.htm"
        try:
            lst = parse_list(get(list_url), list_url)
        except Exception as e:                      # noqa: BLE001
            errors.append({"stage": "list", "source": src["name"],
                           "url": list_url, "error": str(e)})
            continue

        for title, durl in lst:
            if limit and len(rows) >= limit:
                break
            try:
                d = parse_detail(get(durl, referer=list_url), durl, src["scope"])
                d["list_title"] = title
                d["source"] = src["name"]
                d["scope"] = src["scope"]
                d["fetched_at"] = datetime.now(timezone.utc).isoformat()
                rows.append(d)
            except Exception as e:                  # noqa: BLE001
                errors.append({"stage": "detail", "source": src["name"],
                               "url": durl, "error": str(e)})
            time.sleep(0.6)                         # 礼貌限速
    return rows


def main(pages=1, limit=None, only=None):
    started = datetime.now(timezone.utc).isoformat()
    rows, errors = [], []

    for src in SOURCES:
        if only and src["name"] not in only:
            continue
        before = len(rows)
        rows.extend(crawl_source(src, pages, limit, errors))
        print(f"[src] {src['name']}: {len(rows)-before} 条")

    # 跨源去重（同一项目可能同时出现在中央和地方）
    seen, uniq = set(), []
    for r in rows:
        key = r.get("project_no") or (r.get("title", "") + "|" + r.get("agency", ""))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    dup = len(rows) - len(uniq)

    result = {
        "source": "中国政府采购网（中央 + 地方）",
        "sources": [s["name"] for s in SOURCES if not only or s["name"] in only],
        "collected_at": started,
        "count": len(uniq),
        "duplicates_removed": dup,
        "errors": errors,
        "items": uniq,
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / f"ccgp_{stamp}.json"
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    out.write_text(payload, encoding="utf-8")
    (OUT_DIR / "latest.json").write_text(payload, encoding="utf-8")

    tenders = [r for r in uniq if r["kind"] == "招标公告"]
    filled = sum(1 for r in tenders if r["budget_cny"])
    dl = sum(1 for r in tenders if r["deadline"])
    prov = sum(1 for r in uniq if r["province"])
    prov_set = sorted({r["province"] for r in uniq if r["province"]})

    print(f"[ok] 采集 {len(uniq)} 条（去重 {dup}），错误 {len(errors)} 个 -> {out}")
    print(f"[stat] 招标公告 {len(tenders)} 条；预算命中 {filled}/{len(tenders)}；"
          f"截止日命中 {dl}/{len(tenders)}")
    print(f"[stat] 省份识别 {prov}/{len(uniq)}，覆盖 {len(prov_set)} 个："
          f"{'、'.join(prov_set[:12])}{'…' if len(prov_set) > 12 else ''}")
    for r in tenders[:8]:
        print(f"  - [{r['scope']}/{r['province'] or '—'}] {r['title'][:30]} "
              f"| 预算 {r['budget_cny']} | 截止 {r['deadline']}")
    if errors:
        for e in errors[:5]:
            print("[err]", e.get("source"), e["url"], e["error"][:70])
    return 0 if uniq else 1


if __name__ == "__main__":
    pages = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    only = sys.argv[3].split(",") if len(sys.argv) > 3 else None
    raise SystemExit(main(pages, limit, only))
