# 政府采购招标监控 · 可交付原型

一个能真跑起来的自动化流水线：从中国政府采购网公开页面采集招标公告，
结构化出**项目名 / 采购单位 / 预算 / 截止日期 / 地区品目 / 原文链接**，
去重、检测变化、生成日报、按关键词订阅匹配，再推送到邮件 / Telegram / 企业微信。

零成本可跑，不需要任何付费服务，不登录、不绕验证码、不碰个人信息。

## 现在就能验证

```bash
cd tender-watch
python collect.py 3      # 采集 3 页（约 75 条公告）
python report.py         # 生成日报 HTML + Markdown
python push.py --dry-run # 关键词订阅匹配预览
python run.py            # 一条命令跑完全流程
```

实测（2026-10-23）：采集 105 条，其中招标公告 21 条，
**预算字段命中 21/21，截止日期命中 21/21，详情页 0 错误。**

产出：
- `out/report_YYYYMMDD.html` — 可直接发给客户的日报页面
- `out/report_YYYYMMDD.md` — 纯文本版
- `out/digest_*.txt` — 订阅推送摘要
- `data/health.json` — 自检结果（字段命中率、告警）

## 文件说明

| 文件 | 作用 |
|---|---|
| `collect.py` | 采集 + 解析 + 公告类型分类（招标/结果/更正） |
| `report.py` | 去重指纹、变化检测、日报生成、健康自检与告警 |
| `push.py` | 关键词订阅匹配 + 邮件/Telegram/企业微信推送 |
| `run.py` | 编排入口，失败重试，退出码驱动告警 |
| `config.json` | 订阅规则与推送渠道配置（见下） |

## 配置订阅

复制 `config.example.json` 为 `config.json`，按需修改：

```json
{
  "subscriptions": [
    { "name": "信息化项目", "keywords": ["软件","信息系统","服务器","数据","网络安全"],
      "min_budget": 500000, "max_days_left": 30 },
    { "name": "大额工程", "keywords": [], "min_budget": 10000000 }
  ],
  "telegram_bot_token": "",
  "telegram_chat_id": "",
  "wecom_webhook": "",
  "smtp_host": "", "smtp_port": 465, "smtp_user": "", "smtp_pass": "",
  "mail_to": ["customer@example.com"]
}
```

密钥建议走环境变量（`TG_BOT_TOKEN` / `WECOM_WEBHOOK` / `SMTP_PASS`），
不要写进仓库。

## 自愈设计

`report.py` 每次运行都算一遍健康指标，任一指标异常就写进 `health.json` 的 `alerts`：

- 列表页返回空 → 源站可能改版或被封
- 招标公告为 0 条 → 解析规则失效
- 预算 / 截止日命中率跌破 60% → 页面结构变了
- 详情页失败率超 30% → 需要检查

配合 `run.py` 的非 0 退出码，可以直接挂到 GitHub Actions 或任意 cron 上，
坏了会自己报出来，而不是悄悄空转。

## 已验证的边界

- 目前只接了中国政府采购网的**中央公告**频道，地方公告（各省平台）还没接，
  各地页面结构差异大，需要逐个写适配器。
- 竞争性磋商 / 询价类公告的截止时间常写在正文而非概要表里，已做正文兜底解析。
- 源站偶发超时（实测遇到过一次），已加 3 次重试 + 1.5s 递增退避。
- 抓取限速 0.8s/请求，属于礼貌抓取范围。

## 合规边界

只抓公开、无需登录的页面；不做验证码绕过、不伪造身份、不采集个人信息；
所有条目保留原文链接可溯源。
