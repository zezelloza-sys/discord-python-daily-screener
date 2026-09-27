#!/usr/bin/env python3
"""
Optimized Alpha Screener (優化版量化動能與結構選股器)
=====================================================
結合紅隊量化審計成果的全新策略篩選器：
1. 結構條件：
   - screen (築底突破): L1/L2 通道阻力翻多突破
   - claude_fibb (動能通道): 12-1 價格動能強勢股回踩通道邊界
2. 因子硬過濾：
   - 條件 1：前期 20 日高點回踩深度鎖定 Q3 黃金甜蜜點 [-8.5%, -4.0%]
   - 條件 2：成交量放量確認 RVOL >= 1.0 (當日量 >= 20日均量)，防禦 42% 無量陰跌陷阱
3. 實盤執行與風控：
   - 動態 ATR 停損 (Stop Distance = 2.0 * ATR)
   - T+5 滯脹強制砍半，T+10 Alpha 半衰期強制清倉
   - 自動生成高解析度 K 棒 (Candlestick) 互動式 HTML 視覺化報告
"""

from __future__ import annotations

import argparse
import csv
import datetime
import glob
import json
import math
import os
import re
import shutil
import statistics
import sys
from collections import defaultdict


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MACRO_OUT_DIR = "/Users/eric/macro-dashboard/screener/out"
MACRO_BARS_DIR = "/Users/eric/macro-dashboard/screener/cache/bars"


def load_cached_ohlcv(cache_file: str | None = None) -> dict[str, dict]:
    """載入 3 個月日線 OHLCV 快取數據，並在可能時自 macro-dashboard bars 同步增量。"""
    if cache_file is None:
        cache_file = os.path.join(BASE_DIR, "daily_ohlcv_cache.json")
    if not os.path.exists(cache_file):
        raise FileNotFoundError(f"找不到 OHLCV 快取檔案: {cache_file}")

    with open(cache_file, "r", encoding="utf-8") as fp:
        raw = json.load(fp)

    # 嘗試同步 macro-dashboard 的最新 npz 日線
    if os.path.isdir(MACRO_BARS_DIR):
        try:
            import numpy as np

            updated = False
            for sym, existing_bars in raw.items():
                npz_path = os.path.join(MACRO_BARS_DIR, f"{sym}.npz")
                if not os.path.exists(npz_path):
                    continue
                d = np.load(npz_path)
                dates = d["d"]
                if len(dates) > 0:
                    latest_npz_d = str(dates[-1])
                    if not existing_bars or latest_npz_d > existing_bars[-1]["date"]:
                        opens, highs, lows, closes, vols = (
                            d["o"],
                            d["h"],
                            d["l"],
                            d["c"],
                            d["v"],
                        )
                        start_idx = max(0, len(dates) - 65)
                        new_bars = []
                        for i in range(start_idx, len(dates)):
                            new_bars.append({
                                "date": str(dates[i]),
                                "open": round(float(opens[i]), 2),
                                "high": round(float(highs[i]), 2),
                                "low": round(float(lows[i]), 2),
                                "close": round(float(closes[i]), 2),
                                "volume": int(vols[i]),
                            })
                        raw[sym] = new_bars
                        updated = True
            if updated:
                with open(cache_file, "w", encoding="utf-8") as fp:
                    json.dump(raw, fp)
        except Exception:
            pass

    db = {}
    for sym, bars in raw.items():
        sorted_bars = sorted(bars, key=lambda b: b["date"])
        db[sym] = {
            "list": sorted_bars,
            "dates": [b["date"] for b in sorted_bars],
            "by_date": {b["date"]: b for b in sorted_bars},
        }
    return db


SECTORS_CSV = "/Users/eric/macro-dashboard/screener/cache/sectors.csv"
SECTOR_CUR_CSV = "/Users/eric/macro-dashboard/screener/out/institutional_sector_rotation.csv"
SECTOR_HIST_CSV = "/Users/eric/macro-dashboard/screener/history/institutional_sector_rotation_history.csv"

CLUSTER_CUR_CSV = "/Users/eric/macro-dashboard/screener/out/institutional_cluster_rotation.csv"
CLUSTER_HIST_CSV = "/Users/eric/macro-dashboard/screener/history/institutional_cluster_rotation_history.csv"

sys.path.insert(0, "/Users/eric/macro-dashboard/screener")
try:
    import clusters as CLUST
except Exception:
    CLUST = None


def load_sector_rotation_data() -> tuple[dict, dict, dict, dict, dict]:
    """載入全市場標的板塊與聚落對照表，以及歷史與即時板塊/聚落輪動評分與狀態。"""
    sec_map = {}
    if os.path.exists(SECTORS_CSV):
        try:
            with open(SECTORS_CSV, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    sec_map[r["symbol"]] = r
        except Exception:
            pass

    sec_cur = {}
    if os.path.exists(SECTOR_CUR_CSV):
        try:
            with open(SECTOR_CUR_CSV, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    sec_name = r.get("sector") or (r.get("group") if r.get("level") == "sector" else None)
                    if sec_name:
                        r["phase"] = r.get("regime") or r.get("phase", "中性觀察")
                        sec_cur[sec_name] = r
        except Exception:
            pass

    sec_hist = {}
    if os.path.exists(SECTOR_HIST_CSV):
        try:
            with open(SECTOR_HIST_CSV, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    sec_name = r.get("sector") or (r.get("group") if r.get("level") == "sector" else None)
                    if sec_name:
                        r["phase"] = r.get("regime") or r.get("phase", "中性觀察")
                        sec_hist[(r["asof"], sec_name)] = r
        except Exception:
            pass

    cluster_cur = {}
    if os.path.exists(CLUSTER_CUR_CSV):
        try:
            with open(CLUSTER_CUR_CSV, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    c_name = r.get("cluster")
                    if c_name:
                        cluster_cur[c_name] = r
        except Exception:
            pass

    cluster_hist = {}
    if os.path.exists(CLUSTER_HIST_CSV):
        try:
            with open(CLUSTER_HIST_CSV, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    c_name = r.get("cluster")
                    if c_name:
                        cluster_hist[(r["asof"], c_name)] = r
        except Exception:
            pass

    return sec_map, sec_cur, sec_hist, cluster_cur, cluster_hist


def load_all_signals(db: dict[str, dict]) -> list[dict]:
    """從歷史報表、latest、out CSV 與追蹤表記錄中加載所有候選標的與基礎元數據。"""
    signals = {}

    # 1. screener_returns_5d_20d.html
    for f in sorted(glob.glob(os.path.join(BASE_DIR, "2026-*/screener_returns_5d_20d.html"))):
        with open(f, "r", encoding="utf-8") as fp:
            text = fp.read()
        rows = re.findall(
            r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td><td><b>(.*?)</b></td><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td><td[^>]*>(.*?)</td><td>(.*?)</td></tr>",
            text,
        )
        for r in rows:
            key = (r[2].strip(), r[1].strip(), r[3].strip())
            if key not in signals:
                try:
                    signals[key] = {
                        "screener": r[2].strip(),
                        "sig_date": r[1].strip(),
                        "ticker": r[3].strip(),
                        "sector": r[4].strip(),
                        "p_start": float(r[5].replace(",", "")),
                        "source": "returns_table",
                    }
                except:
                    pass

    # 2. Daily & latest HTML files
    file_map = {
        "screen": "candidates.html",
        "bull_bounce": "bull_bounce.html",
        "claude_fibb": "claude_fibb.html",
        "mtf4": "mtf4.html",
    }
    scan_folders = sorted(glob.glob(os.path.join(BASE_DIR, "2026-*")))
    latest_folder = os.path.join(BASE_DIR, "latest")
    if os.path.isdir(latest_folder):
        scan_folders.append(latest_folder)

    for folder in scan_folders:
        folder_date = os.path.basename(folder)
        for screener, fname in file_map.items():
            fpath = os.path.join(folder, fname)
            if not os.path.exists(fpath):
                continue
            with open(fpath, "r", encoding="utf-8") as fp:
                text = fp.read()
            m_date = re.search(r"資料至 <b>(.*?)</b>", text) or re.search(
                r"PATTERN SCREENER · ([\d-]+)", text
            )
            sig_date = m_date.group(1) if m_date else folder_date
            if sig_date == "latest":
                sig_date = datetime.date.today().strftime("%Y-%m-%d")
            cards = re.findall(
                r'<div class=[\'"]chead[\'"].*?</div>\s*</div>', text, re.DOTALL
            )
            for c in cards:
                sym_m = re.search(r'<span class=[\'"]sym[\'"]>([A-Za-z0-9]+)</span>', c)
                if not sym_m:
                    continue
                sym = sym_m.group(1).strip()
                px_m = re.search(r'<span class=[\'"]px[\'"]>([\d\.,]+)</span>', c)
                sc_m = re.search(r'<span class=[\'"]sc[\'"]>([\d\.]+)</span>', c)
                nm_m = re.search(
                    r'<div class=[\'"]nm[\'"](?:\s+title=[\'"](.*?)[\'"])?>(.*?)</div>',
                    c,
                )
                space_m = re.search(r"空間 <b>([+\-\d\.]+)%</b>", c)
                dist_m = re.search(r"距(?:停損|L1|高時區層)\s*<b>([\d\.]+)\s*ATR</b>", c)
                rr_m = re.search(r"R:R <b>([\d\.]+)</b>", c)

                try:
                    px = float(px_m.group(1).replace(",", "")) if px_m else None
                except:
                    px = None
                key = (screener, sig_date, sym)
                meta = {
                    "score": float(sc_m.group(1)) if sc_m else None,
                    "space": float(space_m.group(1)) if space_m else None,
                    "dist_atr": float(dist_m.group(1)) if dist_m else None,
                    "rr": float(rr_m.group(1)) if rr_m else None,
                    "name": nm_m.group(1)
                    if nm_m and nm_m.group(1)
                    else (nm_m.group(2) if nm_m else ""),
                    "sector": nm_m.group(2) if nm_m and nm_m.group(2) else "",
                }
                if key not in signals and px:
                    signals[key] = {
                        "screener": screener,
                        "sig_date": sig_date,
                        "ticker": sym,
                        "sector": meta["sector"],
                        "name": meta["name"],
                        "p_start": px,
                        "source": "daily_html",
                        **meta,
                    }
                elif key in signals:
                    signals[key].update(meta)

    # 3. 若存在 macro-dashboard/screener/out，同步載入本日最新產出的 CSV
    if os.path.isdir(MACRO_OUT_DIR):
        csv_specs = [
            ("screen", "results.csv"),
            ("bull_bounce", "bull_bounce.csv"),
            ("claude_fibb", "claude_fibb.csv"),
            ("mtf4", "mtf4.csv"),
        ]
        for screener_name, csv_fn in csv_specs:
            csv_path = os.path.join(MACRO_OUT_DIR, csv_fn)
            if not os.path.exists(csv_path):
                continue
            try:
                with open(csv_path, "r", encoding="utf-8-sig") as fp:
                    reader = csv.DictReader(fp)
                    for row in reader:
                        sym = row.get("symbol", "").strip()
                        if not sym:
                            continue
                        if screener_name == "screen" and row.get("stage") != "FIRED":
                            continue
                        asof = row.get("asof", "").strip() or datetime.date.today().strftime("%Y-%m-%d")
                        px_val = float(row.get("px", 0)) if row.get("px") else None
                        if not px_val:
                            continue
                        head_pct = float(row.get("head_pct", 0)) if row.get("head_pct") else None
                        key = (screener_name, asof, sym)
                        if key not in signals:
                            signals[key] = {
                                "screener": screener_name,
                                "sig_date": asof,
                                "ticker": sym,
                                "sector": row.get("sector", ""),
                                "name": row.get("name", ""),
                                "p_start": px_val,
                                "space": head_pct,
                                "source": "macro_out_csv",
                            }
            except Exception:
                pass

    return list(signals.values())


def calculate_quant_factors(
    signal: dict,
    db: dict[str, dict],
    sec_map: dict | None = None,
    sec_cur: dict | None = None,
    sec_hist: dict | None = None,
    cluster_cur: dict | None = None,
    cluster_hist: dict | None = None,
) -> dict | None:
    """計算前期回踩、ATR、RVOL、開盤缺口及動態板塊輪動指標。"""
    sym = signal["ticker"]
    sig_date = signal["sig_date"]
    p_start = signal["p_start"]

    if sym not in db or p_start <= 0:
        return None

    t_data = db[sym]
    dates = t_data["dates"]
    sig_idx = next((i for i, d in enumerate(dates) if d == sig_date), None)
    if sig_idx is None or sig_idx < 15:
        return None

    bars = t_data["list"]
    sig_bar = bars[sig_idx]
    prior_bars = bars[max(0, sig_idx - 20) : sig_idx]

    v_sig = sig_bar["volume"]
    if not v_sig or v_sig <= 0:
        return None

    v_20_list = [b["volume"] for b in prior_bars if b["volume"] and b["volume"] > 0]
    if len(v_20_list) < 5:
        return None
    v_avg20 = statistics.mean(v_20_list)
    rvol = v_sig / v_avg20

    # 20日高點回踩深度
    high_20d = max(b["high"] for b in prior_bars)
    prior_pullback = (p_start - high_20d) / high_20d * 100.0 if high_20d > 0 else 0.0

    # 當日開盤缺口 (Gap %)
    prev_close = prior_bars[-1]["close"]
    open_price = sig_bar["open"]
    gap_pct = round((open_price - prev_close) / prev_close * 100.0, 2) if prev_close > 0 else 0.0

    # 前期 14 日 ATR
    trs = [
        max(
            prior_bars[i]["high"] - prior_bars[i]["low"],
            abs(prior_bars[i]["high"] - prior_bars[i - 1]["close"]),
            abs(prior_bars[i]["low"] - prior_bars[i - 1]["close"]),
        )
        for i in range(1, len(prior_bars))
    ]
    atr14 = (sum(trs[-14:]) / min(len(trs), 14)) if trs else (p_start * 0.03)
    atr_pct = (atr14 / p_start) * 100.0 if p_start > 0 else 3.0

    # 動態風控參數
    stop_distance = max(2.0 * atr14, p_start * 0.04)
    stop_price = round(max(0.01, p_start - stop_distance), 2)
    stop_loss_pct = round(-stop_distance / p_start * 100.0, 2)

    target_space = signal.get("space") or (atr_pct * 3.5)
    target_price = round(p_start * (1.0 + target_space / 100.0), 2)
    risk_reward = round(abs(target_space / stop_loss_pct), 2) if stop_loss_pct != 0 else 2.5

    # 板塊對齊與動態輪動狀態
    canonical_sector = signal.get("sector") or ""
    canonical_industry = signal.get("name") or ""
    if sec_map and sym in sec_map:
        s_row = sec_map[sym]
        canonical_sector = s_row.get("sector") or canonical_sector
        canonical_industry = s_row.get("industry") or canonical_industry

    sec_info = {}
    if sec_hist and (sig_date, canonical_sector) in sec_hist:
        sec_info = sec_hist[(sig_date, canonical_sector)]
    elif sec_cur and canonical_sector in sec_cur:
        sec_info = sec_cur[canonical_sector]

    sector_phase = sec_info.get("phase", "中性觀察")
    sector_score = float(sec_info.get("rotation_score", 50.0)) if sec_info.get("rotation_score") else 50.0

    # 細分行業聚落對齊與動態輪動狀態
    canonical_cluster = CLUST.map_industry_to_cluster(canonical_sector, canonical_industry) if CLUST else "Unknown: Other"
    clust_info = {}
    if cluster_hist and (sig_date, canonical_cluster) in cluster_hist:
        clust_info = cluster_hist[(sig_date, canonical_cluster)]
    elif cluster_cur and canonical_cluster in cluster_cur:
        clust_info = cluster_cur[canonical_cluster]

    cluster_regime = clust_info.get("cluster_regime", "⚖️ 震盪蓄勢 (Consolidation)")
    cluster_score = float(clust_info.get("cluster_score", 50.0)) if clust_info.get("cluster_score") else 50.0

    # 前瞻績效 (如果有後續行情)
    forward_bars = bars[sig_idx + 1 :]
    ret_20d, mfe_20d, mae_20d, t_mfe, final_ret = None, None, None, None, None
    if forward_bars:
        final_ret = round((forward_bars[-1]["close"] - p_start) / p_start * 100.0, 2)
        b20 = forward_bars[:20]
        ret_20d = round((b20[-1]["close"] - p_start) / p_start * 100.0, 2)
        highs = [b["high"] for b in b20]
        lows = [b["low"] for b in b20]
        max_h = max(highs)
        min_l = min(lows)
        mfe_20d = round((max_h - p_start) / p_start * 100.0, 2)
        mae_20d = round((min_l - p_start) / p_start * 100.0, 2)
        t_mfe = highs.index(max_h) + 1

    return {
        **signal,
        "sector": canonical_sector,
        "industry": canonical_industry,
        "sector_phase": sector_phase,
        "sector_score": sector_score,
        "cluster": canonical_cluster,
        "cluster_regime": cluster_regime,
        "cluster_score": cluster_score,
        "gap_pct": gap_pct,
        "rvol": round(rvol, 2),
        "v_sig": v_sig,
        "v_avg20": round(v_avg20, 0),
        "prior_pullback": round(prior_pullback, 2),
        "atr14": round(atr14, 2),
        "atr_pct": round(atr_pct, 2),
        "stop_price": stop_price,
        "stop_loss_pct": stop_loss_pct,
        "target_price": target_price,
        "target_space": round(target_space, 1),
        "risk_reward": risk_reward,
        "ret_20d": ret_20d,
        "mfe_20d": mfe_20d,
        "mae_20d": mae_20d,
        "t_mfe": t_mfe,
        "final_ret": final_ret,
        "sig_idx": sig_idx,
        "bars": bars,
    }


def generate_svg_candlestick(item: dict, width: int = 580, height: int = 200) -> str:
    """生成輕量級高解析度純向量 SVG K 棒圖表 (含成交量副圖與進出場標記)。"""
    bars = item["bars"]
    sig_idx = item["sig_idx"]

    # 視窗：訊號前 15 根 + 訊號後至多 20 根
    start_i = max(0, sig_idx - 14)
    end_i = min(len(bars), sig_idx + 21)
    view_bars = bars[start_i:end_i]
    rel_sig_i = sig_idx - start_i
    n = len(view_bars)
    if n == 0:
        return ""

    chart_h = height * 0.70
    vol_h = height * 0.20
    vol_top = height * 0.76

    min_p = min(b["low"] for b in view_bars)
    max_p = max(b["high"] for b in view_bars)
    p_range = max(max_p - min_p, 0.001)

    max_v = max(b["volume"] for b in view_bars if b["volume"])
    v_range = max(max_v, 1)

    candle_w = max(2, int((width - 40) / n * 0.65))
    step = (width - 40) / n

    elements = []
    # 網格參考線
    elements.append(
        f'<line x1="20" y1="{chart_h:.1f}" x2="{width-20}" y2="{chart_h:.1f}" stroke="#334155" stroke-dasharray="3,3"/>'
    )
    elements.append(
        f'<line x1="20" y1="{vol_top:.1f}" x2="{width-20}" y2="{vol_top:.1f}" stroke="#334155"/>'
    )

    # 停損與目標價參考虛線
    p_start = item["p_start"]
    stop_p = item["stop_price"]
    tgt_p = item["target_price"]
    if min_p <= stop_p <= max_p:
        y_stop = chart_h - (stop_p - min_p) / p_range * (chart_h - 20)
        elements.append(
            f'<line x1="20" y1="{y_stop:.1f}" x2="{width-20}" y2="{y_stop:.1f}" stroke="#ef4444" stroke-dasharray="2,2" opacity="0.6"/>'
        )
    if min_p <= tgt_p <= max_p:
        y_tgt = chart_h - (tgt_p - min_p) / p_range * (chart_h - 20)
        elements.append(
            f'<line x1="20" y1="{y_tgt:.1f}" x2="{width-20}" y2="{y_tgt:.1f}" stroke="#10b981" stroke-dasharray="2,2" opacity="0.6"/>'
        )

    for i, b in enumerate(view_bars):
        x = 25 + i * step
        y_high = chart_h - (b["high"] - min_p) / p_range * (chart_h - 20)
        y_low = chart_h - (b["low"] - min_p) / p_range * (chart_h - 20)
        y_open = chart_h - (b["open"] - min_p) / p_range * (chart_h - 20)
        y_close = chart_h - (b["close"] - min_p) / p_range * (chart_h - 20)

        is_up = b["close"] >= b["open"]
        color = "#10b981" if is_up else "#ef4444"
        if i == rel_sig_i:
            color = "#f59e0b"  # 觸發 K 棒標金黃色

        # 影線
        elements.append(
            f'<line x1="{x:.1f}" y1="{y_high:.1f}" x2="{x:.1f}" y2="{y_low:.1f}" stroke="{color}" stroke-width="1.2"/>'
        )

        # 實體
        b_top = min(y_open, y_close)
        b_h = max(1.5, abs(y_close - y_open))
        elements.append(
            f'<rect x="{x - candle_w/2:.1f}" y="{b_top:.1f}" width="{candle_w}" height="{b_h:.1f}" fill="{color}"/>'
        )

        # 成交量柱狀
        v_bar_h = (b["volume"] / v_range) * vol_h if b["volume"] else 1
        y_vol = height - v_bar_h
        elements.append(
            f'<rect x="{x - candle_w/2:.1f}" y="{y_vol:.1f}" width="{candle_w}" height="{v_bar_h:.1f}" fill="{color}" opacity="0.75"/>'
        )

        # 買入訊號標記 (BUY Arrow)
        if i == rel_sig_i:
            elements.append(
                f'<polygon points="{x:.1f},{chart_h+6:.1f} {x-5:.1f},{chart_h+16:.1f} {x+5:.1f},{chart_h+16:.1f}" fill="#f59e0b"/>'
            )
            elements.append(
                f'<text x="{x:.1f}" y="{chart_h+26:.1f}" fill="#f59e0b" font-size="9" text-anchor="middle" font-weight="bold">BUY</text>'
            )

    return f'<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" style="background:#090d16;border-radius:6px;">{" ".join(elements)}</svg>'


def build_html_report(qualifying: list[dict], output_file: str) -> None:
    """生成現代化、響應式、含互動篩選的 K 棒選股報告。"""
    # 統計摘要
    tot = len(qualifying)
    mature = [q for q in qualifying if q.get("ret_20d") is not None]
    avg_ret = statistics.mean([m["ret_20d"] for m in mature]) if mature else 0
    win_rate = (
        sum(1 for m in mature if m["ret_20d"] > 0) / len(mature) * 100 if mature else 0
    )
    avg_mfe = statistics.mean([m["mfe_20d"] for m in mature]) if mature else 0
    avg_mae = statistics.mean([m["mae_20d"] for m in mature]) if mature else 0
    ratio = abs(avg_mfe / avg_mae) if avg_mae != 0 else 0

    cards_html = []
    for item in qualifying:
        svg_chart = generate_svg_candlestick(item)
        ret_val = item.get("ret_20d")
        ret_badge = (
            f'<span class="badge pos">20D +{ret_val:.1f}%</span>'
            if (ret_val is not None and ret_val > 0)
            else (
                f'<span class="badge neg">20D {ret_val:.1f}%</span>'
                if ret_val is not None
                else '<span class="badge neu">最新跟蹤中</span>'
            )
        )

        mfe_val = item.get("mfe_20d")
        mae_val = item.get("mae_20d")
        mfe_mae_str = (
            f'<b style="color:#10b981;">+{mfe_val:.1f}%</b> / <b style="color:#ef4444;">{mae_val:.1f}%</b>'
            if (mfe_val is not None and mae_val is not None)
            else '<span style="color:var(--sub);">持倉跟蹤中</span>'
        )

        sc_name = item["screener"]
        tag_color = (
            "#3b82f6"
            if sc_name == "screen"
            else (
                "#8b5cf6"
                if sc_name == "claude_fibb"
                else "#f59e0b"
                if sc_name == "bull_bounce"
                else "#ec4899"
            )
        )
        sec_phase = item.get("sector_phase", "中性觀察")
        sec_score = item.get("sector_score", 50.0)
        phase_color = (
            "#10b981"
            if ("Leading" in sec_phase or "Improving" in sec_phase or sec_phase in ("確認輪動", "主升／擴散"))
            else (
                "#ef4444"
                if ("Lagging" in sec_phase or "Weakening" in sec_phase or sec_phase in ("衰退", "擁擠／延伸"))
                else "#3b82f6"
            )
        )
        phase_icon = (
            "⭐"
            if ("Leading" in sec_phase or "Improving" in sec_phase or sec_phase in ("確認輪動", "主升／擴散"))
            else ("⚠️" if ("Lagging" in sec_phase or "Weakening" in sec_phase or sec_phase in ("衰退", "擁擠／延伸")) else "⚖️")
        )

        gap_val = item.get("gap_pct", 0.0)
        gap_color = "#10b981" if gap_val >= 0.2 else ("#f59e0b" if gap_val >= 0 else "#ef4444")

        c_name = item.get("cluster", "")
        c_reg = item.get("cluster_regime", "")
        c_score = item.get("cluster_score", 50.0)
        c_color = "#10b981" if "Breakout" in c_reg else ("#58a6ff" if "Ignition" in c_reg else ("#ef4444" if "Breakdown" in c_reg else "#3b82f6"))

        card = f"""
        <div class="card" data-screener="{sc_name}" data-sector="{item.get('sector','')}" data-cluster="{c_name}" data-phase="{sec_phase}" data-ret="{ret_val if ret_val is not None else 0}">
            <div class="card-head">
                <div class="title-group">
                    <span class="sym">{item['ticker']}</span>
                    <span class="tag" style="background:{tag_color}22;color:{tag_color};border:1px solid {tag_color}55;">{sc_name}</span>
                    <span class="tag" style="background:{phase_color}22;color:{phase_color};border:1px solid {phase_color}55;">{item.get('sector','')}: {sec_phase} ({sec_score:.1f}分 {phase_icon})</span>
                    <span class="tag" style="background:{c_color}22;color:{c_color};border:1px solid {c_color}55;">聚落: {c_name} ({c_reg}, {c_score:.1f}分)</span>
                    <span class="date">{item['sig_date']}</span>
                    {ret_badge}
                </div>
                <div class="price-group">
                    <span class="p-start">觸發價 <b>${item['p_start']:.2f}</b></span>
                    <span class="p-stop">停損 <b>${item['stop_price']:.2f} ({item['stop_loss_pct']}%)</b></span>
                    <span class="p-tgt">目標 <b>${item['target_price']:.2f} (+{item['target_space']}%)</b></span>
                </div>
            </div>
            <div class="metrics-row">
                <span>回踩深度: <b>{item['prior_pullback']}%</b> (Q3黃金帶)</span>
                <span>相對成交量: <b style="color:#10b981;">{item['rvol']}x</b> (放量確認)</span>
                <span>開盤缺口: <b style="color:{gap_color};">{gap_val:+0.2f}%</b></span>
                <span>日波動 ATR: <b>{item['atr_pct']}%</b> (≤5%)</span>
                <span>盈虧比 R:R: <b>{item['risk_reward']}</b></span>
                <span>20D MFE/MAE: {mfe_mae_str}</span>
            </div>
            <div class="chart-box">
                {svg_chart}
            </div>
            <div class="card-foot">
                <span class="sector">{item.get('sector','')} ｜ {item.get('industry','')} ｜ {item.get('name','')}</span>
                <span class="sop-tip">SOP: T+5 滯脹砍半 ｜ T+10 全數離場</span>
            </div>
        </div>
        """
        cards_html.append(card)

    html_content = f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Optimized Alpha Screener · 量化優化旗艦選股報告</title>
<style>
:root {{
    --bg: #090d16;
    --card-bg: #111827;
    --border: #1f2937;
    --text: #f3f4f6;
    --sub: #9ca3af;
    --green: #10b981;
    --red: #ef4444;
    --gold: #f59e0b;
    --blue: #3b82f6;
}}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    margin: 0;
    padding: 24px;
}}
.container {{
    max-width: 1400px;
    margin: auto;
}}
h1 {{
    font-size: 26px;
    margin: 0 0 6px 0;
    display: flex;
    align-items: center;
    gap: 12px;
}}
.subtitle {{
    color: var(--sub);
    font-size: 14px;
    margin-bottom: 20px;
    line-height: 1.6;
}}
.hud {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
    margin-bottom: 24px;
}}
.hud-box {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px;
}}
.hud-box .label {{
    color: var(--sub);
    font-size: 13px;
    margin-bottom: 6px;
}}
.hud-box .value {{
    font-size: 22px;
    font-weight: 700;
}}
.controls {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    margin-bottom: 20px;
    align-items: center;
}}
.btn {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 14px;
    border-radius: 6px;
    cursor: pointer;
    font-size: 13px;
}}
.btn.active {{
    background: var(--blue);
    border-color: var(--blue);
}}
.search-input {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    color: var(--text);
    padding: 8px 12px;
    border-radius: 6px;
    font-size: 13px;
    min-width: 220px;
}}
.grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(620px, 1fr));
    gap: 18px;
}}
.card {{
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 10px;
}}
.card-head {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
}}
.title-group {{
    display: flex;
    align-items: center;
    gap: 8px;
}}
.sym {{
    font-size: 18px;
    font-weight: 800;
    letter-spacing: 0.5px;
}}
.tag {{
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 600;
}}
.date {{
    font-size: 12px;
    color: var(--sub);
}}
.badge {{
    font-size: 12px;
    padding: 3px 8px;
    border-radius: 4px;
    font-weight: 700;
}}
.badge.pos {{ background: rgba(16,185,129,0.15); color: var(--green); }}
.badge.neg {{ background: rgba(239,68,68,0.15); color: var(--red); }}
.badge.neu {{ background: rgba(156,163,175,0.15); color: var(--sub); }}
.price-group {{
    font-size: 12px;
    display: flex;
    gap: 10px;
}}
.metrics-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    font-size: 12px;
    color: var(--sub);
    background: #0d121f;
    padding: 8px 12px;
    border-radius: 6px;
}}
.metrics-row b {{ color: var(--text); }}
.chart-box {{
    margin: 4px 0;
}}
.card-foot {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 12px;
    color: var(--sub);
    border-top: 1px solid #1a2333;
    padding-top: 8px;
}}
.sop-tip {{
    color: var(--gold);
    font-size: 11px;
}}
</style>
</head>
<body>
<div class="container">
    <h1><span>◆</span> Optimized Alpha Screener (優化版量化選股旗艦報告)</h1>
    <div class="subtitle">
        嚴格納入紅隊量化審計與因子區分度結論：<b>回踩深度鎖定 Q3 黃金甜蜜點 [-8.5%, -4.0%]</b> ＋ <b>成交量確認 (RVOL ≥ 1.0)</b> ＋ <b>波動風控 (ATR% ≤ 5.0%)</b> ＋ <b>動態板塊輪動模型 (排除衰退與擁擠板塊)</b>。
    </div>

    <div class="hud">
        <div class="hud-box">
            <div class="label">優化入選標的總數</div>
            <div class="value">{tot} <span style="font-size:13px;color:var(--sub);font-weight:normal;">(淘汰 88% 雜訊)</span></div>
        </div>
        <div class="hud-box">
            <div class="label">20D 成熟勝率 (>0%)</div>
            <div class="value" style="color:var(--green);">{win_rate:.1f}% <span style="font-size:13px;color:var(--sub);font-weight:normal;">(確認輪動板塊達 68.1%)</span></div>
        </div>
        <div class="hud-box">
            <div class="label">20D 平均 MFE / MAE</div>
            <div class="value">+{avg_mfe:.1f}% / {avg_mae:.1f}%</div>
        </div>
        <div class="hud-box">
            <div class="label">全體極值比 (MFE/|MAE|)</div>
            <div class="value" style="color:var(--gold);">{ratio:.2f} <span style="font-size:13px;color:var(--sub);font-weight:normal;">(盈虧非對稱優勢)</span></div>
        </div>
        <div class="hud-box">
            <div class="label">波動與動態板塊風控</div>
            <div class="value" style="color:var(--blue);font-size:16px;">ATR ≤ 5.0% ｜ 排除衰退板塊</div>
        </div>
    </div>

    <div class="controls">
        <button class="btn active" onclick="filterScreener('all', this)">全部策略 ({tot})</button>
        <button class="btn" onclick="filterPhase('rotation', this)">⭐ 確認輪動／主升板塊</button>
        <button class="btn" onclick="filterScreener('screen', this)">screen 築底突破旗艦</button>
        <button class="btn" onclick="filterScreener('claude_fibb', this)">claude_fibb 動能波段</button>
        <button class="btn" onclick="filterScreener('bull_bounce', this)">bull_bounce</button>
        <button class="btn" onclick="filterScreener('mtf4', this)">mtf4</button>
        <input type="text" id="searchInput" class="search-input" placeholder="搜尋代號 (如 MRNA, TEAM) 或產業..." oninput="searchCards()">
    </div>

    <div class="grid" id="cardGrid">
        {"".join(cards_html)}
    </div>
</div>

<script>
function filterScreener(sc, btn) {{
    document.querySelectorAll('.controls .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const cards = document.querySelectorAll('.card');
    cards.forEach(c => {{
        if (sc === 'all' || c.getAttribute('data-screener') === sc) {{
            c.style.display = 'flex';
        }} else {{
            c.style.display = 'none';
        }}
    }});
}}

function filterPhase(type, btn) {{
    document.querySelectorAll('.controls .btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const cards = document.querySelectorAll('.card');
    cards.forEach(c => {{
        const p = c.getAttribute('data-phase') || '';
        if (p === '確認輪動' || p === '主升／擴散') {{
            c.style.display = 'flex';
        }} else {{
            c.style.display = 'none';
        }}
    }});
}}

function searchCards() {{
    const query = document.getElementById('searchInput').value.toUpperCase().trim();
    const cards = document.querySelectorAll('.card');
    cards.forEach(c => {{
        const text = c.innerText.toUpperCase();
        if (!query || text.includes(query)) {{
            c.style.display = 'flex';
        }} else {{
            c.style.display = 'none';
        }}
    }});
}}
</script>
</body>
</html>
"""
    with open(output_file, "w", encoding="utf-8") as fp:
        fp.write(html_content)
    print(f"成功生成 HTML K 棒視覺化報告: {output_file} ({tot} 檔標的)")


def main():
    parser = argparse.ArgumentParser(description="Optimized Alpha Screener")
    parser.add_argument(
        "--mode",
        choices=["all", "screen", "claude_fibb", "bull_bounce", "mtf4"],
        default="all",
        help="Screener 模型模式",
    )
    parser.add_argument(
        "--min-rvol",
        type=float,
        default=1.0,
        help="最低相對成交量 (RVOL) 門檻 (預設: 1.0x)",
    )
    parser.add_argument(
        "--pullback-min",
        type=float,
        default=-8.5,
        help="回踩深度下限 (預設: -8.5%%)",
    )
    parser.add_argument(
        "--pullback-max",
        type=float,
        default=-4.0,
        help="回踩深度上限 (預設: -4.0%%)",
    )
    parser.add_argument(
        "--max-atr",
        type=float,
        default=5.0,
        help="最高 14D ATR%% 波動度上限 (預設: 5.0%%，剔除高波動妖股)",
    )
    parser.add_argument(
        "--min-sector-score",
        type=float,
        default=45.0,
        help="最低板塊輪動評分門檻 (預設: 45.0 分)",
    )
    parser.add_argument(
        "--allow-decaying-sectors",
        action="store_true",
        default=False,
        help="是否允許處於「衰退」與「擁擠」狀態的逆風板塊 (預設: 嚴格排除)",
    )
    parser.add_argument(
        "--output-html",
        default="optimized_screener_report.html",
        help="HTML K 棒報告輸出路徑",
    )
    parser.add_argument(
        "--output-csv",
        default="optimized_candidates.csv",
        help="候選標的 CSV 輸出路徑",
    )
    args = parser.parse_args()

    print("==================================================")
    print("   Optimized Alpha Screener (優化版量化選股器)   ")
    print("==================================================")
    print(f"篩選條件：回踩深度 [{args.pullback_min}%, {args.pullback_max}%] 且 RVOL >= {args.min_rvol}x 且 ATR% <= {args.max_atr}%")
    print(f"板塊風控：最低輪動分 >= {args.min_sector_score} 且 {'允許衰退板塊' if args.allow_decaying_sectors else '嚴格排除衰退/擁擠板塊'}")

    db = load_cached_ohlcv()
    sec_map, sec_cur, sec_hist, cluster_cur, cluster_hist = load_sector_rotation_data()
    signals = load_all_signals(db)
    print(f"加載歷史原始候選訊號總數: {len(signals)}")
    print(f"載入全市場標的板塊表: {len(sec_map)} 檔，板塊輪動記錄: {len(sec_hist)} 筆，細分聚落輪動: {len(cluster_hist)} 筆")

    # 因子計算與過濾
    qualifying = []
    for s in signals:
        if args.mode != "all" and s["screener"] != args.mode:
            continue
        item = calculate_quant_factors(s, db, sec_map, sec_cur, sec_hist, cluster_cur, cluster_hist)
        if not item:
            continue
        pb = item["prior_pullback"]
        rvol = item["rvol"]
        atr = item["atr_pct"]
        s_phase = item.get("sector_phase", "中性觀察")
        s_score = item.get("sector_score", 50.0)

        # 條件 1: 回踩深度在黃金窗口 [-8.5%, -4.0%]
        if not (args.pullback_min <= pb <= args.pullback_max):
            continue

        # 條件 2: 成交量放量確認 RVOL >= 1.0x
        if rvol < args.min_rvol:
            continue

        # 條件 3: 波動度風控 ATR% <= max_atr (預設 5.0%)
        if atr > args.max_atr:
            continue

        # 條件 4: 動態板塊輪動過濾
        is_decaying = ("Lagging" in s_phase or "衰退" in s_phase or ("Weakening" in s_phase or "擁擠／延伸" in s_phase))
        if not args.allow_decaying_sectors and is_decaying:
            continue
        if s_score < args.min_sector_score:
            continue

        # 條件 5: 細分行業聚落風控 (若所屬聚落處於破位且評分低於 42 分，嚴格過濾)
        c_reg = item.get("cluster_regime", "")
        c_score = item.get("cluster_score", 50.0)
        is_cluster_breakdown = ("Breakdown" in c_reg or "破位" in c_reg) and c_score < 42.0
        if not args.allow_decaying_sectors and is_cluster_breakdown:
            continue

        qualifying.append(item)

    print(f"符合優化條件之精選標的總數: {len(qualifying)}")

    # 波段去重與多策略共振合併 (Episode Deduplication & Multi-Screener Confluence)
    # 消除時間序列重複計數偏誤 (Time-Series Clustering Bias)，確保 Daily Routine 觀察清單檔檔獨立不重複
    by_ticker = defaultdict(list)
    for q in qualifying:
        by_ticker[q["ticker"]].append(q)

    deduped_qualifying = []
    for ticker, q_list in by_ticker.items():
        # 依最新日期與輪動評分排序
        q_list.sort(
            key=lambda x: (x["sig_date"], float(x.get("cluster_score", 0.0))),
            reverse=True,
        )
        best_q = q_list[0].copy()

        # 若最新交易日有多個策略同時命中，合併策略標籤為 Confluence（例如 bull_bounce + mtf4）
        latest_date = best_q["sig_date"]
        same_date_scs = list(
            dict.fromkeys(x["screener"] for x in q_list if x["sig_date"] == latest_date)
        )
        if len(same_date_scs) > 1:
            best_q["screener"] = " + ".join(same_date_scs)

        deduped_qualifying.append(best_q)

    print(
        f"波段去重後獨立標的總數: {len(deduped_qualifying)} 檔 (剔除 {len(qualifying) - len(deduped_qualifying)} 筆重複觸發)"
    )
    qualifying = deduped_qualifying

    # 排序：優先以最新日期、聚落突破狀態與 RVOL
    qualifying.sort(
        key=lambda x: (
            x["sig_date"],
            1 if "Breakout" in x.get("cluster_regime", "") else 0,
            x["ret_20d"] if x["ret_20d"] is not None else -999,
            x["rvol"],
        ),
        reverse=True,
    )

    # 輸出 CSV
    with open(args.output_csv, "w", newline="", encoding="utf-8-sig") as fp:
        writer = csv.writer(fp)
        writer.writerow([
            "代號", "公司名稱", "產業板塊", "細分行業", "細分聚落", "聚落輪動狀態", "聚落輪動評分",
            "板塊輪動狀態", "板塊輪動評分", "Screener策略", "訊號日期", "起始價", "停損價", "停損幅(%)",
            "目標價", "目標空間(%)", "風險報酬比", "前期回踩(%)",
            "開盤缺口(%)", "相對成交量(RVOL)", "日波動ATR(%)",
            "20D報酬(%)", "20D MFE(%)", "20D MAE(%)", "達頂天數"
        ])
        for q in qualifying:
            writer.writerow([
                q["ticker"], q.get("name", ""), q.get("sector", ""), q.get("industry", ""),
                q.get("cluster", ""), q.get("cluster_regime", ""), q.get("cluster_score", ""),
                q.get("sector_phase", ""), q.get("sector_score", ""),
                q["screener"], q["sig_date"], q["p_start"],
                q["stop_price"], q["stop_loss_pct"], q["target_price"], q["target_space"], q["risk_reward"],
                q["prior_pullback"], q.get("gap_pct", 0.0), q["rvol"], q["atr_pct"],
                q.get("ret_20d", ""), q.get("mfe_20d", ""), q.get("mae_20d", ""), q.get("t_mfe", "")
            ])
    # 輸出相容格式 CSV 至 macro-dashboard 供 report.py 繪圖
    macro_out_csv = "/Users/eric/macro-dashboard/screener/out/optimized_alpha.csv"
    try:
        os.makedirs(os.path.dirname(macro_out_csv), exist_ok=True)
        with open(macro_out_csv, "w", newline="", encoding="utf-8") as fp:
            writer = csv.writer(fp)
            writer.writerow([
                "symbol", "name", "sector", "industry", "cluster", "cluster_regime", "cluster_score",
                "sector_phase", "sector_score", "screener", "asof", "px", "stop_price", "stop_loss_pct",
                "target_price", "target_space", "risk_reward", "prior_pullback", "gap_pct", "rvol", "atr_pct",
                "score", "ret_20d", "mfe_20d", "mae_20d"
            ])
            for q in qualifying:
                writer.writerow([
                    q["ticker"], q.get("name", ""), q.get("sector", ""), q.get("industry", ""),
                    q.get("cluster", ""), q.get("cluster_regime", ""), q.get("cluster_score", ""),
                    q.get("sector_phase", ""), q.get("sector_score", ""),
                    q["screener"], q["sig_date"], q["p_start"],
                    q["stop_price"], q["stop_loss_pct"], q["target_price"], q["target_space"], q["risk_reward"],
                    q["prior_pullback"], q.get("gap_pct", 0.0), q["rvol"], q["atr_pct"],
                    q.get("cluster_score", 50.0),
                    q.get("ret_20d", ""), q.get("mfe_20d", ""), q.get("mae_20d", "")
                ])
    except Exception as e:
        print(f"寫入 macro-dashboard CSV 警告: {e}")

    # 輸出標準 Modernist 接觸印樣 HTML 報告 (呼叫 report.py)
    py_exec = "/Users/eric/macro-dashboard/.venv/bin/python"
    report_script = "/Users/eric/macro-dashboard/screener/report.py"
    standard_generated = False
    if os.path.exists(py_exec) and os.path.exists(report_script) and os.path.exists(macro_out_csv):
        import subprocess
        try:
            cmd = [py_exec, report_script, "--csv", macro_out_csv, "--out", args.output_html]
            subprocess.run(cmd, check=True)
            print(f"成功呼叫 report.py 生成標準 Modernist HTML 報表: {args.output_html}")
            standard_generated = True
        except Exception as e:
            print(f"呼叫 report.py 失敗，改用回退生成器: {e}")

    if not standard_generated:
        build_html_report(qualifying, args.output_html)

    # 同步複製到 latest/ 與本日目錄
    # 2026-09-27 修正：本日目錄原本寫死成 "2026-09-26"，每天都寫進同一個舊資料夾。
    #   改用執行當天的日期，跟 macro-dashboard 的 history.py（reports/<今天>）與
    #   publish_discord.py（Pages/<今天>）同一個規則。本日目錄還不存在時照舊略過，
    #   交給主流程第 ⑨ 步發布時建立（它會一併帶上 optimized_screener_report.html）。
    latest_dir = os.path.join(BASE_DIR, "latest")
    today_dir = os.path.join(BASE_DIR, datetime.date.today().strftime("%Y-%m-%d"))
    for d_path in [latest_dir, today_dir]:
        if os.path.isdir(d_path):
            try:
                target_html = os.path.join(d_path, "optimized_screener_report.html")
                target_csv = os.path.join(d_path, "optimized_candidates.csv")
                target_opt_html = os.path.join(d_path, "optimized_alpha.html")
                if os.path.abspath(args.output_html) != os.path.abspath(target_html):
                    shutil.copy2(args.output_html, target_html)
                if os.path.abspath(args.output_csv) != os.path.abspath(target_csv):
                    shutil.copy2(args.output_csv, target_csv)
                shutil.copy2(args.output_html, target_opt_html)
                print(f"已同步更新 {d_path} 目錄快照。")
            except Exception as e:
                print(f"同步更新 {d_path} 警告: {e}")


if __name__ == "__main__":
    main()
