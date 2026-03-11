#!/bin/bash
# run_workflow_a.sh — D0 Trending Digest 完整流水线
# Workflow A: 抓取 → 过滤 → LLM分析 → 格式化 → 推送 Slack
#
# 用法：
#   ./run_workflow_a.sh              # 正常运行
#   ./run_workflow_a.sh --dry-run    # 只跑第一个账号测试
#   ./run_workflow_a.sh --no-push    # 不推送，只生成 digest

set -euo pipefail

WORKSPACE="$(cd "$(dirname "$0")" && pwd)"
DATE=$(date +%Y-%m-%d)
DRY_RUN=""
NO_PUSH=false
SLACK_TARGET="U0ACEKZ4F52"

for arg in "$@"; do
  case $arg in
    --dry-run) DRY_RUN="--dry-run" ;;
    --no-push) NO_PUSH=true ;;
  esac
done

# ─── 错误通知函数 ──────────────────────────────────────────────────────────
notify_error() {
  local step="$1"
  local detail="${2:-}"
  local msg="❌ *Trend Radar 流水线失败*
日期：\`$DATE\`
步骤：$step
${detail:+详情：$detail}"

  openclaw message send \
    --channel slack \
    --to "$SLACK_TARGET" \
    --message "$msg" 2>/dev/null || true
}

# ─── trap 捕获意外退出 ─────────────────────────────────────────────────────
CURRENT_STEP="unknown"
trap 'if [ $? -ne 0 ]; then notify_error "$CURRENT_STEP" "非预期退出，请检查日志"; fi' EXIT

echo "=== D0 Trend Radar — Workflow A ==="
echo "Date: $DATE"
echo ""

# Workflow B: Grok 校准（每天在 Workflow A 之前运行）
CURRENT_STEP="Workflow B: Grok Calibration"
echo "--- Workflow B: Grok Calibration ---"
python3 "$WORKSPACE/calibrate_grok.py" || {
  echo "[WARN] Grok calibration failed or no response — continuing with existing seed list"
}
echo ""

# Step 1: 抓取 + 过滤
CURRENT_STEP="Step 1: Collect & Filter"
echo "--- Step 1: Collect & Filter ---"
collector_exit=0
python3 "$WORKSPACE/collector.py" $DRY_RUN || collector_exit=$?

if [ $collector_exit -eq 2 ]; then
  # exit code 2 = 空结果（非脚本错误，但需要告警）
  notify_error "Step 1: Collect & Filter" "所有推文均未通过过滤条件，今日 Digest 为空"
  echo "[WARN] No tweets passed filters. Aborting pipeline."
  trap - EXIT  # 不触发 EXIT trap（已手动处理）
  exit 0
elif [ $collector_exit -ne 0 ]; then
  notify_error "Step 1: Collect & Filter" "collector.py 异常退出（code $collector_exit）"
  trap - EXIT
  exit 1
fi

# Step 2: LLM 分析
CURRENT_STEP="Step 2: LLM Analysis"
echo ""
echo "--- Step 2: LLM Analysis ---"
if ! python3 "$WORKSPACE/analyzer.py" --date "$DATE"; then
  notify_error "Step 2: LLM Analysis" "analyzer.py 失败"
  trap - EXIT
  exit 1
fi

# Step 3: 格式化
CURRENT_STEP="Step 3: Format Digest"
echo ""
echo "--- Step 3: Format Digest ---"
if ! python3 "$WORKSPACE/formatter.py" --date "$DATE"; then
  notify_error "Step 3: Format Digest" "formatter.py 失败"
  trap - EXIT
  exit 1
fi

# Step 4: 推送到 Slack
CURRENT_STEP="Step 4: Push to Slack"
if [ "$NO_PUSH" = false ]; then
  echo ""
  echo "--- Step 4: Push to Slack ---"
  DIGEST_FILE="$WORKSPACE/output/digest/${DATE}_digest.md"
  if [ -f "$DIGEST_FILE" ]; then
    if ! python3 "$WORKSPACE/push_slack.py" --date "$DATE"; then
      notify_error "Step 4: Push to Slack" "push_slack.py 失败，digest 已生成但未推送"
      trap - EXIT
      exit 1
    fi
  else
    notify_error "Step 4: Push to Slack" "digest 文件不存在：${DATE}_digest.md"
    echo "[WARN] No digest file found, skipping push"
  fi
fi

trap - EXIT  # 正常完成，不触发错误通知
echo ""
echo "=== Done ==="
echo "Output: $WORKSPACE/output/digest/${DATE}_digest.md"
