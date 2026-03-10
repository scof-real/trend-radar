#!/usr/bin/env python3
"""
push_slack.py — Workflow A Step 5
将 Digest 推送到 Slack，并对每条趋势发送 reaction 反馈请求

用法：
  python3 push_slack.py --date 2026-03-10
  python3 push_slack.py  # 使用今天
"""

import sys
import json
import datetime
import argparse
import subprocess
import time
from pathlib import Path

WORKSPACE = Path(__file__).parent
DIGEST_DIR = WORKSPACE / "output" / "digest"
FEEDBACK_DIR = WORKSPACE / "output" / "feedback"
FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)

# 推送目标：Qiaochu 的 Slack DM
SLACK_TARGET = "U0ACEKZ4F52"


# ─── Slack 推送 ─────────────────────────────────────────────────────────────

def push_to_slack(text: str) -> bool:
    """通过 openclaw CLI 推送到 Slack，返回是否成功"""
    try:
        result = subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "slack",
             "--to", SLACK_TARGET,
             "--message", text],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True
        print(f"[WARN] openclaw send failed: {result.stderr[:200]}", file=sys.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return False


# ─── 反馈闭环 ───────────────────────────────────────────────────────────────

def push_feedback_prompt(trend_title: str, trend_index: int, total: int) -> bool:
    """
    每条趋势推送后，发一条 reaction 引导消息。
    格式：「👍 有用 / 👎 没用」— 用 emoji 回复即可，Dough 会记录。
    """
    msg = (
        f"*趋势 {trend_index}/{total}：{trend_title}*\n"
        f"👍 有用　　👎 没用\n"
        f"_（直接在上面的消息上加 reaction）_"
    )
    return push_to_slack(msg)


def log_feedback_init(date_str: str, trends: list[dict]) -> None:
    """
    初始化当日反馈记录文件。
    格式：{trend_title: {useful: 0, not_useful: 0}, ...}
    后续可由 heartbeat 或手动脚本读取 Slack reaction 并更新此文件。
    """
    feedback_file = FEEDBACK_DIR / f"{date_str}_feedback.json"
    if feedback_file.exists():
        return  # 已存在则不覆盖

    record = {}
    for i, t in enumerate(trends):
        title = t.get("title", f"Trend {i+1}")
        record[title] = {"useful": 0, "not_useful": 0, "signal_strength": t.get("signal_strength", "")}

    with open(feedback_file, "w") as f:
        json.dump(record, f, indent=2, ensure_ascii=False)

    print(f"  [feedback] Initialized {feedback_file.name}")


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--no-feedback", action="store_true", help="跳过 reaction 反馈提示")
    args = parser.parse_args()

    date_str = args.date
    digest_file = DIGEST_DIR / f"{date_str}_digest.md"
    trends_file = DIGEST_DIR / f"{date_str}_trends.json"

    if not digest_file.exists():
        print(f"[ERROR] No digest for {date_str}. Run formatter.py first.", file=sys.stderr)
        sys.exit(1)

    with open(digest_file) as f:
        digest_text = f.read()

    # ── 推送主 Digest ──
    print(f"[push] Sending digest to Slack ({len(digest_text)} chars)...")
    success = push_to_slack(digest_text)

    if not success:
        print("[push] Slack push failed. Digest content:")
        print("-" * 40)
        print(digest_text[:2000])
        print("-" * 40)
        sys.exit(1)

    print("[push] Slack push successful ✓")

    # ── 反馈闭环 ──
    if args.no_feedback:
        return

    if not trends_file.exists():
        print(f"[feedback] No trends JSON for {date_str}, skipping feedback prompts.")
        return

    with open(trends_file) as f:
        trends = json.load(f)

    if not trends:
        return

    # 初始化反馈记录文件
    log_feedback_init(date_str, trends)

    # 稍等片刻让主 Digest 消息先送达
    time.sleep(2)

    # 推送 reaction 引导消息（每条趋势一条，带短暂间隔避免刷屏）
    print(f"[feedback] Sending reaction prompts for {len(trends)} trends...")
    for i, trend in enumerate(trends, start=1):
        title = trend.get("title", f"Trend {i}")
        push_feedback_prompt(title, i, len(trends))
        if i < len(trends):
            time.sleep(1)

    print(f"[feedback] Done. Feedback log: output/feedback/{date_str}_feedback.json")


if __name__ == "__main__":
    main()
