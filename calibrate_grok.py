#!/usr/bin/env python3
"""
calibrate_grok.py — Workflow B: 每月信号源校准
用 Playwright 浏览器自动化打开 x.com/i/grok，提交 prompt，抓取响应。

用法：
  python3 calibrate_grok.py           # 运行校准，输出建议到 Slack
  python3 calibrate_grok.py --apply   # 运行后交互式更新 seed_accounts.yaml
  python3 calibrate_grok.py --dry-run # 只打印 prompt，不打开浏览器
"""

import subprocess
import json
import sys
import time
import argparse
import yaml
from pathlib import Path
from datetime import date

WORKSPACE = Path(__file__).parent
SEED_FILE = WORKSPACE / "seed_accounts.yaml"
OUTPUT_DIR = WORKSPACE / "output" / "calibration"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BIRD_AUTH_TOKEN = "ad2923023d097a2e0626370982e85a2e0396d265"
BIRD_CT0 = "07b0b7be4d3d14cd9fff6942e63e3b8b380ee5e2539a9dc6159ec103b496652433af885015cdb060411ead9a325a00a73781344ee25e23e29f5c09daf45cbe4f8f95e3b3e401c4d7587be3400521d630"


def load_seed_accounts() -> list[dict]:
    with open(SEED_FILE) as f:
        config = yaml.safe_load(f)
    return config["seed_accounts"]


def build_grok_prompt(accounts: list[dict]) -> str:
    """构建发给 Grok 的 prompt，动态读取当前种子账号列表"""
    account_lines = "\n".join(
        f"- {a['handle']} [{a['category']}] — {a['reason']}"
        for a in accounts
    )
    return f"""I'm building "D0" (Day Zero) — a product for Gen-Z crypto natives that unifies on-chain tools into a single AI + CLI + Telegram Bot interface. Think: one place to manage wallets, trade, read markets, and execute on-chain actions.

My current signal sources on X:
{account_lines}

Two questions:
1. **Who is missing?** Suggest 3-5 accounts I should add. Must be high-signal for: AI agents + on-chain execution, Telegram trading bots, CLI/developer tools, or Gen-Z crypto behavior. No VCs, no general crypto news.
2. **Who is low-signal?** Identify 1-2 accounts from my current list that have gone quiet or are less relevant for D0's focus.

Format your answer as:
ADD: @handle1, @handle2, @handle3
REMOVE: @handle1, @handle2
REASON: one sentence per suggestion"""


def run_grok_via_playwright(prompt: str) -> str:
    """
    用 Playwright 打开 x.com/i/grok，注入 cookie，提交 prompt，返回 Grok 响应文本。
    """
    import tempfile, os

    script_content = '''
import asyncio, json, sys
from playwright.async_api import async_playwright

AUTH_TOKEN = sys.argv[1]
CT0 = sys.argv[2]
PROMPT = sys.argv[3]

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        await context.add_cookies([
            {"name": "auth_token", "value": AUTH_TOKEN, "domain": ".x.com", "path": "/"},
            {"name": "ct0",        "value": CT0,        "domain": ".x.com", "path": "/"},
        ])

        page = await context.new_page()
        print("[grok] Navigating to x.com/i/grok...", flush=True)
        await page.goto("https://x.com/i/grok", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        print("[grok] Looking for input box...", flush=True)
        input_sel = 'textarea[placeholder], div[contenteditable="true"][data-testid]'
        try:
            await page.wait_for_selector(input_sel, timeout=15000)
        except Exception:
            input_sel = 'div[contenteditable="true"]'
            await page.wait_for_selector(input_sel, timeout=10000)

        await page.click(input_sel)
        await asyncio.sleep(0.5)
        await page.keyboard.type(PROMPT, delay=10)
        await asyncio.sleep(1)

        print("[grok] Submitting prompt...", flush=True)
        send_btn = 'button[data-testid="sendButton"], button[aria-label*="Send"]'
        try:
            await page.click(send_btn, timeout=3000)
        except Exception:
            await page.keyboard.press("Enter")

        print("[grok] Waiting for response...", flush=True)
        await asyncio.sleep(5)

        prev_text = ""
        stable_count = 0
        for _ in range(24):
            await asyncio.sleep(2.5)
            response_text = await page.evaluate("""() => {
                const els = document.querySelectorAll('[data-testid="bot-response-text"], [class*="grokResponse"]');
                if (els.length > 0) return els[els.length - 1].innerText || "";
                const msgs = document.querySelectorAll('[data-testid="messageEntry"]');
                if (msgs.length > 0) return msgs[msgs.length - 1].innerText || "";
                return "";
            }""")
            if response_text and response_text == prev_text:
                stable_count += 1
                if stable_count >= 2:
                    break
            else:
                stable_count = 0
                prev_text = response_text

        await browser.close()
        return prev_text

response = asyncio.run(run())
print("---GROK_RESPONSE_START---")
print(response)
print("---GROK_RESPONSE_END---")
'''

    # 写入临时脚本文件
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(script_content)
        tmp_path = f.name

    try:
        result = subprocess.run(
            ["python3", tmp_path, BIRD_AUTH_TOKEN, BIRD_CT0, prompt],
            capture_output=True, text=True, timeout=120
        )
        output = result.stdout + result.stderr

        if "---GROK_RESPONSE_START---" in output:
            start = output.index("---GROK_RESPONSE_START---") + len("---GROK_RESPONSE_START---\n")
            end = output.index("---GROK_RESPONSE_END---")
            return output[start:end].strip()

        print(f"[WARN] Could not parse Grok output:\n{output[:500]}", file=sys.stderr)
        return ""

    except subprocess.TimeoutExpired:
        print("[ERROR] Grok browser session timed out (>120s)", file=sys.stderr)
        return ""
    except Exception as e:
        print(f"[ERROR] Playwright error: {e}", file=sys.stderr)
        return ""
    finally:
        os.unlink(tmp_path)


def check_playwright_installed() -> bool:
    """检查 Playwright 是否已安装"""
    try:
        result = subprocess.run(
            ["python3", "-c", "from playwright.async_api import async_playwright; print('ok')"],
            capture_output=True, text=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def push_to_slack(message: str):
    """推送结果到 Slack"""
    try:
        subprocess.run(
            ["openclaw", "message", "send",
             "--channel", "slack",
             "--to", "U0ACEKZ4F52",
             "--message", message],
            timeout=15
        )
    except Exception as e:
        print(f"[WARN] Slack push failed: {e}", file=sys.stderr)


def apply_changes(accounts: list[dict], grok_response: str):
    """交互式更新 seed_accounts.yaml"""
    print("\n--- Grok 建议 ---")
    print(grok_response)
    print("─" * 50)

    add_input = input("\n要添加的账号（逗号分隔，或 Enter 跳过）: ").strip()
    remove_input = input("要移除的账号（逗号分隔，或 Enter 跳过）: ").strip()

    if not add_input and not remove_input:
        print("无变更。")
        return

    with open(SEED_FILE) as f:
        config = yaml.safe_load(f)

    if remove_input:
        to_remove = {h.strip().lstrip("@").lower() for h in remove_input.split(",")}
        before = len(config["seed_accounts"])
        config["seed_accounts"] = [
            a for a in config["seed_accounts"]
            if a["handle"].lstrip("@").lower() not in to_remove
        ]
        print(f"  移除了 {before - len(config['seed_accounts'])} 个账号")

    if add_input:
        for handle in add_input.split(","):
            handle = handle.strip()
            if not handle.startswith("@"):
                handle = f"@{handle}"
            config["seed_accounts"].append({
                "handle": handle,
                "category": "Other",
                "reason": f"Added via Grok calibration {date.today().isoformat()}"
            })
        print(f"  新增了 {len(add_input.split(','))} 个账号")

    with open(SEED_FILE, "w") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)
    print(f"  ✓ seed_accounts.yaml 已更新（共 {len(config['seed_accounts'])} 个账号）")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="交互式更新 seed_accounts.yaml")
    parser.add_argument("--dry-run", action="store_true", help="只打印 prompt，不打开浏览器")
    args = parser.parse_args()

    accounts = load_seed_accounts()
    prompt = build_grok_prompt(accounts)

    print("[calibrate] Workflow B — Monthly Seed Account Calibration via Grok")
    print(f"  Accounts: {len(accounts)}")
    print(f"  Date: {date.today().isoformat()}")
    print("")

    if args.dry_run:
        print("=== Grok Prompt (dry-run) ===")
        print(prompt)
        print("=" * 40)
        sys.exit(0)

    # 检查 Playwright
    if not check_playwright_installed():
        print("[ERROR] Playwright not installed.", file=sys.stderr)
        print("Install with: pip3 install playwright && python3 -m playwright install chromium", file=sys.stderr)
        print("\nFallback — paste this prompt manually at x.com/i/grok:")
        print("─" * 50)
        print(prompt)
        sys.exit(1)

    print("[calibrate] Launching browser → x.com/i/grok...")
    response = run_grok_via_playwright(prompt)

    if not response:
        print("[ERROR] No response from Grok.", file=sys.stderr)
        print("Manual prompt:")
        print(prompt)
        sys.exit(1)

    # 保存响应
    out_file = OUTPUT_DIR / f"{date.today().isoformat()}_grok_calibration.txt"
    with open(out_file, "w") as f:
        f.write(f"PROMPT:\n{prompt}\n\n---\n\nGROK RESPONSE:\n{response}\n")
    print(f"[calibrate] Response saved → {out_file.name}")

    # 推送到 Slack
    slack_msg = f"📡 *Trend Radar 月度校准 — Grok 建议*\n日期：{date.today().isoformat()}\n\n{response}\n\n_如需更新账号列表，运行：`python3 calibrate_grok.py --apply`_"
    push_to_slack(slack_msg)
    print("[calibrate] Pushed to Slack ✓")

    if args.apply:
        apply_changes(accounts, response)


if __name__ == "__main__":
    main()
