#!/usr/bin/env python3
"""
push_slack.py — Workflow A Step 5
将 Digest 推送到 Slack

用法：
  python3 push_slack.py --date 2026-03-10
  python3 push_slack.py  # 使用今天
"""

import sys
import datetime
import argparse
import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).parent
DIGEST_DIR = WORKSPACE / "output" / "digest"

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


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    args = parser.parse_args()

    date_str = args.date
    digest_file = DIGEST_DIR / f"{date_str}_digest.md"

    if not digest_file.exists():
        print(f"[ERROR] No digest for {date_str}. Run formatter.py first.", file=sys.stderr)
        sys.exit(1)

    with open(digest_file) as f:
        digest_text = f.read()

    print(f"[push] Sending digest to Slack ({len(digest_text)} chars)...")
    success = push_to_slack(digest_text)

    if not success:
        print("[push] Slack push failed. Digest content:")
        print("-" * 40)
        print(digest_text[:2000])
        print("-" * 40)
        sys.exit(1)

    print("[push] Slack push successful ✓")


if __name__ == "__main__":
    main()
