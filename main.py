# main.py
# BOT AUTO SPOT (OKX) — bản tích hợp 9 nâng cấp
# - Risk per trade + position sizing
# - Choppy filter (ADX, ATR%, BB width percentile)
# - Volume percentile confirmation
# - Liquidity & spread filter
# - BTC regime filter
# - Stable JSON (atomic write + file lock, key chuẩn COIN-USDT)
# - BUY + TP/SL (RR tối thiểu) + Auto-sell TP/SL
# - Logs rõ ràng
# ---------------------------------------------------------------

import os, json, time, math, statistics, logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

import ccxt

# ===================== LOGGING =====================
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("spot-bot")

# ===================== CONFIG NÂNG CẤP =====================
UPGRADE = {
    "risk_per_trade": float(os.getenv("RISK_PER_TRADE", 0.008)),   # 0.8% vốn/lệnh
    "min_rr": float(os.getenv("MIN_RR", 1.8)),                     # RR tối thiểu
    "min_adx": float(os.getenv("MIN_ADX", 22)),                    # choppy filter
    "min_atr_pct": float(os.getenv("MIN_ATR_PCT", 0.006)),         # ATR% >= 0.6%
    "min_bbwidth_pctile": float(os.getenv("MIN_BBWIDTH_PCTILE", 0.25)),  # >= p25 90d
    "vol_pctile": float(os.getenv("VOL_PCTILE", 0.70)),            # volume >= p70 50 nến
    "btc_drop_block": float(os.getenv("BTC_DROP_BLOCK", 0.008)),   # block nếu BTC -0.8%/30p
    "min_quote_volume_24h": float(os.getenv("MIN_QV_24H", 1_000_000)),  # USDT
    "max_spread": float(os.getenv("MAX_SPREAD", 0.002)),           # 0.2%
    "use_stop_for_spot": os.getenv("USE_STOP_FOR_SPOT", "true").lower() == "true",
    "lock_timeout_sec": int(os.getenv("LOCK_TIMEOUT_SEC", 10)),
    "max_symbols": int(os.getenv("MAX_SYMBOLS", 120)),             # tối đa số cặp duyệt
}

# ===================== TIỆN ÍCH =====================
def now_utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def normalize_symbol_dash(symbol: str) -> str:
    return symbol.upper().replace("/", "-")

def normalize_symbol_slash(symbol_dash: str) -> str:
    return symbol_dash.upper().replace("-", "/")

def pct(a, b):
    if b == 0: 
        return 0.0
    return (a - b) / b

def percentile(values, q):
    if not values: 
        return None
    arr = sorted(float(x) for x in values if x is not None)
    if not arr: 
        return None
    k = max(0, min(len(arr)-1, int(round(q*(len(arr)-1)))))
    return float(arr[k])

# ===================== JSON ENTRY PRICES (AN TOÀN) =====================
SPOT_JSON_PATH = Path(__file__).with_name("spot_entry_prices.json")
LOCK_PATH = SPOT_JSON_PATH.with_suffix(".lock")

def _acquire_lock(timeout=UPGRADE["lock_timeout_sec"]):
    start = time.time()
    while True:
        try:
            fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.close(fd)
            return True
        except FileExistsError:
            if time.time() - start > timeout:
                return False
            time.sleep(0.1)

def _release_lock():
    try: LOCK_PATH.unlink(missing_ok=True)
    except: pass

def load_entry_prices() -> dict:
    try:
        if not SPOT_JSON_PATH.exists():
            return {}
        with SPOT_JSON_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON lỗi/đang ghi dở: {e}")
        return {}
    except Exception as e:
        logger.error(f"❌ Lỗi load {SPOT_JSON_PATH}: {e}")
        return {}

def save_entry_prices(prices_dict: dict):
    ok = _acquire_lock()
    if not ok:
        logger.error("❌ Không thể khoá file JSON để ghi (timeout). Bỏ qua lần ghi này.")
        return
    tmp = SPOT_JSON_PATH.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(prices_dict, f, indent=2, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, SPOT_JSON_PATH)  # atomic
        logger.debug(f"💾 Ghi JSON xong ({SPOT_JSON_PATH})")
    except Exception as e:
        logger.error(f"❌ Lỗi khi lưu {SPOT_JSON_PATH}: {e}")
        try: tmp.unlink(missing_ok=True)
        except: pass
    finally:
        _release_lock()

# ===================== KHỞI TẠO SÀN =====================
def init_exchange():
    api_key = os.getenv("OKX_API_KEY")
    secret = os.getenv("OKX_API_SECRET")
    passwd = os.getenv("OKX_API_PASSPHRASE")
    if not (api_key and secret and passwd):
        raise RuntimeError("Thiếu OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE")
    ex = ccxt.okx({
        "apiKey": api_key,
        "secret": secret,
        "password": passwd,
        "options": {"defaultType": "spot"},
        "enableRateLimit": True,
    })
    ex.load_markets()
    return ex

exchange = init_exchange()

# ===================== CHỈ BÁO CƠ BẢN =====================
def ema(series, n):
    series = list(series)
    if len(series) < n: return [None]*len(series)
    k = 2/(n+1)
    out = [None]*(n-1)
    s = sum(series[:n])/n
    out.append(s)
    for x in series[n:]:
        s = x*k + s*(1-k)
        out.append(s)
    return out

def rsi(series, n=14):
    series = list(series)
    if len(series) <= n: return [None]*len(series)
    gains, losses = [], []
    for i in range(1, n+1):
        ch = series[i] - series[i-1]
        gains.append(max(ch, 0.0))
        losses.append(abs(min(ch, 0.0)))
    avg_gain = sum(gains)/n
    avg_loss = sum(losses)/n
    out = [None]*n
    def rs_to_rsi(ag, al):
        if al == 0: return 100.0
        rs = ag/al
        return 100 - 100/(1+rs)
    out.append(rs_to_rsi(avg_gain, avg_loss))
    for i in range(n+1, len(series)):
        ch = series[i] - series[i-1]
        gain = max(ch, 0.0)
        loss = abs(min(ch, 0.0))
        avg_gain = (avg_gain*(n-1)+gain)/n
        avg_loss = (avg_loss*(n-1)+loss)/n
        out.append(rs_to_rsi(avg_gain, avg_loss))
    return out

def macd(series, fast=12, slow=26, signal=9):
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = [None if (a is None or b is None) else a-b for a,b in zip(ema_fast, ema_slow)]
    # signal
    vals = [x for x in macd_line if x is not None]
    pad = len(macd_line) - len(vals)
    sig = [None]*pad + ema(vals, signal)
    hist = [None if (m is None or s is None) else m-s for m,s in zip(macd_line, sig)]
    return macd_line, sig, hist

def true_range(h, l, pc):
    return max(h - l, abs(h - pc), abs(l - pc))

def atr(hlc, n=14):
    # hlc: list of (h,l,c)
    if len(hlc) <= n: return [None]*len(hlc)
    trs = []
    prev_close = hlc[0][2]
    for i in range(1, len(hlc)):
        h,l,c = hlc[i]
        trs.append(true_range(h,l,prev_close))
        prev_close = c
    # initial ATR
    atr_vals = [None]
    a = sum(trs[:n])/n
    atr_vals += [None]*(n-1)
    atr_vals.append(a)
    for tr in trs[n:]:
        a = (a*(n-1) + tr)/n
        atr_vals.append(a)
    return atr_vals

def adx(ohlcv, n=14):
    # ohlcv: [ts,o,h,l,c,v]
    if len(ohlcv) <= n+1: return [None]*len(ohlcv)
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(ohlcv)):
        h1,l1 = ohlcv[i][2], ohlcv[i][3]
        h0,l0 = ohlcv[i-1][2], ohlcv[i-1][3]
        up = h1 - h0
        dn = l0 - l1
        plus_dm.append(up if (up>dn and up>0) else 0.0)
        minus_dm.append(dn if (dn>up and dn>0) else 0.0)
        trs.append(true_range(h1,l1,ohlcv[i-1][4]))
    def smma(vals, n):
        s = sum(vals[:n])
        out = [None]*(n-1)
        prev = s/n
        out.append(prev)
        for x in vals[n:]:
            prev = (prev*(n-1)+x)/n
            out.append(prev)
        return out
    tr_n = smma(trs, n)
    plus_n = smma(plus_dm, n)
    minus_n = smma(minus_dm, n)
    di_plus = [None if (a is None or b==0) else 100*(a/b) for a,b in zip(plus_n, tr_n)]
    di_minus = [None if (a is None or b==0) else 100*(a/b) for a,b in zip(minus_n, tr_n)]
    dx = []
    for p,m in zip(di_plus, di_minus):
        if p is None or m is None or (p+m)==0:
            dx.append(None); continue
        dx.append(100*abs(p-m)/(p+m))
    # ADX = smma(dx, n)
    valid = [d for d in dx if d is not None]
    pad = len(dx) - len(valid)
    adx_vals = [None]*pad + ema(valid, n)  # dùng EMA thay vì SMMA cho nhẹ
    return adx_vals

# ===================== BỘ LỌC NÂNG CAO =====================
def calc_atr_pct(ohlcv_15m, n=14):
    trs = []
    prev_close = None
    window = ohlcv_15m[-(n+1):]
    if len(window) < n+1: 
        return 0.0
    for _, o,h,l,c,v in window:
        if prev_close is None:
            tr = h - l
        else:
            tr = true_range(h,l,prev_close)
        trs.append(tr)
        prev_close = c
    atr_v = sum(trs)/len(trs)
    close = window[-1][4]
    return atr_v / close if close else 0.0

def bb_width_series(closes, n=20, k=2.0):
    if len(closes) < n: return []
    res = []
    for i in range(n-1, len(closes)):
        window = closes[i-n+1:i+1]
        ma = sum(window)/n
        std = (sum((x-ma)**2 for x in window)/n) ** 0.5
        upper = ma + k*std
        lower = ma - k*std
        width = (upper - lower) / ma if ma else 0
        res.append(width)
    return res

def pass_choppy_filters(ohlcv_15m, adx_15m_value, closes_90d):
    atr_ok = calc_atr_pct(ohlcv_15m) >= UPGRADE["min_atr_pct"]
    bbw = bb_width_series(closes_90d, n=20, k=2.0)
    bbw_ok = False
    if bbw:
        p25 = percentile(bbw, UPGRADE["min_bbwidth_pctile"])
        bbw_ok = (bbw[-1] >= p25)
    adx_ok = (adx_15m_value is not None and adx_15m_value >= UPGRADE["min_adx"])
    return adx_ok and atr_ok and bbw_ok

def pass_volume_confirmation(vol_series_15m, lookback=50):
    vols = vol_series_15m[-lookback:]
    if len(vols) < 10: return False
    threshold = percentile(vols, UPGRADE["vol_pctile"])
    avg = sum(vols)/len(vols)
    return vol_series_15m[-1] >= max(threshold, avg)

def pass_btc_filter(btc_ohlcv_15m):
    if len(btc_ohlcv_15m) < 3:
        return True
    p_now = btc_ohlcv_15m[-1][4]
    p_2 = btc_ohlcv_15m[-3][4]
    return pct(p_now, p_2) > -UPGRADE["btc_drop_block"]

def pass_liquidity_and_spread(ticker):
    # OKX: info.volCcy24h ~ quote volume (USDT)
    qv = None
    if isinstance(ticker.get("info"), dict):
        try:
            qv = float(ticker["info"].get("volCcy24h") or 0.0)
        except: 
            qv = None
    if qv is None:
        qv = float(ticker.get("quoteVolume") or ticker.get("quoteVolume24h") or 0.0)
    if qv < UPGRADE["min_quote_volume_24h"]:
        return False
    bid = ticker.get("bid")
    ask = ticker.get("ask")
    if bid and ask and bid > 0:
        spread = (ask - bid) / bid
        if spread > UPGRADE["max_spread"]:
            return False
    return True

# ===================== RISK & TP/SL =====================
def calc_spot_amount_by_risk(balance_usdt: float, entry: float, stop: float):
    if stop is None or entry is None or stop >= entry:
        return None
    risk_usdt = balance_usdt * UPGRADE["risk_per_trade"]
    loss_per_unit = entry - stop
    if loss_per_unit <= 0:
        return None
    amount = risk_usdt / loss_per_unit
    return max(0.0, amount)

def derive_tp_from_rr(entry: float, stop: float, rr: float):
    return entry + rr * (entry - stop)

# ===================== DỮ LIỆU & TÍN HIỆU =====================
def fetch_ohlcv_safe(symbol_slash, timeframe="15m", limit=250):
    try:
        return exchange.fetch_ohlcv(symbol_slash, timeframe=timeframe, limit=limit)
    except Exception as e:
        logger.warning(f"⚠️ fetch_ohlcv lỗi {symbol_slash} tf={timeframe}: {e}")
        return []

def detect_buy_signal(symbol_dash):
    """
    Trả về tuple (entry, stop, extras) hoặc None.
    Logic: Trend-follow 1H đồng pha 15m + MACD/RSI xác nhận + bộ lọc nâng cao.
    """
    sym = normalize_symbol_slash(symbol_dash)  # CHZ/USDT
    o15 = fetch_ohlcv_safe(sym, "15m", 250)
    o1h = fetch_ohlcv_safe(sym, "1h", 300)
    if len(o15) < 120 or len(o1h) < 120: 
        return None

    closes15 = [x[4] for x in o15]
    vols15 = [x[5] for x in o15]
    closes1h = [x[4] for x in o1h]

    # Trend 1H: EMA50 > EMA200 & close > EMA50
    ema50_1h = ema(closes1h, 50)
    ema200_1h = ema(closes1h, 200)
    if ema50_1h[-1] is None or ema200_1h[-1] is None:
        return None
    trend_up_1h = ema50_1h[-1] > ema200_1h[-1] and closes1h[-1] > ema50_1h[-1]
    if not trend_up_1h:
        return None

    # 15m: EMA10 cross > EMA20 + MACD hist > 0 + RSI > 50
    ema10_15 = ema(closes15, 10)
    ema20_15 = ema(closes15, 20)
    macd_line, macd_sig, macd_hist = macd(closes15)
    rsi15 = rsi(closes15, 14)
    cond_cross = (ema10_15[-2] is not None and ema20_15[-2] is not None and
                  ema10_15[-2] <= ema20_15[-2] and ema10_15[-1] > ema20_15[-1])
    cond_macd = (macd_hist[-1] is not None and macd_hist[-1] > 0)
    cond_rsi = (rsi15[-1] is not None and rsi15[-1] > 50)

    if not (cond_cross and cond_macd and cond_rsi):
        return None

    # ADX 15m
    adx15_series = adx(o15, n=14)
    adx15_value = adx15_series[-1]

    # choppy + volume
    closes90d = closes15  # dùng chuỗi này làm proxy nếu thiếu 90d thực
    if not pass_choppy_filters(o15, adx15_value, closes90d):
        return None
    if not pass_volume_confirmation(vols15):
        return None

    # BTC filter
    btc = "BTC/USDT"
    btc15 = fetch_ohlcv_safe(btc, "15m", 80)
    if not pass_btc_filter(btc15):
        return None

    entry = closes15[-1]
    # Stop: swing low N=10 nến hoặc ATR*1.8
    N = 10
    lowN = min(x[3] for x in o15[-N:])
    hlc = [(x[2], x[3], x[4]) for x in o15]
    atr14 = atr(hlc, 14)[-1] or 0
    stop = max(lowN, entry - 1.8*(atr14 or 0))
    if stop >= entry:
        return None

    tp = derive_tp_from_rr(entry, stop, UPGRADE["min_rr"])
    return (entry, stop, {"tp": tp})

# ===================== DUYỆT DANH SÁCH SYMBOL =====================
def get_candidate_symbols():
    # Ưu tiên user set qua env
    env_syms = os.getenv("SYMBOLS")
    if env_syms:
        syms = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        return [normalize_symbol_dash(s) for s in syms]

    # Mặc định: top SPOT/USDT có vol tốt
    markets = exchange.load_markets()
    out = []
    for sym, m in markets.items():
        if m.get("type") != "spot": 
            continue
        if m.get("quote") != "USDT":
            continue
        # chuyển thành dash
        out.append(normalize_symbol_dash(sym))
    # cắt tối đa
    return out[:UPGRADE["max_symbols"]]

# ===================== BUY FLOW =====================
def try_buy_symbol(symbol_dash):
    symbol_slash = normalize_symbol_slash(symbol_dash)
    # liquidity & spread
    try:
        tkr = exchange.fetch_ticker(symbol_slash)
    except Exception as e:
        logger.debug(f"⚠️ Không fetch ticker {symbol_slash}: {e}")
        return False
    if not pass_liquidity_and_spread(tkr):
        logger.info(f"⛔ Bỏ {symbol_dash}: thanh khoản/spread không đạt.")
        return False

    sig = detect_buy_signal(symbol_dash)
    if not sig:
        return False
    entry, stop, extra = sig
    tp = extra.get("tp")
    if tp <= entry or stop >= entry:
        logger.info(f"⛔ Bỏ {symbol_dash}: RR không đạt (entry={entry:.6g}, sl={stop:.6g}, tp={tp:.6g}).")
        return False

    # size theo risk
    bal = exchange.fetch_balance()
    free_usdt = float(bal.get("USDT", {}).get("free", 0.0))
    amount = calc_spot_amount_by_risk(free_usdt, entry, stop)
    if not amount or amount*entry < 5:
        logger.info(f"⛔ Bỏ {symbol_dash}: size theo risk quá nhỏ (free={free_usdt:.2f} USDT).")
        return False

    # BUY
    try:
        order = exchange.create_market_buy_order(symbol_slash, amount)
        logger.info(f"✅ BUY {symbol_dash}: amount={amount:.6g} ~ {amount*entry:.2f} USDT @~{entry:.6g}")
    except Exception as e:
        logger.error(f"❌ Lỗi BUY {symbol_dash}: {e}")
        return False

    # update JSON
    data = load_entry_prices()
    data[symbol_dash] = {
        "price": float(entry),
        "stop": float(stop) if UPGRADE["use_stop_for_spot"] else None,
        "tp": float(tp),
        "timestamp": now_utc_iso(),
    }
    save_entry_prices(data)
    logger.debug(f"💾 JSON sau BUY {symbol_dash}:\n{json.dumps(data, indent=2, ensure_ascii=False)}")
    return True

# ===================== AUTO SELL =====================
def auto_sell_once():
    was_updated = False
    entries = load_entry_prices()
    if not entries:
        logger.info("ℹ️ [AUTO SELL] Không có entry nào trong JSON.")
        return
    for sym_dash, info in list(entries.items()):
        try:
            sym_slash = normalize_symbol_slash(sym_dash)
            entry = float(info.get("price", 0))
            stop = info.get("stop")
            tp = info.get("tp")
            bal = exchange.fetch_balance()
            coin = sym_slash.split("/")[0]
            amount = float(bal.get(coin, {}).get("total", 0))
            if amount <= 0:
                logger.debug(f"↩️ [AUTO SELL] Ví không còn {sym_dash}. Bỏ qua lần này.")
                continue
            tkr = exchange.fetch_ticker(sym_slash)
            last = float(tkr["last"])
            # TP/SL
            if tp and last >= tp:
                exchange.create_market_sell_order(sym_slash, amount)
                logger.info(f"🎯 TP hit {sym_dash}: entry={entry:.6g}, tp={tp:.6g}, last={last:.6g}, pnl={(pct(last,entry)*100):.2f}%")
                entries.pop(sym_dash, None); was_updated = True
                continue
            if UPGRADE["use_stop_for_spot"] and stop and last <= stop:
                exchange.create_market_sell_order(sym_slash, amount)
                logger.info(f"🛑 SL hit {sym_dash}: entry={entry:.6g}, sl={stop:.6g}, last={last:.6g}, pnl={(pct(last,entry)*100):.2f}%")
                entries.pop(sym_dash, None); was_updated = True
                continue
        except Exception as e:
            logger.error(f"❌ AUTO SELL lỗi {sym_dash}: {e}")
    if was_updated:
        save_entry_prices(entries)
        logger.info("💾 Đã lưu JSON sau AUTO SELL.")

# ===================== VÒNG CHẠY CHÍNH =====================
def main_once():
    logger.info("🚀 Bắt đầu vòng quét SPOT...")
    symbols = get_candidate_symbols()
    logger.info(f"📌 Số cặp xét: {len(symbols)}")

    # Ưu tiên các cặp giá < 1 USDT (tuỳ chiến lược)
    for sym_dash in symbols:
        try_buy_symbol(sym_dash)

    auto_sell_once()
    logger.info("✅ Kết thúc vòng quét.")

if __name__ == "__main__":
    # Chạy theo kiểu cron/loop nhẹ; bạn có thể để cron gọi mỗi 3–5 phút
    LOOP = os.getenv("LOOP", "false").lower() == "true"
    interval_sec = int(os.getenv("INTERVAL_SEC", 180))
    if LOOP:
        while True:
            try:
                main_once()
            except Exception as e:
                logger.exception(f"💥 Lỗi vòng chính: {e}")
            time.sleep(interval_sec)
    else:
        main_once()
