#!/usr/bin/env python3
"""
collector.py — Workflow A Step 1-2
用 bird CLI 抓取种子账号推文，按时间窗口过滤 + 互动量过滤，输出 JSONL

用法：
  python3 collector.py              # 正常运行，抓全部账号
  python3 collector.py --dry-run    # 只测试第一个账号
  python3 collector.py --window 48  # 自定义时间窗口（小时，默认 24）
"""

import subprocess
import json
import sys
import re
import yaml
import datetime
import argparse
import time
from pathlib import Path
from collections import defaultdict

WORKSPACE = Path(__file__).parent
RAW_DIR = WORKSPACE / "output" / "raw"
FILTERED_DIR = WORKSPACE / "output" / "filtered"
DEDUP_DIR = WORKSPACE / "output" / "deduped"
RAW_DIR.mkdir(parents=True, exist_ok=True)
FILTERED_DIR.mkdir(parents=True, exist_ok=True)
DEDUP_DIR.mkdir(parents=True, exist_ok=True)

BIRD_AUTH_TOKEN = "ad2923023d097a2e0626370982e85a2e0396d265"
BIRD_CT0 = "07b0b7be4d3d14cd9fff6942e63e3b8b380ee5e2539a9dc6159ec103b496652433af885015cdb060411ead9a325a00a73781344ee25e23e29f5c09daf45cbe4f8f95e3b3e401c4d7587be3400521d630"

def load_config():
    with open(WORKSPACE / "seed_accounts.yaml") as f:
        return yaml.safe_load(f)


# ─── ① 抓取 ────────────────────────────────────────────────────────────────

def fetch_tweets(handle: str, count: int) -> list[dict]:
    """用 bird CLI 抓取指定账号的推文（最多 count 条）"""
    clean_handle = handle.lstrip("@")
    try:
        result = subprocess.run(
            ["bird", "--auth-token", BIRD_AUTH_TOKEN, "--ct0", BIRD_CT0,
             "user-tweets", clean_handle, "--count", str(count), "--json"],
            capture_output=True, text=True, timeout=90
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout)[:300]
            print(f"  [WARN] bird failed for {handle}: {err}", file=sys.stderr)
            return []

        # bird 输出 JSON array，可能混有 ℹ️/⚠️/❌ 行
        clean_lines = [l for l in result.stdout.splitlines()
                       if l.strip()
                       and not l.startswith("ℹ️")
                       and not l.startswith("⚠️")
                       and not l.startswith("❌")]
        clean_output = "\n".join(clean_lines)
        if not clean_output.strip():
            return []

        try:
            data = json.loads(clean_output)
            return data if isinstance(data, list) else [data]
        except json.JSONDecodeError:
            # fallback JSONL
            tweets = []
            for line in clean_lines:
                try:
                    tweets.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return tweets

    except subprocess.TimeoutExpired:
        print(f"  [WARN] Timeout for {handle}", file=sys.stderr)
        return []
    except FileNotFoundError:
        print("  [ERROR] bird CLI not found. Is it installed?", file=sys.stderr)
        sys.exit(1)


# ─── ② 时间窗口过滤 ─────────────────────────────────────────────────────────

def parse_created_at(tweet: dict) -> datetime.datetime | None:
    """解析 bird 返回的 createdAt 字符串为 UTC datetime"""
    raw = tweet.get("createdAt", "")
    if not raw:
        return None
    # 格式示例："Wed Mar 04 16:02:40 +0000 2026"
    try:
        return datetime.datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        # 尝试 ISO 格式
        try:
            return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

def filter_by_time_window(tweets: list[dict], window_hours: int) -> list[dict]:
    """只保留最近 window_hours 小时内的推文"""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=window_hours)
    result = []
    for t in tweets:
        created = parse_created_at(t)
        if created is None:
            # 无法解析时间的，保守放行
            result.append(t)
        elif created >= cutoff:
            result.append(t)
    return result


# ─── ③ 互动量过滤（per-account 相对阈值）───────────────────────────────────

def compute_per_account_threshold(tweets: list[dict]) -> tuple[float, float]:
    """计算该账号所有推文的 likes/RT 中位数，返回 (median_likes, median_rt)"""
    likes_list = sorted([t.get("likeCount", 0) or 0 for t in tweets])
    rt_list = sorted([t.get("retweetCount", 0) or 0 for t in tweets])
    n = len(likes_list)
    if n == 0:
        return 0, 0
    mid = n // 2
    median_likes = (likes_list[mid - 1] + likes_list[mid]) / 2 if n % 2 == 0 else likes_list[mid]
    median_rt = (rt_list[mid - 1] + rt_list[mid]) / 2 if n % 2 == 0 else rt_list[mid]
    return median_likes, median_rt

def filter_by_engagement(tweets: list[dict],
                          global_min_likes: int,
                          global_min_rt: int,
                          use_relative: bool = True) -> list[dict]:
    """
    互动量过滤。
    use_relative=True：用该账号中位数 × 1.5 作为动态阈值（兜底用全局最低值）
    use_relative=False：直接用全局阈值
    """
    if not tweets:
        return []

    if use_relative:
        median_likes, median_rt = compute_per_account_threshold(tweets)
        # 动态阈值 = max(全局最低值, 中位数 × 1.5)
        dyn_likes = max(global_min_likes, median_likes * 1.5)
        dyn_rt = max(global_min_rt, median_rt * 1.5)
    else:
        dyn_likes = global_min_likes
        dyn_rt = global_min_rt

    filtered = []
    for t in tweets:
        likes = t.get("likeCount", 0) or 0
        rt = t.get("retweetCount", 0) or 0
        if likes >= dyn_likes or rt >= dyn_rt:
            filtered.append(t)
    return filtered


# ─── ④ URL 收集（collector 阶段完成）───────────────────────────────────────

def enrich_tweet_url(tweet: dict) -> dict:
    """在 collector 阶段就把 tweet URL 写入推文对象"""
    tid = str(tweet.get("id", tweet.get("tweet_id", tweet.get("id_str", ""))))
    author = tweet.get("author", {})
    if isinstance(author, dict):
        username = author.get("username", author.get("screen_name", "unknown"))
    else:
        username = str(author)
    if tid and username != "unknown":
        tweet["_tweet_url"] = f"https://x.com/{username}/status/{tid}"
    else:
        tweet["_tweet_url"] = ""
    return tweet

def build_id_url_map(tweets: list[dict]) -> dict:
    """构建 tweet_id → URL 对照表（从已 enrich 过的推文中提取）"""
    return {
        str(t.get("id", t.get("tweet_id", ""))): t.get("_tweet_url", "")
        for t in tweets
        if t.get("_tweet_url")
    }


# ─── ⑤ 去重（关键词 overlap）───────────────────────────────────────────────

def extract_keywords(text: str) -> set[str]:
    """提取推文关键词（去停用词，小写）"""
    stopwords = {
        "the","a","an","is","it","in","on","at","to","for","of","and","or",
        "but","with","from","by","as","this","that","be","are","was","were",
        "have","has","will","we","our","your","their","its","not","no","you",
        "i","my","he","she","they","what","how","why","when","where","which",
        "so","do","did","get","got","can","if","all","also","just","more",
        "than","out","up","about","been","had","into","new","now","one","some",
        "there","use","very","may","any","even","like","make","over","after",
    }
    words = re.findall(r'[a-zA-Z]{4,}', text.lower())
    return {w for w in words if w not in stopwords}

def deduplicate_tweets(tweets: list[dict], overlap_threshold: float = 0.5) -> list[dict]:
    """
    去重：如果两条推文的关键词 Jaccard 相似度 >= threshold，认为是同一趋势。
    保留 likes 最高的那条，并在 _cluster_count 里记录聚合数量。
    """
    if not tweets:
        return []

    clusters: list[list[dict]] = []
    used = [False] * len(tweets)

    for i, t in enumerate(tweets):
        if used[i]:
            continue
        kw_i = extract_keywords(t.get("text", ""))
        cluster = [t]
        for j, t2 in enumerate(tweets[i+1:], start=i+1):
            if used[j]:
                continue
            kw_j = extract_keywords(t2.get("text", ""))
            if not kw_i or not kw_j:
                continue
            intersection = len(kw_i & kw_j)
            union = len(kw_i | kw_j)
            jaccard = intersection / union if union > 0 else 0
            if jaccard >= overlap_threshold:
                cluster.append(t2)
                used[j] = True
        used[i] = True
        clusters.append(cluster)

    result = []
    for cluster in clusters:
        # 保留 likes 最高的代表推文
        best = max(cluster, key=lambda t: t.get("likeCount", 0) or 0)
        best["_cluster_count"] = len(cluster)
        best["_cluster_handles"] = list({t.get("_seed_handle", "") for t in cluster})
        result.append(best)

    return result


# ─── main ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只跑第一个账号")
    parser.add_argument("--window", type=int, default=24, help="时间窗口（小时，默认 24）")
    args = parser.parse_args()

    config = load_config()
    accounts = config["seed_accounts"]
    global_min_likes = config["filter"].get("min_likes", 50)
    global_min_rt = config["filter"].get("min_retweets", 20)
    tweets_per_account = config["filter"].get("tweets_per_account", 30)
    window_hours = args.window

    if args.dry_run:
        accounts = accounts[:1]
        print(f"[DRY RUN] Testing with 1 account only")

    today = datetime.date.today().isoformat()
    raw_file = RAW_DIR / f"{today}_raw.jsonl"
    filtered_file = FILTERED_DIR / f"{today}_filtered.jsonl"
    deduped_file = DEDUP_DIR / f"{today}_deduped.jsonl"
    id_map_file = FILTERED_DIR / f"{today}_id_url_map.json"

    all_raw = []
    all_filtered = []

    print(f"[collector] Time window: past {window_hours}h | Accounts: {len(accounts)}")
    print(f"[collector] Global thresholds: likes≥{global_min_likes} OR RT≥{global_min_rt} (per-account relative enabled)")

    for i, account in enumerate(accounts):
        handle = account["handle"]
        category = account["category"]
        print(f"  [{i+1}/{len(accounts)}] {handle} ({category})...", end=" ", flush=True)

        tweets = fetch_tweets(handle, tweets_per_account)

        # 时间窗口过滤
        tweets_in_window = filter_by_time_window(tweets, window_hours)

        # 给推文加标记 + URL
        for t in tweets_in_window:
            t["_seed_handle"] = handle
            t["_seed_category"] = category
            enrich_tweet_url(t)
            all_raw.append(t)

        # 互动量过滤（per-account 相对阈值）
        filtered = filter_by_engagement(tweets_in_window, global_min_likes, global_min_rt, use_relative=True)

        print(f"{len(tweets)} fetched → {len(tweets_in_window)} in {window_hours}h window → {len(filtered)} passed engagement filter")
        all_filtered.extend(filtered)

        # 账号间延迟，避免 429
        if i < len(accounts) - 1:
            delay = 8 + (hash(handle) % 8)
            time.sleep(delay)

    # 去重
    deduped = deduplicate_tweets(all_filtered, overlap_threshold=0.45)

    # 写入文件
    with open(raw_file, "w") as f:
        for t in all_raw:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    with open(filtered_file, "w") as f:
        for t in all_filtered:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    with open(deduped_file, "w") as f:
        for t in deduped:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")

    id_map = build_id_url_map(all_filtered)
    with open(id_map_file, "w") as f:
        json.dump(id_map, f, indent=2, ensure_ascii=False)

    print(f"\n[collector] Done.")
    print(f"  Raw:      {len(all_raw)} tweets  → {raw_file.name}")
    print(f"  Filtered: {len(all_filtered)} tweets  → {filtered_file.name}")
    print(f"  Deduped:  {len(deduped)} tweets  → {deduped_file.name}")
    print(f"  ID map:   {len(id_map)} entries → {id_map_file.name}")

    # 如果没有任何内容通过，输出警告（供 run_workflow_a.sh 捕获）
    if len(deduped) == 0:
        print("[WARN] No tweets passed all filters. Digest will be empty.", file=sys.stderr)
        sys.exit(2)  # exit code 2 = 空结果（非错误，但需要告警）

if __name__ == "__main__":
    main()
