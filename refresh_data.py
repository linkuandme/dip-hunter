"""
Dip Hunter — Live Data Refresher
=================================
Run this script from your local machine (Python 3.8+) to update
dip_hunter.html with real-time data from Yahoo Finance.

Requirements:
    pip install yfinance pandas numpy

Usage:
    python refresh_data.py                 # refresh HTML with latest data
    python refresh_data.py --top 50        # top N droppers (default 50)
    python refresh_data.py --period 2y     # history period (default 2y)
    python refresh_data.py --buys 25       # top N strong-buy candidates (default 25)
"""
import argparse, json, os, sys, warnings, logging, io, contextlib
from datetime import datetime
warnings.filterwarnings('ignore')

# ─── Silence yfinance noise (delisted, no-data warnings, etc.) ────────────────
logging.getLogger('yfinance').setLevel(logging.CRITICAL)
logging.getLogger().setLevel(logging.CRITICAL)

try:
    import yfinance as yf
    import pandas as pd
    import numpy as np
except ImportError:
    sys.exit("Missing packages. Run:  pip install yfinance pandas numpy")

OUT = os.path.dirname(os.path.abspath(__file__))

# Tickers known to be delisted, renamed, or otherwise dead — auto-skip.
# (SQ → XYZ since 2024-12; FB → META; etc.)
DELISTED = {
    "SQ",       # Block Inc. — renamed to XYZ in Dec 2024
    "FB",       # Facebook — renamed to META in 2022
    "TWTR",     # Twitter — taken private in 2022
    "ATVI",     # Activision — acquired by MSFT in 2023
    "FRC",      # First Republic — failed 2023
    "SIVB",     # SVB Financial — failed 2023
    "SBNY",     # Signature Bank — failed 2023
    "CTXS",     # Citrix — taken private 2022
    "DISCA",    # Discovery → WBD
    "VIAC",     # ViacomCBS → PARA
}

# Replacement tickers for ones that were renamed.
RENAMED = {
    "SQ": "XYZ",   # Block Inc. new ticker
    "FB": "META",
}

# ─── 1. Universe ──────────────────────────────────────────────────────────────
def get_sp500_tickers():
    try:
        sp500 = pd.read_html("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")[0]
        return sp500["Symbol"].str.replace(".", "-", regex=False).tolist()
    except Exception:
        print("  Wikipedia fetch failed — using hardcoded fallback list")
        return [
            "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","AVGO","JPM","LLY",
            "XOM","UNH","V","MA","COST","HD","PG","ABBV","MRK","CVX","BAC","NFLX",
            "KO","ADBE","CRM","WMT","AMD","TMO","MCD","CSCO","ABT","ACN","PEP",
            "DHR","LIN","DIS","VZ","INTC","NKE","TXN","NEE","UPS","RTX","HON",
            "PM","AMGN","LOW","QCOM","SBUX","GS","MS","BLK","SPGI","IBM","CAT",
            "BA","GE","DE","MMM","AXP","C","USB","PLD","CCI","AMT","EQIX","WFC",
            "T","NOW","PANW","SNOW","COIN","PLTR","MARA","RIOT","DKNG","RBLX",
            "PYPL","SHOP","XYZ","ROKU","DOCU","ZM","LYFT","ABNB","SOFI","HOOD",
            "F","GM",
            "PFE","BMY","GILD",
            "COP","SLB","EOG","PSX","MPC","VLO",
        ]

def get_etfs():
    return [
        "SPY","QQQ","IWM","VTI","GLD","SLV","TLT","HYG",
        "XLF","XLE","XLK","XLV","XLI","XLY","XLP",
        "ARKK","ARKG","SOXX","SMH","IBB",
    ]

def clean_universe(tickers):
    """Drop known-dead tickers and apply rename map. De-dup, preserve order."""
    out, seen = [], set()
    for t in tickers:
        t = t.strip().upper()
        if not t:
            continue
        t = RENAMED.get(t, t)        # apply renames first
        if t in DELISTED:
            continue                 # skip dead ones
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out

# ─── 2. Technical Indicators ──────────────────────────────────────────────────
def calc_rsi(closes, period=14):
    delta = pd.Series(closes).diff()
    gain = delta.clip(lower=0).ewm(com=period-1, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(com=period-1, adjust=False).mean()
    rs = gain / (loss + 1e-10)
    return (100 - 100/(1+rs)).fillna(50).values

def calc_macd(closes):
    s = pd.Series(closes)
    e12 = s.ewm(span=12, adjust=False).mean()
    e26 = s.ewm(span=26, adjust=False).mean()
    macd = e12 - e26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd.values, signal.values

def calc_bollinger(closes, period=20):
    s = pd.Series(closes)
    sma = s.rolling(period).mean().fillna(s)
    std = s.rolling(period).std().fillna(0)
    return sma.values, (sma+2*std).values, (sma-2*std).values

def calc_sma(closes, period):
    s = pd.Series(closes)
    return s.rolling(period).mean().fillna(s).values

def score_ticker(last, rsi, macd, macd_sig, bb_pos, vol_ratio, drop_pct, lows_1y, closes):
    """Rebound score for the *biggest droppers* table."""
    score = 0
    score += max(0, (35 - rsi) * 1.5)
    score += max(0, (0.2 - bb_pos) * 60)
    score += min(20, vol_ratio * 5)
    score += min(15, abs(drop_pct) * 1.5)
    if macd < 0 and macd > macd_sig - 0.5:
        score += 8
    low_52 = min(lows_1y)
    pct_from_low = (last - low_52) / (low_52 + 1e-9) * 100
    if pct_from_low < 10:
        score += 12
    return min(100, max(0, score))

def score_strong_buy(last, rsi, rsi_5d_ago, macd, macd_sig, macd_hist, macd_hist_5d_ago,
                     sma20, sma50, sma200, bb_pos, vol_ratio, low_52, high_52, closes):
    """
    Strong-buy score — different criteria from rebound score.
    Looks for stocks already turning UP off support, not still falling.
    """
    score = 0

    # 1. RSI in healthy oversold-to-neutral zone, ideally rising
    if 30 <= rsi <= 55:
        score += 15
    if rsi > rsi_5d_ago + 3:           # RSI rising = momentum recovering
        score += 12

    # 2. MACD bullish: histogram positive or rising fast from negative
    if macd > macd_sig:                # bullish crossover
        score += 18
    elif macd_hist > macd_hist_5d_ago: # histogram improving
        score += 10

    # 3. Above SMA50 = uptrend intact (or near it = pullback support)
    if last > sma50:
        score += 12
    elif last > sma50 * 0.97:          # within 3% of SMA50 = textbook pullback
        score += 8

    # 4. SMA50 > SMA200 (golden-cross territory = long-term uptrend)
    if sma50 > sma200:
        score += 10

    # 5. Reasonable BB position (lower-half = buying dip, not chasing top)
    if 0.1 <= bb_pos <= 0.55:
        score += 8

    # 6. Healthy volume (above 80% of average)
    if vol_ratio >= 0.8:
        score += 5
    if vol_ratio >= 1.5:               # volume confirmation on the move
        score += 5

    # 7. Distance from 52-week extremes — prefer middle/lower zone
    pct_from_low  = (last - low_52)  / (low_52  + 1e-9)
    pct_from_high = (high_52 - last) / (high_52 + 1e-9)
    if 0.05 <= pct_from_low <= 0.40:    # 5–40% off the low = base-building
        score += 8
    if pct_from_high >= 0.10:           # at least 10% off high = room to run
        score += 5

    # 8. Penny-stock penalty
    if last < 5:
        score -= 30

    return min(100, max(0, score))

def buy_grade(score):
    if score >= 75: return "STRONG BUY", "#00ff88"
    if score >= 60: return "BUY",        "#3fb950"
    if score >= 45: return "ACCUMULATE", "#ffd700"
    return "HOLD", "#8b949e"

# ─── 3. Bulk download helper (silences delisted noise) ────────────────────────
def safe_bulk_download(tickers, **kw):
    """yf.download but with stderr/stdout swallowed so a few dead tickers
    don't spam the log."""
    buf_err, buf_out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(buf_err), contextlib.redirect_stdout(buf_out):
        df = yf.download(tickers, progress=False, **kw)
    return df

# ─── 4. Main ──────────────────────────────────────────────────────────────────
def main(top_n=50, buys_n=25, period="2y"):
    print(f"\n{'='*60}")
    print(f"  Dip Hunter — Live Refresh  ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    print(f"{'='*60}")

    raw_universe = list(dict.fromkeys(get_sp500_tickers() + get_etfs()))
    tickers = clean_universe(raw_universe)
    print(f"Universe: {len(tickers)} tickers (cleaned from {len(raw_universe)})")

    # ── 1. Download 5-day summary for all tickers ─────────────────────────────
    print("\n1/4  Fetching today's moves…")
    raw5 = safe_bulk_download(tickers, period="5d", interval="1d",
                              group_by="ticker", auto_adjust=True, threads=True)
    records_5d = []
    bad = []
    for t in tickers:
        try:
            df = raw5[t].dropna() if len(tickers) > 1 else raw5.dropna()
            if len(df) < 2:
                bad.append(t); continue
            prev = float(df["Close"].iloc[-2])
            last = float(df["Close"].iloc[-1])
            vol  = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0
            pct  = (last - prev) / prev * 100
            records_5d.append({"ticker": t, "prev_close": prev,
                                "last_close": last, "pct_change": pct, "volume": vol})
        except Exception:
            bad.append(t)

    if bad:
        print(f"   Skipped {len(bad)} tickers with no data (delisted/illiquid)")

    if not records_5d:
        sys.exit("No data downloaded — check internet connection / firewall.")

    daily = pd.DataFrame(records_5d).sort_values("pct_change")
    top50_tickers = daily.head(top_n)["ticker"].tolist()
    print(f"   Biggest drop: {daily.iloc[0]['ticker']} {daily.iloc[0]['pct_change']:.1f}%")
    print(f"   Top {top_n} droppers identified")

    # ── 2. Download history for entire valid universe (for strong-buy scan) ───
    valid_tickers = [r["ticker"] for r in records_5d]
    print(f"\n2/4  Downloading {period} history for {len(valid_tickers)} valid tickers…")
    hist = safe_bulk_download(valid_tickers, period=period, interval="1d",
                              group_by="ticker", auto_adjust=True, threads=True)

    # ── 3. Analyze every valid ticker → both top-droppers & strong-buys ──────
    print(f"\n3/4  Computing indicators & scores…")
    drop_records, buy_records, chart_data = [], [], []
    chart_data = {}

    drop_set = set(top50_tickers)

    for t in valid_tickers:
        try:
            df = hist[t].dropna() if len(valid_tickers) > 1 else hist.dropna()
            df = df.reset_index()
            if len(df) < 60:
                continue

            closes  = df["Close"].values.astype(float)
            opens   = df["Open"].values.astype(float)
            highs   = df["High"].values.astype(float)
            lows    = df["Low"].values.astype(float)
            vols    = df["Volume"].values.astype(float)
            dates   = df["Date"].astype(str).tolist()

            rsi        = calc_rsi(closes)
            macd, msig = calc_macd(closes)
            sma20, bb_up, bb_lo = calc_bollinger(closes)
            sma50      = calc_sma(closes, 50)
            sma200     = calc_sma(closes, 200)

            last = closes[-1]
            prev = closes[-2]
            drop_pct = (last - prev) / prev * 100
            rsi_now  = float(rsi[-1])
            rsi_5d   = float(rsi[-6]) if len(rsi) >= 6 else rsi_now
            macd_now = float(macd[-1])
            msig_now = float(msig[-1])
            macd_h   = macd_now - msig_now
            macd_h_5 = float(macd[-6] - msig[-6]) if len(macd) >= 6 else macd_h
            vol_now  = float(vols[-1])
            vol_avg  = float(np.mean(vols[-21:-1]) + 1e-9)
            vol_ratio = vol_now / vol_avg
            bb_pos = (last - bb_lo[-1]) / (bb_up[-1] - bb_lo[-1] + 1e-9)
            low_52  = float(lows[-252:].min()) if len(lows) >= 252 else float(lows.min())
            high_52 = float(highs[-252:].max()) if len(highs) >= 252 else float(highs.max())

            # ─── A. If this is a top dropper, build a "drop" record ──────────
            if t in drop_set:
                score = score_ticker(last, rsi_now, macd_now, msig_now,
                                     bb_pos, vol_ratio, drop_pct, lows[-252:], closes)
                if score >= 70:   action, ac = "BUY NOW",  "#00ff88"
                elif score >= 50: action, ac = "WATCH",    "#ffd700"
                else:             action, ac = "WAIT",     "#ff6b6b"

                drop_records.append({
                    "ticker": t, "company": t, "sector": "—",
                    "last_close": round(float(last), 2),
                    "prev_close": round(float(prev), 2),
                    "drop_pct":   round(float(drop_pct), 2),
                    "volume":     int(vol_now),
                    "vol_ratio":  round(float(vol_ratio), 2),
                    "rsi":        round(rsi_now, 1),
                    "macd":       round(macd_now, 4),
                    "bb_pos":     round(bb_pos, 3),
                    "score":      round(score, 1),
                    "action":     action, "action_color": ac,
                    "low_52wk":   round(low_52, 2),
                    "high_52wk":  round(high_52, 2),
                    "target_5d":  round(last * (1 + min(0.12, abs(drop_pct)*0.3/100)), 2),
                    "target_30d": round(last * (1 + min(0.25, abs(drop_pct)*0.7/100)), 2),
                })

            # ─── B. Strong-buy scoring — applied to the WHOLE universe ───────
            sb = score_strong_buy(last, rsi_now, rsi_5d, macd_now, msig_now,
                                  macd_h, macd_h_5,
                                  sma20[-1], sma50[-1], sma200[-1],
                                  bb_pos, vol_ratio, low_52, high_52, closes)
            grade, gc = buy_grade(sb)
            buy_records.append({
                "ticker": t, "company": t, "sector": "—",
                "last_close": round(float(last), 2),
                "drop_pct":   round(float(drop_pct), 2),
                "rsi":        round(rsi_now, 1),
                "rsi_trend":  round(rsi_now - rsi_5d, 1),
                "macd":       round(macd_now, 4),
                "macd_hist":  round(macd_h, 4),
                "vol_ratio":  round(float(vol_ratio), 2),
                "bb_pos":     round(bb_pos, 3),
                "buy_score":  round(sb, 1),
                "grade":      grade, "grade_color": gc,
                "low_52wk":   round(low_52, 2),
                "high_52wk":  round(high_52, 2),
                "pct_from_low":  round((last-low_52)/(low_52+1e-9)*100, 1),
                "pct_from_high": round((high_52-last)/(high_52+1e-9)*100, 1),
                "target_5d":  round(last * 1.04, 2),
                "target_30d": round(last * 1.10, 2),
                "above_sma50":  bool(last > sma50[-1]),
                "above_sma200": bool(last > sma200[-1]),
                "golden_cross": bool(sma50[-1] > sma200[-1]),
            })

            # ─── C. Chart data — keep for any ticker that lands in either list
            N = 90
            chart_data[t] = {
                "dates":       dates[-N:],
                "open":        [round(x,2) for x in opens[-N:]],
                "high":        [round(x,2) for x in highs[-N:]],
                "low":         [round(x,2) for x in lows[-N:]],
                "close":       [round(x,2) for x in closes[-N:]],
                "volume":      [int(x) for x in vols[-N:]],
                "sma20":       [round(x,2) for x in sma20[-N:]],
                "sma50":       [round(x,2) for x in sma50[-N:]],
                "bb_upper":    [round(x,2) for x in bb_up[-N:]],
                "bb_lower":    [round(x,2) for x in bb_lo[-N:]],
                "rsi":         [round(x,1) for x in rsi[-N:]],
                "macd":        [round(x,4) for x in macd[-N:]],
                "macd_signal": [round(x,4) for x in msig[-N:]],
            }
        except Exception as e:
            # silent — most failures are intermittent yfinance errors
            pass

    # Filter strong-buy list down to the top N candidates
    buy_records.sort(key=lambda x: -x["buy_score"])
    strong_buys = [r for r in buy_records if r["buy_score"] >= 60][:buys_n]

    # If fewer than `buys_n` qualify at >=60, fall back to simply the top N by score
    if len(strong_buys) < buys_n:
        strong_buys = buy_records[:buys_n]

    # Trim chart_data to only what's referenced
    keep_set = set([r["ticker"] for r in drop_records] + [r["ticker"] for r in strong_buys])
    chart_data = {k: v for k, v in chart_data.items() if k in keep_set}

    drop_records.sort(key=lambda x: x["drop_pct"])
    print(f"   Drops processed : {len(drop_records)} tickers")
    print(f"   Strong buys     : {len(strong_buys)} (score≥60)")

    # ── 4. Re-build dashboard HTML ────────────────────────────────────────────
    print("\n4/4  Writing dashboard HTML…")
    top50_js     = json.dumps(drop_records)
    chart_js     = json.dumps(chart_data)
    strongbuy_js = json.dumps(strong_buys)

    html_path = os.path.join(OUT, "dip_hunter.html")
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    import re
    top50_repl  = lambda m, d=top50_js:     f'const TOP50 = {d};'
    chart_repl  = lambda m, d=chart_js:     f'const CHART = {d};'
    sbuy_repl   = lambda m, d=strongbuy_js: f'const STRONG_BUYS = {d};'

    html = re.sub(r'const TOP50\s*=\s*\[.*?\];',         top50_repl,  html, flags=re.DOTALL)
    html = re.sub(r'const CHART\s*=\s*\{.*?\};',         chart_repl,  html, flags=re.DOTALL)
    if re.search(r'const STRONG_BUYS\s*=', html):
        html = re.sub(r'const STRONG_BUYS\s*=\s*\[.*?\];', sbuy_repl, html, flags=re.DOTALL)
    else:
        # Inject the STRONG_BUYS constant right after CHART
        html = re.sub(r'(const CHART = \{.*?\};)',
                      lambda m: m.group(1) + '\n' + f'const STRONG_BUYS = {strongbuy_js};',
                      html, count=1, flags=re.DOTALL)

    ts = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = re.sub(r'Updated:.*?(?=<)', lambda m: f'Updated: {ts}', html)

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Save JSONs for reuse
    with open(os.path.join(OUT, "top50.json"), "w") as f:
        json.dump(drop_records, f, indent=2)
    with open(os.path.join(OUT, "strong_buys.json"), "w") as f:
        json.dump(strong_buys, f, indent=2)

    print(f"\n[OK]  Dashboard updated -> {html_path}")
    print(f"   BUY NOW (drops)   : {sum(1 for r in drop_records if r['action']=='BUY NOW')}")
    print(f"   WATCH   (drops)   : {sum(1 for r in drop_records if r['action']=='WATCH')}")
    print(f"   WAIT    (drops)   : {sum(1 for r in drop_records if r['action']=='WAIT')}")
    print(f"   STRONG BUY        : {sum(1 for r in strong_buys if r['grade']=='STRONG BUY')}")
    print(f"   BUY               : {sum(1 for r in strong_buys if r['grade']=='BUY')}")
    print(f"   ACCUMULATE        : {sum(1 for r in strong_buys if r['grade']=='ACCUMULATE')}")
    print(f"\n   Open in browser: file:///{html_path.replace(os.sep, '/')}")

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--top",    type=int, default=50, help="N biggest droppers (default 50)")
    p.add_argument("--buys",   type=int, default=25, help="N strong-buy candidates (default 25)")
    p.add_argument("--period", default="2y",         help="History period (default 2y)")
    args = p.parse_args()
    main(args.top, args.buys, args.period)
