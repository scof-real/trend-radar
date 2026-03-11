#!/usr/bin/env python3
"""
formatter.py — Workflow A Step 4
把结构化趋势数据格式化成 Slack Digest 消息

用法：
  python3 formatter.py                   # 格式化今天的分析结果
  python3 formatter.py --date 2026-03-10
  python3 formatter.py --stdout          # 输出到 stdout（调试用）
"""

import json
import sys
import datetime
import argparse
import yaml
from pathlib import Path

WORKSPACE = Path(__file__).parent
DIGEST_DIR = WORKSPACE / "output" / "digest"
DEDUP_DIR = WORKSPACE / "output" / "deduped"
FILTERED_DIR = WORKSPACE / "output" / "filtered"

SIGNAL_EMOJI = {
    "high":   "🔥",
    "medium": "📡",
    "low":    "💬",
}

SIGNAL_RANK = {"high": 3, "medium": 2, "low": 1}

CATEGORY_EMOJI = {
    "AI x Crypto":   "🤖",
    "Telegram Bot":  "🤖",
    "CLI Tools":     "⌨️",
    "Gen-Z Behavior": "👾",
    "Other":         "📌",
}


# ─── 加载 seed_accounts.yaml 的 type 映射 ──────────────────────────────────

def load_account_types() -> dict[str, str]:
    """返回 handle -> type ('native' | 'observer') 的映射"""
    seed_file = WORKSPACE / "seed_accounts.yaml"
    try:
        with open(seed_file) as f:
            config = yaml.safe_load(f)
        return {
            a["handle"].lstrip("@").lower(): a.get("type", "observer")
            for a in config.get("seed_accounts", [])
        }
    except Exception:
        return {}


def get_trend_source_type(trend: dict, account_types: dict) -> str:
    """
    判断这条趋势的来源类型。
    优先看 trend 对象里 _cluster_handles（去重后的来源账号列表），
    如果所有来源账号都是 observer，返回 'observer'；否则返回 'native'。
    """
    handles = trend.get("_cluster_handles", [])
    if not handles:
        # 没有 cluster 信息，看 representative_tweet_id 对应的账号
        handles = [trend.get("_seed_handle", "")]

    types = set()
    for h in handles:
        clean = h.lstrip("@").lower()
        types.add(account_types.get(clean, "observer"))

    # 只要有一个 native 来源，就算 native
    return "native" if "native" in types else "observer"


def downgrade_signal(signal: str) -> str:
    """降一级信号强度"""
    if signal == "high":
        return "medium"
    if signal == "medium":
        return "low"
    return "low"


def apply_source_type_adjustment(trends: list[dict], account_types: dict) -> list[dict]:
    """
    对每条趋势：
    - 标注 _source_type（native / observer）
    - observer 来源的趋势降一级 signal_strength
    - 保留 _original_signal 供 Digest 显示
    """
    for trend in trends:
        source_type = get_trend_source_type(trend, account_types)
        trend["_source_type"] = source_type
        original_signal = trend.get("signal_strength", "medium").lower()
        trend["_original_signal"] = original_signal

        if source_type == "observer":
            trend["signal_strength"] = downgrade_signal(original_signal)

    return trends


# ─── 格式化单条趋势 ──────────────────────────────────────────────────────────

def format_trend(trend: dict, index: int) -> str:
    signal = trend.get("signal_strength", "medium").lower()
    original_signal = trend.get("_original_signal", signal)
    source_type = trend.get("_source_type", "observer")
    cluster_count = trend.get("_cluster_count", 1)
    category = trend.get("category", "Other")
    title = trend.get("title", "Untitled")
    description = trend.get("description", "")
    user_psychology = trend.get("user_psychology", "")
    product_insight = trend.get("product_insight", "")
    url = trend.get("representative_url", "")

    sem = SIGNAL_EMOJI.get(signal, "📡")
    cem = CATEGORY_EMOJI.get(category, "📌")

    # 来源标签
    if source_type == "observer":
        source_tag = "` 二手信号`"
        if original_signal != signal:
            source_tag = f"` 二手信号 · 原为{original_signal}，已降级`"
    else:
        source_tag = ""

    # 多账号提及标签
    cluster_tag = f" · _{cluster_count} 个账号提及_" if cluster_count > 1 else ""

    lines = [
        f"{sem}{cem} *{index}. {title}*{source_tag}{cluster_tag}",
        f"",
        f"*趋势* {description}",
        f"*用户心理* {user_psychology}",
        f"*产品启发* {product_insight}",
    ]

    if url:
        lines.append(f"*原推文* <{url}|→>")

    return "\n".join(lines)


# ─── 格式化完整 Digest ───────────────────────────────────────────────────────

def format_digest(trends: list[dict], date_str: str) -> str:
    high   = [t for t in trends if t.get("signal_strength") == "high"]
    medium = [t for t in trends if t.get("signal_strength") == "medium"]
    low    = [t for t in trends if t.get("signal_strength") == "low"]

    native_count   = sum(1 for t in trends if t.get("_source_type") == "native")
    observer_count = sum(1 for t in trends if t.get("_source_type") == "observer")

    sections = []

    # Header
    sections.append(
        f"📡 *D0 Trending Digest — {date_str}*\n"
        f"*{len(trends)} 条趋势* | 🔥 {len(high)} | 📡 {len(medium)} | 💬 {len(low)}\n"
        f"来源：{native_count} 条原生信号 · {observer_count} 条观察者信号（已降级）"
    )
    sections.append("─" * 40)

    # High signal
    if high:
        sections.append("*🔥 高信号*（直接可落地）\n")
        for i, t in enumerate(high, 1):
            sections.append(format_trend(t, i))
            sections.append("")

    # Medium signal
    if medium:
        sections.append("─" * 40)
        sections.append("*📡 中信号*（值得关注）\n")
        for i, t in enumerate(medium, len(high) + 1):
            sections.append(format_trend(t, i))
            sections.append("")

    # Low signal (condensed)
    if low:
        sections.append("─" * 40)
        sections.append("*💬 低信号*（仅做参考）")
        for i, t in enumerate(low, len(high) + len(medium) + 1):
            title = t.get("title", "Untitled")
            desc = t.get("description", "")
            url = t.get("representative_url", "")
            source_tag = " ·`二手`" if t.get("_source_type") == "observer" else ""
            url_part = f" <{url}|→>" if url else ""
            sections.append(f"{i}. {title}{source_tag} — {desc}{url_part}")
        sections.append("")

    sections.append("─" * 40)
    sections.append("_由 D0 Trend Radar (Agent 03) 自动生成_")

    return "\n".join(sections)


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.date.today().isoformat())
    parser.add_argument("--stdout", action="store_true")
    args = parser.parse_args()

    date_str = args.date
    trends_file = DIGEST_DIR / f"{date_str}_trends.json"

    if not trends_file.exists():
        print(f"[ERROR] No trends file for {date_str}. Run analyzer.py first.", file=sys.stderr)
        sys.exit(1)

    with open(trends_file) as f:
        trends = json.load(f)

    # 加载账号类型，应用来源调整
    account_types = load_account_types()
    trends = apply_source_type_adjustment(trends, account_types)

    digest = format_digest(trends, date_str)

    if args.stdout:
        print(digest)
    else:
        output_file = DIGEST_DIR / f"{date_str}_digest.md"
        with open(output_file, "w") as f:
            f.write(digest)
        print(f"[formatter] Digest saved → {output_file.name}")
        native_count = sum(1 for t in trends if t.get("_source_type") == "native")
        observer_count = sum(1 for t in trends if t.get("_source_type") == "observer")
        print(f"  {len(trends)} trends | native: {native_count} | observer (downgraded): {observer_count}")


if __name__ == "__main__":
    main()
