# -*- coding: utf-8 -*-
"""
一键跑完：采集 -> 去重/变化检测 -> 出报告 -> 订阅匹配 -> 推送
失败自动重试，跑完写 health.json，异常时退出码非 0（方便 GitHub Actions 告警）。
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PY = sys.executable

STEPS = [
    ("采集", [PY, "collect.py", "3"]),
    ("报告", [PY, "report.py"]),
    ("推送", [PY, "push.py"]),
]


def run(cmd, retries=2):
    last = ""
    for i in range(retries + 1):
        p = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        last = (p.stdout or "") + (p.stderr or "")
        if p.returncode == 0:
            return True, last
        if i < retries:
            print(f"  第 {i+1} 次失败，重试…")
            time.sleep(3 * (i + 1))
    return False, last


def main():
    failed = []
    for name, cmd in STEPS:
        print(f"== {name} ==")
        ok, out = run(cmd)
        print(out.strip()[:2000])
        if not ok:
            failed.append(name)
            print(f"[FAIL] {name} 失败")
    if failed:
        print(f"[pipeline] 失败环节：{', '.join(failed)}")
        return 1
    print("[pipeline] 全部完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
