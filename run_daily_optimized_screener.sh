#!/bin/bash
# ==============================================================================
# run_daily_optimized_screener.sh
# 每日量化優化旗艦選股器 (Optimized Alpha Screener) 自動化更新腳本
#
# 使用方式:
#   ./run_daily_optimized_screener.sh              # 執行本機選股與生成 HTML/CSV
#   ./run_daily_optimized_screener.sh --publish    # 執行並自動 git commit & push 到 GitHub Pages
#   ./run_daily_optimized_screener.sh --mode screen --publish # 單獨運行 screen 旗艦並發布
# ==============================================================================

set -uo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BASE_DIR" || exit 1

LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/daily_optimized_screener.log"

ts() { date "+%Y-%m-%d %H:%M:%S"; }
log() { echo "[$(ts)] $*" | tee -a "$LOG_FILE"; }

# 選擇 Python 執行環境 (優先使用 macro-dashboard venv，內建 numpy 支援日線極速同步)
if [ -x "/Users/eric/macro-dashboard/.venv/bin/python" ]; then
    PY="/Users/eric/macro-dashboard/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
else
    log "ERROR: 找不到可用的 Python 執行檔！"
    exit 1
fi

DO_PUBLISH=0
PASS_ARGS=()

for arg in "$@"; do
    if [ "$arg" = "--publish" ]; then
        DO_PUBLISH=1
    else
        PASS_ARGS+=("$arg")
    fi
done

log "=================================================================="
log "開始執行 Optimized Alpha Screener 每日量化選股作業..."
log "執行路徑: $BASE_DIR"
log "使用 Python: $PY"

# 1. 執行選股器生成最新報告
if [ ${#PASS_ARGS[@]} -gt 0 ]; then
    "$PY" "$BASE_DIR/optimized_alpha_screener.py" "${PASS_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
    RC=$?
else
    "$PY" "$BASE_DIR/optimized_alpha_screener.py" 2>&1 | tee -a "$LOG_FILE"
    RC=$?
fi

if [ $RC -ne 0 ]; then
    log "ERROR: optimized_alpha_screener.py 執行失敗 (代碼: $RC)"
    exit $RC
fi

log "選股與圖表生成順利完成！"

# 2. 自動發布至 GitHub Pages (若帶有 --publish 參數或環境變數 SCREENER_PUBLISH_ENABLED=1)
if [ "$DO_PUBLISH" -eq 1 ] || [ "${SCREENER_PUBLISH_ENABLED:-0}" = "1" ]; then
    log "正在將最新選股成果推播至 GitHub Pages..."
    
    # 確保 git 倉庫狀態
    if [ -d "$BASE_DIR/.git" ]; then
        git add optimized_screener_report.html optimized_candidates.csv \
                optimized_alpha_screener.py daily_ohlcv_cache.json \
                run_daily_optimized_screener.sh 2>&1 | tee -a "$LOG_FILE"
        
        if [ -d "$BASE_DIR/latest" ]; then
            git add latest/optimized_screener_report.html latest/optimized_candidates.csv 2>&1 | tee -a "$LOG_FILE" || true
        fi

        TODAY=$(date "+%Y-%m-%d")
        if ! git diff --cached --quiet; then
            git commit -m "Auto-update Optimized Alpha Screener report ${TODAY}" 2>&1 | tee -a "$LOG_FILE"
            git push origin main 2>&1 | tee -a "$LOG_FILE"
            log "✅ 成功發布至 GitHub Pages！"
        else
            log "ℹ️ 報告內容已是最新，無需產生新的 git commit。"
        fi
    else
        log "WARN: $BASE_DIR 不是一個 Git 倉庫，略過 push 步驟。"
    fi
fi

# 日誌輪轉 (超過 1000 行自動裁切)
if [ -f "$LOG_FILE" ] && [ "$(wc -l < "$LOG_FILE")" -gt 1000 ]; then
    tail -500 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
fi

log "Optimized Alpha Screener 作業結束。"
log "=================================================================="
exit 0
