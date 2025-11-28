from datetime import datetime, timedelta, timezone
import os
import csv
import requests
import logging
import ccxt
import time
import json
from pathlib import Path
import os, json
    
# Cấu hình logging
# logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s:%(message)s")
# 
# ===================== UPGRADE CONFIG & HELPERS =====================
UPGRADE = {
    "risk_per_trade": float(os.getenv("RISK_PER_TRADE", 0.008)),
    "min_rr": float(os.getenv("MIN_RR", 1.8)),
    "min_adx": float(os.getenv("MIN_ADX", 22)),
    "min_atr_pct": float(os.getenv("MIN_ATR_PCT", 0.006)),
    "min_bbwidth_pctile": float(os.getenv("MIN_BBWIDTH_PCTILE", 0.25)),
    "vol_pctile": float(os.getenv("VOL_PCTILE", 0.70)),
    "btc_drop_block": float(os.getenv("BTC_DROP_BLOCK", 0.008)),
    "min_quote_volume_24h": float(os.getenv("MIN_QV_24H", 1_000_000)),
    "max_spread": float(os.getenv("MAX_SPREAD", 0.002)),
    "use_stop_for_spot": os.getenv("USE_STOP_FOR_SPOT", "true").lower() == "true",
}
def _now_iso(): 
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
def _percentile(vals, q):
    vals = [float(x) for x in vals if x is not None]
    if not vals: return None
    vals.sort()
    k = max(0, min(len(vals)-1, int(round(q*(len(vals)-1)))))
    return vals[k]
def _ema(series, n):
    if len(series) < n: return None
    k = 2/(n+1)
    s = sum(series[:n])/n
    for x in series[n:]:
        s = x*k + s*(1-k)
    return s
def _adx14(ohlcv):
    if len(ohlcv) < 20: return None
    def tr(h,l,pc): 
        return max(h-l, abs(h-pc), abs(l-pc))
    plus_dm, minus_dm, trs = [], [], []
    for i in range(1,len(ohlcv)):
        h1,l1 = ohlcv[i][2], ohlcv[i][3]
        h0,l0 = ohlcv[i-1][2], ohlcv[i-1][3]
        up = h1 - h0; dn = l0 - l1
        plus_dm.append(up if (up>dn and up>0) else 0.0)
        minus_dm.append(dn if (dn>up and dn>0) else 0.0)
        trs.append(tr(h1,l1,ohlcv[i-1][4]))
    n=14
    def smma(vals):
        s=sum(vals[:n]); prev=s/n; out=[prev]
        for x in vals[n:]:
            prev = (prev*(n-1)+x)/n
            out.append(prev)
        return out[-1]
    tr_n = smma(trs); plus_n=smma(plus_dm); minus_n=smma(minus_dm)
    if tr_n==0: return None
    dip = 100*(plus_n/tr_n); dim = 100*(minus_n/tr_n)
    if (dip+dim)==0: return None
    dx = 100*abs(dip-dim)/(dip+dim)
    return dx  # dùng DX gần nhất như proxy ADX để nhẹ
def _atr_pct(ohlcv, n=14):
    if len(ohlcv) < n+2: return 0.0
    def tr(h,l,pc): 
        return max(h-l, abs(h-pc), abs(l-pc))
    trs=[]; pc=ohlcv[-(n+1)][4]
    for i in range(len(ohlcv)-n, len(ohlcv)):
        h,l,c = ohlcv[i][2], ohlcv[i][3], ohlcv[i][4]
        trs.append(tr(h,l,pc)); pc=c
    atr = sum(trs)/len(trs); close=ohlcv[-1][4] or 0.0
    return atr/close if close else 0.0
def _bb_width(closes, n=20, k=2.0):
    if len(closes)<n: return 0.0
    w = closes[-n:]
    ma = sum(w)/n
    std = (sum((x-ma)**2 for x in w)/n)**0.5
    upper = ma + k*std; lower = ma - k*std
    return (upper-lower)/ma if ma else 0.0
def _pass_liquidity_and_spread(tkr):
    qv = 0.0
    if isinstance(tkr.get("info"), dict):
        try: qv = float(tkr["info"].get("volCcy24h") or 0.0)
        except: qv = 0.0
    if qv==0.0:
        qv = float(tkr.get("quoteVolume") or tkr.get("quoteVolume24h") or 0.0)
    if qv < UPGRADE["min_quote_volume_24h"]:
        return False
    bid=tkr.get("bid"); ask=tkr.get("ask")
    if bid and ask and bid>0:
        spr=(ask-bid)/bid
        if spr>UPGRADE["max_spread"]: return False
    return True
def pre_buy_screen_and_sizing(symbol, fallback_usdt):
    sym_slash = symbol.replace("-", "/")
    try:
        tkr = exchange.fetch_ticker(sym_slash)
    except Exception as e:
        logger.info(f"⛔ Bỏ {symbol}: không lấy được ticker ({e})")
        return (False, None, None, None, None, "no_ticker")
    if not _pass_liquidity_and_spread(tkr):
        return (False, None, None, None, None, "liquidity")
    entry = float(tkr.get("last") or 0.0)
    if entry<=0: 
        return (False, None, None, None, None, "bad_price")
    o15 = exchange.fetch_ohlcv(sym_slash, timeframe="15m", limit=120)
    if len(o15)<40:
        return (False, None, None, None, None, "no_ohlcv")
    adx_val = _adx14(o15)
    atrp = _atr_pct(o15)
    closes15 = [x[4] for x in o15]
    bbw = _bb_width(closes15)
    # percentile volume
    vols = [x[5] for x in o15][-50:]
    vthr = _percentile(vols, UPGRADE["vol_pctile"]) or 0.0
    vol_ok = len(vols)>=10 and vols[-1] >= max(vthr, sum(vols)/len(vols))
    # BTC filter
    btc15 = exchange.fetch_ohlcv("BTC/USDT", timeframe="15m", limit=80)
    btc_ok = True
    if len(btc15)>=3:
        nowp = btc15[-1][4]; past = btc15[-3][4]
        btc_ok = ((nowp - past)/past) > -UPGRADE["btc_drop_block"]
    choppy_ok = (adx_val is not None and adx_val>=UPGRADE["min_adx"] and atrp>=UPGRADE["min_atr_pct"])
    # BB width percentile approx via recent widths (proxy p25)
    # We use last 90 widths from closes15 if available
    widths=[]
    for i in range(20, len(closes15)):
        widths.append(_bb_width(closes15[:i]))
    p25 = _percentile(widths, UPGRADE["min_bbwidth_pctile"]) or 0.0
    bbw_ok = (bbw >= p25)
    if not (choppy_ok and bbw_ok and vol_ok and btc_ok):
        return (False, None, None, None, None, "filters")
    # Stop = min swing low last 10 bars OR ATR*1.8 below entry
    lowN = min(x[3] for x in o15[-10:])
    # approximate ATR value from atr% and entry
    stop_atr = entry - 1.8*(atrp*entry)
    stop = max(lowN, stop_atr)
    if stop>=entry:
        return (False, None, None, None, None, "rr_invalid")
    tp = entry + UPGRADE["min_rr"]*(entry - stop)
    # sizing by risk
    bal = exchange.fetch_balance()
    free_usdt = float(bal.get("USDT", {}).get("free", 0.0))
    risk_usdt = free_usdt * UPGRADE["risk_per_trade"]
    loss_per_unit = entry - stop
    amt = risk_usdt / loss_per_unit if loss_per_unit>0 else 0.0
    if amt*entry < 5:  # min notional
        amt = fallback_usdt / entry
    return (True, float(amt), float(entry), float(stop), float(tp), "ok")
logger = logging.getLogger("AUTO_SELL")
logger = logging.getLogger("AUTO_SELL")
logger.setLevel(logging.DEBUG)  # Luôn bật DEBUG/INFO

handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)

if not logger.handlers:
    logger.addHandler(handler)
# Đọc biến môi trường
SPREADSHEET_URL = os.environ.get("SPREADSHEET_URL")
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Khởi tạo OKX
exchange = ccxt.okx({
    'apiKey': OKX_API_KEY,
    'secret': OKX_API_SECRET,
    'password': OKX_API_PASSPHRASE,
    'enableRateLimit': True,
    'options': {
        'defaultType': 'spot'
    }
})

spot_entry_prices = {}  # ✅ khai báo biến toàn cục

SPOT_JSON_PATH = Path(__file__).with_name("spot_entry_prices.json")

def load_entry_prices() -> dict:
    try:
        if not SPOT_JSON_PATH.exists():
            logger.warning(f"⚠️ File {SPOT_JSON_PATH} KHÔNG tồn tại! => Trả về dict rỗng.")
            return {}
        with SPOT_JSON_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            logger.warning(f"⚠️ Dữ liệu trong {SPOT_JSON_PATH} KHÔNG phải dict: {type(data)}")
            return {}
        logger.debug(f"📥 Đã load JSON từ file: {json.dumps(data, indent=2, ensure_ascii=False)}")
        return data
    except json.JSONDecodeError as e:
        logger.error(f"❌ JSON lỗi/đang ghi dở, KHÔNG ghi đè file: {e}")
        return {}
    except Exception as e:
        logger.error(f"❌ Lỗi khi load {SPOT_JSON_PATH}: {e}")
        return {}

def save_entry_prices(prices_dict: dict):
    tmp = SPOT_JSON_PATH.with_suffix(".json.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(prices_dict, f, indent=2, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, SPOT_JSON_PATH)  # atomic write
        logger.debug(f"💾 Đã ghi {SPOT_JSON_PATH} xong.\n📦 Nội dung:\n{json.dumps(prices_dict, indent=2, ensure_ascii=False)}")
    except Exception as e:
        logger.error(f"❌ Lỗi khi lưu {SPOT_JSON_PATH}: {e}")
        try: tmp.unlink(missing_ok=True)
        except: pass

def auto_sell_once():
    global spot_entry_prices
    was_updated = False  # ✅ Reset biến mỗi lần duyệt coin
    logging.info("🟢 [AUTO SELL WATCHER] Đã khởi động luồng kiểm tra auto sell")

    # Load lại dữ liệu

    new_data = load_entry_prices()
    if isinstance(new_data, dict):
        spot_entry_prices.update(new_data)
        logger.info(f"📂 File `spot_entry_prices.json` hiện tại:\n{json.dumps(spot_entry_prices, indent=2)}")
        # Sau khi load thành công:
        for symbol, data in spot_entry_prices.items():
            logger.debug(f"[ENTRY JSON] {symbol}: {data} (type={type(data)})")
    else:
        logging.warning("⚠️ Dữ liệu load từ JSON không phải dict!")

    try:
        logging.info("🔄 [AUTO SELL] Kiểm tra ví SPOT để chốt lời...")
        balances = exchange.fetch_balance()
        tickers = exchange.fetch_tickers()
        updated_prices = spot_entry_prices.copy()
        # ✅ Lọc coin trong tài khoản
        spot_coins = {
            coin: float(data.get("total", 0))
            for coin, data in balances.items()
            if (
                isinstance(data, dict)
                and float(data.get("total", 0)) > 0
                and coin.endswith("/USDT")       # Chỉ lấy coin/USDT
                and coin in tickers              # Có giá hiện tại
                and float(tickers[coin]['last']) * float(data.get("total", 0)) > 1  # Giá trị > 1 USDT
                
            )
        }
        
        # ✅ Hiển thị chi tiết từng coin
        for coin, amount in spot_coins.items():
            try:
                price = float(tickers[coin]['last'])
                value = price * amount
                logger.debug(f"[SPOT HOLDINGS] {coin}: số lượng = {amount:.4f}, giá = {price:.6f} → giá trị = {value:.2f} USDT")
            except Exception as e:
                logger.warning(f"[⚠️] Không thể lấy giá cho {coin}: {e}")
        
        # ✅ Duyệt từng coin trong balance
        for coin, balance_data in balances.items():
            was_updated = False  # ✅ Reset biến mỗi lần duyệt coin
            try:
                if not isinstance(balance_data, dict):
                    logger.warning(f"⚠️ {coin} không phải dict: {balance_data}")
                    continue
                balance = float(balance_data.get("total", 0))
                if balance <= 0:
                    continue
                
                # ✅ Bỏ qua coin có số lượng nhỏ hơn 1
                if balance < 1:
                    continue
                # ✅ Log số lượng coin đang nắm giữ (>=1)
                logger.debug(f"✅ Coin {coin} đang nắm giữ với số lượng: {balance}")
        
                symbol_dash = f"{coin}-USDT"
                symbol_slash = f"{coin}/USDT"
                # ✅ Ưu tiên symbol có trong tickers
                ticker = tickers.get(symbol_dash) or tickers.get(symbol_slash)
                if not ticker or 'last' not in ticker:
                    logger.warning(f"⚠️ Không có giá hiện tại cho {symbol_dash} hoặc {symbol_slash} (ticker=None hoặc thiếu key 'last')")
                    continue
        
                # ✅ Lấy giá hiện tại chính xác
                try:
                    current_price = float(ticker['last'])
                    logger.debug(f"📉 Giá hiện tại của {coin} ({symbol_dash}): {current_price} (ticker={ticker})")
                except Exception as e:
                    logger.warning(f"⚠️ Giá hiện tại của {coin} KHÔNG hợp lệ: {ticker['last']} ({e})")
                    continue
        
                # ✅ Gán đúng symbol (tránh dùng nhầm)
                symbol = symbol_dash
        
                # ✅ Lấy entry_data từ spot_entry_prices
                entry_data = spot_entry_prices.get(symbol.upper())
                if not isinstance(entry_data, dict):
                    logger.warning(f"⚠️ {symbol} entry_data KHÔNG phải dict: {entry_data}")
                    continue
                
                # ✅ Kiểm tra timestamp của entry_data (phải là string)
                timestamp = entry_data.get("timestamp")
                if timestamp and not isinstance(timestamp, str):
                    logger.warning(f"⚠️ {symbol} timestamp KHÔNG phải string (entry_data): {timestamp}")
                    continue
                else:
                    logger.debug(f"📅 Entry timestamp cho {symbol}: {timestamp}")
                entry_price = entry_data.get("price")
                if not isinstance(entry_price, (int, float)):
                    logger.warning(f"⚠️ {symbol} entry_price KHÔNG phải số: {entry_price}")
                    continue
        
                # ✅ Tính phần trăm lời
                
                # ✅ Ưu tiên TP/SL nếu có trong JSON
                stop_in = entry_data.get("stop")
                tp_in = entry_data.get("tp")
                if isinstance(tp_in, (int,float)) and current_price >= tp_in:
                    logger.info(f"🎯 TP hit {symbol}: entry={entry_price} tp={tp_in} last={current_price}")
                    try:
                        exchange.create_market_sell_order(symbol, balance)
                        logger.info(f"✅ Đã bán TP {symbol} số lượng {balance}")
                        updated_prices.pop(symbol, None)
                        was_updated = True
                    except Exception as e:
                        logger.error(f"❌ Lỗi bán TP {symbol}: {e}")
                    continue
                if isinstance(stop_in, (int,float)) and current_price <= stop_in:
                    logger.info(f"🛑 SL hit {symbol}: entry={entry_price} sl={stop_in} last={current_price}")
                    try:
                        exchange.create_market_sell_order(symbol, balance)
                        logger.info(f"✅ Đã bán SL {symbol} số lượng {balance}")
                        updated_prices.pop(symbol, None)
                        was_updated = True
                    except Exception as e:
                        logger.error(f"❌ Lỗi bán SL {symbol}: {e}")
                    continue
                percent_gain = ((current_price - entry_price) / entry_price) * 100
        
                if percent_gain >= 20:
                    logger.info(f"✅ CHỐT LỜI: {symbol} tăng {percent_gain:.2f}% từ {entry_price} => {current_price}")
                    try:
                        exchange.create_market_sell_order(symbol, balance)
                        logger.info(f"💰 Đã bán {symbol} số lượng {balance} để chốt lời")
                        updated_prices.pop(symbol, None)
                        was_updated = True
                    except Exception as e:
                        logger.error(f"❌ Lỗi khi bán {symbol}: {e}")
                        continue  
                # ✅ Chỉ ghi file nếu có thay đổi thực sự
                if was_updated:
                    spot_entry_prices = updated_prices
                    save_entry_prices(spot_entry_prices)
                    logger.debug(f"📂 Đã cập nhật spot_entry_prices: {json.dumps(spot_entry_prices, indent=2)}")
            except Exception as e:
                logger.error(f"❌ Lỗi khi xử lý coin {coin}: {e}")
                continue
    except Exception as e:
        logger.error(f"❌ Lỗi chính trong auto_sell_once(): {e}")

        
def fetch_sheet():
    try:
        csv_url = SPREADSHEET_URL.replace("/edit#gid=", "/export?format=csv&gid=")
        res = requests.get(csv_url)
        res.raise_for_status()
        return list(csv.reader(res.content.decode("utf-8").splitlines()))
    except Exception as e:
        logging.error(f"❌ Không thể tải Google Sheet: {e}")
        return []

def get_short_term_trend(symbol):
    score = 0
    timeframes = ["1h", "4h", "1d"]

    for tf in timeframes:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=50)
            closes = [c[4] for c in ohlcv]
            if len(closes) < 50:
                continue

            ema20 = sum(closes[-20:]) / 20
            ema50 = sum(closes[-50:]) / 50
            rsi = compute_rsi(closes, period=14)

            if rsi > 60 and ema20 > ema50:
                score += 2
            elif rsi > 50 and ema20 > ema50:
                score += 1
        except Exception as e:
            logger.warning(f"⚠️ Không thể fetch nến {tf} cho {symbol}: {e}")
            continue

    if score >= 3:
        return "TĂNG"
    elif score <= 1:
        return "GIẢM"
    else:
        return "KHÔNG RÕ"

def compute_rsi(closes, period=14):
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [delta if delta > 0 else 0 for delta in deltas]
    losses = [-delta if delta < 0 else 0 for delta in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def run_bot():
    global spot_entry_prices
    rows = fetch_sheet()

    for i, row in enumerate(rows):
        try:
            logger.debug(f"🔍 Đang xử lý dòng {i}: {row}")
            if not row or len(row) < 2:
                logger.warning(f"⚠️ Dòng {i} không hợp lệ: {row}")
                continue

            symbol = row[0].strip().upper()        # ví dụ: DOGE-USDT
            signal = row[1].strip().upper()        # ví dụ: MUA MẠNH
            gia_mua = float(row[2]) if len(row) > 2 and row[2] else None
            ngay = row[3].strip() if len(row) > 3 else ""
            da_mua = row[5].strip().upper() if len(row) > 5 else ""

            logger.info(f"🛒 Đang xét mua {symbol}...")

            if not gia_mua or da_mua == "ĐÃ MUA":
                logger.info(f"⏩ Bỏ qua {symbol} do {'đã mua' if da_mua == 'ĐÃ MUA' else 'thiếu giá'}")
                continue

            if signal != "MUA MẠNH":
                logger.info(f"❌ {symbol} bị loại do tín hiệu Sheet = {signal}")
                continue

            # ✅ Kiểm tra nếu đã quá hạn tần suất (theo giờ Việt Nam UTC+7)
            if len(row) > 4 and row[4].strip():
                try:
                    freq_minutes = int(row[4].strip())
                    time_str = row[3].strip()
                    signal_time = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(hours=7)))
                    now_vn = datetime.now(timezone(timedelta(hours=7)))
                    elapsed = (now_vn - signal_time).total_seconds() / 120
                    if elapsed > freq_minutes:
                        logger.info(f"⏱ Bỏ qua {symbol} vì đã quá hạn {freq_minutes} phút (đã qua {int(elapsed)} phút)")
                        continue
                except Exception as e:
                    logger.warning(f"⚠️ Không thể kiểm tra tần suất cho {symbol}: {e}")
            
            # ✅ Kiểm tra nếu đã có coin trong ví Spot
            coin_name = symbol.split("-")[0]
            balances = exchange.fetch_balance()
            asset_balance = balances.get(coin_name, {}).get('total', 0)

            if asset_balance and asset_balance > 1:
                logger.info(f"❌ Bỏ qua {symbol} vì đã có {asset_balance} {coin_name} trong ví")
                continue

            # ✅ Phân tích xu hướng ngắn hạn thay cho TradingView
            trend = get_short_term_trend(symbol)
            logger.info(f"📉 Xu hướng ngắn hạn của {symbol} = {trend}")
            
            # ✅ Nếu trend là TĂNG → mua ngay (logic cũ)
            if trend == "TĂNG":
                try:
                    usdt_amount = 20
                    price = float(exchange.fetch_ticker(symbol)['last']) # ép về float
                    amount = round(usdt_amount / price, 6)
                    # === CHỐNG FOMO (dành cho trend TĂNG) ===
                    ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=30)
                    closes = [c[4] for c in ohlcv]
                    volumes = [c[5] for c in ohlcv]
                    rsi = compute_rsi(closes, period=14)
                    vol = volumes[-1]
                    vol_sma20 = sum(volumes[-20:]) / 20
                    price_now = closes[-1]
                    price_3bars_ago = closes[-4]
                    price_change = (price_now - price_3bars_ago) / price_3bars_ago * 100
                    
                    if rsi > 70 or vol > vol_sma20 * 2 or price_change > 10:
                        logger.info(f"⛔ {symbol} bị loại do FOMO trong trend TĂNG (RSI={rsi:.1f}, Δgiá 3h={price_change:.1f}%)")
                        continue
                    logger.info(f"💰 [TĂNG] Mua {amount} {symbol} với {usdt_amount} USDT (giá {price})")
                    # === Nâng cấp pre-check + sizing + TP/SL ===
                    _sym_slash = symbol.replace("-", "/")
                    _passed, _amt2, _entry2, _stop2, _tp2, _reason = pre_buy_screen_and_sizing(symbol, usdt_amount)
                    if not _passed:
                        logger.info(f"⛔ Bỏ {_sym_slash} lý do: {_reason}")
                        continue
                    amount = _amt2
                    order = exchange.create_market_buy_order(_sym_slash, amount)
                    logger.info(f"✅ BUY {_sym_slash}: amount={amount} ~ {amount*_entry2:.2f} USDT @~{_entry2}")
                    try:
                        _data = load_entry_prices()
                        _data[symbol.upper().replace("/", "-")] = {"price": _entry2, "stop": _stop2 if UPGRADE["use_stop_for_spot"] else None, "tp": _tp2, "timestamp": _now_iso()}
                        save_entry_prices(_data)
                    except Exception as e:
                        logger.error(f"❌ Lỗi cập nhật JSON sau BUY: {e}")
                    logger.info(f"✅ Đã mua {symbol} theo TĂNG: {order}")
                    
                    # Giả sử sau khi vào lệnh mua thành công:
                    # ✅ Load lại dữ liệu cũ để tránh mất dữ liệu các coin khác
                    # Chuẩn hóa symbol để lưu
                    symbol_dash = symbol.upper().replace("/", "-")
                    
                    # Load file hiện tại để merge, tránh mất các coin khác
                    current_data = load_entry_prices()
                    
                    # Cập nhật hoặc thêm mới coin vừa mua
                    current_data[symbol_dash] = {
                        "price": float(price),
                        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z"
                    }
                    
                    # Ghi lại file an toàn
                    save_entry_prices(current_data)
                    
                    logger.debug(f"💾 JSON sau khi cập nhật {symbol_dash}:\n{json.dumps(current_data, indent=2, ensure_ascii=False)}")
                    time.sleep(1) # đảm bảo file được ghi hoàn toàn

                    # ✅ Gửi thông báo về Telegram sau khi mua và cập nhật JSON
                    try:
                        content = json.dumps({symbol: spot_entry_prices[symbol]}, indent=2)
                        send_to_telegram(f"✅ Đã mua {symbol} và cập nhật JSON:\n```\n{content}\n```")
                    except Exception as e:
                        logger.warning(f"⚠️ Không thể gửi Telegram: {e}")
                    continue  # Đã mua rồi thì bỏ qua phần dưới
                except Exception as e:
                    logger.error(f"❌ Lỗi khi mua {symbol} theo trend TĂNG: {e}")
                    continue
            
            # ✅ Nếu trend là SIDEWAY → kiểm tra thêm RSI và Volume
            if trend == "SIDEWAY":
                try:
                    ohlcv = exchange.fetch_ohlcv(symbol, timeframe="1h", limit=30)
                    closes = [c[4] for c in ohlcv]
                    volumes = [c[5] for c in ohlcv]
                    # Giả sử đã có ohlcv, closes, volumes
                    rsi = compute_rsi(closes, period=14)
                    vol = volumes[-1]
                    vol_sma20 = sum(volumes[-20:]) / 20
                    price_now = closes[-1]
                    price_3bars_ago = closes[-4]
                    price_change = (price_now - price_3bars_ago) / price_3bars_ago * 100
                    # Nếu có dấu hiệu FOMO thì bỏ qua
                    if rsi > 70 or vol > vol_sma20 * 2 or price_change > 10:
                        logger.info(f"⛔ {symbol} bị loại do dấu hiệu FOMO (RSI={rsi:.2f}, Δgiá 3h={price_change:.1f}%, vol={vol:.0f})")
                        continue
                    if len(closes) < 20:
                        logger.warning(f"⚠️ Không đủ dữ liệu nến cho {symbol}")
                        continue
            
                    rsi = compute_rsi(closes, period=14)
                    vol = volumes[-1]
                    vol_sma20 = sum(volumes[-20:]) / 20
            
                    logger.debug(f"📊 {symbol}: RSI = {rsi}, Volume = {vol}, SMA20 = {vol_sma20}")
            
                    if rsi >= 55 or vol >= vol_sma20:
                        logger.info(f"⛔ {symbol} bị loại (SIDEWAY nhưng không nén đủ mạnh)")
                        continue
                    # ✅ Mua nếu đủ điều kiện SIDEWAY tích luỹ
                    usdt_amount = 20
                    price = exchange.fetch_ticker(symbol)['last']
                    amount = round(usdt_amount / price, 6)
                    logger.info(f"💰 [SIDEWAY] Mua {amount} {symbol} với {usdt_amount} USDT (giá {price})")
                    # === Nâng cấp pre-check + sizing + TP/SL ===
                    _sym_slash = symbol.replace("-", "/")
                    _passed, _amt2, _entry2, _stop2, _tp2, _reason = pre_buy_screen_and_sizing(symbol, usdt_amount)
                    if not _passed:
                        logger.info(f"⛔ Bỏ {_sym_slash} lý do: {_reason}")
                        continue
                    amount = _amt2
                    order = exchange.create_market_buy_order(_sym_slash, amount)
                    logger.info(f"✅ BUY {_sym_slash}: amount={amount} ~ {amount*_entry2:.2f} USDT @~{_entry2}")
                    try:
                        _data = load_entry_prices()
                        _data[symbol.upper().replace("/", "-")] = {"price": _entry2, "stop": _stop2 if UPGRADE["use_stop_for_spot"] else None, "tp": _tp2, "timestamp": _now_iso()}
                        save_entry_prices(_data)
                    except Exception as e:
                        logger.error(f"❌ Lỗi cập nhật JSON sau BUY: {e}")
                    logger.info(f"✅ Đã mua {symbol} theo SIDEWAY: {order}")
                    # Giả sử sau khi vào lệnh mua thành công:
                    # ✅ Load lại dữ liệu cũ để tránh mất dữ liệu các coin khác
                    # Chuẩn hóa symbol để lưu
                    symbol_dash = symbol.upper().replace("/", "-")
                    
                    # Load file hiện tại để merge, tránh mất các coin khác
                    current_data = load_entry_prices()
                    
                    # Cập nhật hoặc thêm mới coin vừa mua
                    current_data[symbol_dash] = {
                        "price": float(price),
                        "timestamp": datetime.utcnow().isoformat(timespec="seconds") + "Z"
                    }
                    
                    # Ghi lại file an toàn
                    save_entry_prices(current_data)
                    
                    logger.debug(f"💾 JSON sau khi cập nhật {symbol_dash}:\n{json.dumps(current_data, indent=2, ensure_ascii=False)}")
                    time.sleep(1) # đảm bảo file được ghi hoàn toàn
                    # ✅ Gửi thông báo về Telegram sau khi mua và cập nhật JSON
                    try:
                        content = json.dumps({symbol: spot_entry_prices[symbol]}, indent=2)
                        send_to_telegram(f"✅ Đã mua {symbol} và cập nhật JSON:\n```\n{content}\n```")
                    except Exception as e:
                        logger.warning(f"⚠️ Không thể gửi Telegram: {e}")
                except Exception as e:
                    logger.error(f"❌ Lỗi khi mua {symbol} theo SIDEWAY: {e}")            
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý dòng {i} - {row}: {e}")

def send_to_telegram(message):
    token = TELEGRAM_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {"chat_id": chat_id, "text": message}
    try:
        requests.post(url, data=data)
    except:
        pass

# Sau khi save json:
msg = json.dumps(spot_entry_prices, indent=2)
send_to_telegram(f"📂 Đã cập nhật giá mới:\n{msg}")       

def main():
    #now = datetime.utcnow()
    #minute = now.minute
    #hour = now.hour
    #print(f"🕰️ Bắt đầu lúc {now.isoformat()}")
    # ✅ Chỉ chạy run_bot nếu phút hiện tại chia hết 15 (ví dụ: 00:00, 00:15, 00:30...)
    #if minute % 15 == 0:
        #run_bot()
        #logger.info("🟢 Bắt đầu chạy auto_sell_once() sau run_bot()")
        #auto_sell_once()
    #else:
        #print(f"⌛ Chưa đến thời điểm chạy run_bot(), phút hiện tại = {minute}")
        #logger.info("🟢 Bắt đầu chạy auto_sell_once() khi KHÔNG có run_bot()")
        #auto_sell_once()   
    print(f"🟢 Bắt đầu bot lúc {datetime.utcnow().isoformat()}")
    # Cron đã quyết định lịch, chỉ cần chạy luôn
    run_bot()
    auto_sell_once()
if __name__ == "__main__":
    main()
