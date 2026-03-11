#!/usr/bin/env python3
"""
analyzer.py — Workflow A Step 3
将过滤后的推文喂给 Claude/LLM，返回结构化趋势分析

用法：
  python3 analyzer.py                   # 分析今天的过滤结果
  python3 analyzer.py --date 2026-03-10 # 分析指定日期
"""

import json
import sys
import os
import datetime
import argparse
import subprocess
from pathlib import Path

WORKSPACE = Path(__file__).parent
FILTERED_DIR = WORKSPACE / "output" / "filtered"
DEDUP_DIR = WORKSPACE / "output" / "deduped"
DIGEST_DIR = WORKSPACE / "output" / "digest"
DIGEST_DIR.mkdir(parents=True, exist_ok=True)

PROMPT_FILE = WORKSPACE / "analyzer_prompt.txt"


def load_filtered_tweets(date_str: str) -> tuple[list[dict], dict]:
    """
    优先加载去重后推文（deduped），回退到 filtered。
    推文对象中已包含 _tweet_url 字段（由 collector 阶段写入）。
    """
    deduped_file = DEDUP_DIR / f"{date_str}_deduped.jsonl"
    filtered_file = FILTERED_DIR / f"{date_str}_filtered.jsonl"
    id_map_file = FILTERED_DIR / f"{date_str}_id_url_map.json"

    # 优先读 deduped
    source_file = deduped_file if deduped_file.exists() else filtered_file

    if not source_file.exists():
        print(f"[ERROR] No filtered/deduped file for {date_str}", file=sys.stderr)
        sys.exit(1)

    if source_file == deduped_file:
        print(f"  [analyzer] Using deduped tweets: {deduped_file.name}")
    else:
        print(f"  [analyzer] Deduped file not found, using filtered: {filtered_file.name}")

    tweets = []
    with open(source_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    tweets.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    id_map = {}
    if id_map_file.exists():
        with open(id_map_file) as f:
            id_map = json.load(f)

    return tweets, id_map


def call_llm(prompt_text: str) -> str:
    """调用 claude CLI 进行分析"""
    try:
        result = subprocess.run(
            ["claude", "-p", prompt_text, "--output-format", "text"],
            capture_output=True, text=True, timeout=180
        )
        if result.returncode != 0:
            print(f"[WARN] claude CLI error: {result.stderr[:300]}", file=sys.stderr)
            return ""
        return result.stdout.strip()
    except FileNotFoundError:
        # 尝试 openclaw 自身的 LLM（通过环境变量 ANTHROPIC_API_KEY）
        print("[WARN] claude CLI not found, trying direct API...", file=sys.stderr)
        return call_anthropic_api(prompt_text)
    except subprocess.TimeoutExpired:
        print("[ERROR] LLM call timed out", file=sys.stderr)
        return ""


def call_anthropic_api(prompt_text: str) -> str:
    """直接调用 Anthropic API（fallback）"""
    try:
        import anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            print("[ERROR] ANTHROPIC_API_KEY not set", file=sys.stderr)
            return ""
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt_text}]
        )
        return message.content[0].text
    except ImportError:
        print("[ERROR] anthropic package not installed. Run: pip install anthropic", file=sys.stderr)
        return ""


PRODUCT_FILE = WORKSPACE / "product.md"

def prepare_prompt(tweets: list[dict]) -> str:
    """组装 prompt + 推文数据，注入 product.md 作为判断上下文"""
    with open(PROMPT_FILE) as f:
        base_prompt = f.read()

    # 读取 product.md，注入产品定位和趋势筛选标准
    product_context = ""
    if PRODUCT_FILE.exists():
        with open(PRODUCT_FILE) as f:
            product_context = f.read()
    else:
        print(f"[WARN] product.md not found at {PRODUCT_FILE}", file=sys.stderr)

    # 精简推文数据（只保留分析需要的字段）
    slim_tweets = []
    for t in tweets:
        slim = {
            "id": str(t.get("id", t.get("tweet_id", t.get("id_str", "")))),
            "author": t.get("_seed_handle", "unknown"),
            "category": t.get("_seed_category", ""),
            "text": t.get("text", t.get("full_text", t.get("content", ""))),
            "likes": t.get("likeCount", t.get("like_count", t.get("favorites", 0))),
            "retweets": t.get("retweetCount", t.get("retweet_count", t.get("retweets", 0))),
        }
        # 只保留有文本的推文
        if slim["text"]:
            slim_tweets.append(slim)

    tweet_jsonl = "\n".join(json.dumps(t, ensure_ascii=False) for t in slim_tweets)

    # 拼装最终 prompt：base prompt + product.md 上下文 + 推文数据
    full_prompt = f"""{base_prompt}

---

## 产品上下文（判断依据，必读）

{product_context}

---

## 推文数据（JSONL，一行一条）

{tweet_jsonl}"""

    return full_prompt


def resolve_urls(trends: list[dict], id_map: dict, tweets: list[dict]) -> list[dict]:
    """
    把 representative_tweet_id 转成真实 URL。
    优先用 id_map（collector 阶段已构建），回退到推文对象的 _tweet_url 字段。
    绝不生成编造的 URL。
    """
    # 构建 id → _tweet_url 的反向索引（来自推文对象本身）
    tweet_url_index = {
        str(t.get("id", t.get("tweet_id", ""))): t.get("_tweet_url", "")
        for t in tweets
        if t.get("_tweet_url")
    }

    for trend in trends:
        tid = str(trend.get("representative_tweet_id", ""))
        if tid and tid in id_map and id_map[tid]:
            trend["representative_url"] = id_map[tid]
        elif tid and tid in tweet_url_index:
            trend["representative_url"] = tweet_url_index[tid]
        else:
            # 无法确认 URL，留空而不是编造
            trend["representative_url"] = ""
    return trends


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.date.today().isoformat(),
                        help="Date to analyze (YYYY-MM-DD)")
    args = parser.parse_args()

    date_str = args.date
    print(f"[analyzer] Analyzing tweets for {date_str}...")

    tweets, id_map = load_filtered_tweets(date_str)
    print(f"  Loaded {len(tweets)} filtered tweets")

    if not tweets:
        print("[WARN] No tweets to analyze. Run collector.py first.")
        sys.exit(0)

    print("  Calling LLM for trend analysis...")
    prompt = prepare_prompt(tweets)
    raw_response = call_llm(prompt)

    if not raw_response:
        print("[ERROR] Empty response from LLM", file=sys.stderr)
        sys.exit(1)

    # 解析 JSON 数组
    try:
        # 清理可能的 markdown 代码块
        clean = raw_response.strip()
        if clean.startswith("```"):
            lines = clean.split("\n")
            clean = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        trends = json.loads(clean)
        if not isinstance(trends, list):
            trends = [trends]
    except json.JSONDecodeError as e:
        print(f"[ERROR] Failed to parse LLM response as JSON: {e}", file=sys.stderr)
        # 保存原始响应以便调试
        raw_file = DIGEST_DIR / f"{date_str}_raw_llm_response.txt"
        with open(raw_file, "w") as f:
            f.write(raw_response)
        print(f"  Raw response saved to {raw_file.name}", file=sys.stderr)
        sys.exit(1)

    # 解析 URL（优先 id_map，其次推文对象的 _tweet_url，绝不编造）
    trends = resolve_urls(trends, id_map, tweets)

    # 保存结果
    output_file = DIGEST_DIR / f"{date_str}_trends.json"
    with open(output_file, "w") as f:
        json.dump(trends, f, indent=2, ensure_ascii=False)

    print(f"\n[analyzer] Done. Found {len(trends)} trends → {output_file.name}")
    for t in trends:
        signal = t.get("signal_strength", "?")
        title = t.get("title", "?")
        print(f"  [{signal.upper()}] {title}")


if __name__ == "__main__":
    main()
