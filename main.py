from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import csv
import requests
import logging
import ccxt
import time
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
# ===================== UPGRADE CONFIG & HELPERS =====================
UPGRADE = {
    "risk_per_trade": float(os.getenv("RISK_PER_TRADE", 0.008)),
    "min_rr": float(os.getenv("MIN_RR", 1.8)),
    "min_adx": float(os.getenv("MIN_ADX", 22)),
    "min_atr_pct": float(os.getenv("MIN_ATR_PCT", 0.006)),
    "min_bbwidth_pctile": float(os.getenv("MIN_BBWIDTH_PCTILE", 0.25)),
    "vol_pctile": float(os.getenv("VOL_PCTILE", 0.70)),
    "btc_drop_block": float(os.getenv("BTC_DROP_BLOCK", 0.008)),
    "min_quote_volume_24h": float(os.getenv("MIN_QV_24H", 500_000)),
    "max_spread": float(os.getenv("MAX_SPREAD", 0.002)),
    "use_stop_for_spot": os.getenv("USE_STOP_FOR_SPOT", "true").lower() == "true",
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _percentile(vals, q):
    vals = [float(x) for x in vals if x is not None]
    if not vals:
        return None
    vals.sort()
    k = max(0, min(len(vals) - 1, int(round(q * (len(vals) - 1)))))
    return vals[k]


def _ema(series, n):
    if len(series) < n:
        return None
    k = 2 / (n + 1)
    s = sum(series[:n]) / n
    for x in series[n:]:
        s = x * k + s * (1 - k)
    return s


def _adx14(ohlcv):
    if len(ohlcv) < 20:
        return None

    def tr(h, l, pc):
        return max(h - l, abs(h - pc), abs(l - pc))

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, len(ohlcv)):
        h1, l1 = ohlcv[i][2], ohlcv[i][3]
        h0, l0 = ohlcv[i - 1][2], ohlcv[i - 1][3]
        up = h1 - h0
        dn = l0 - l1
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(tr(h1, l1, ohlcv[i - 1][4]))

    n = 14

    def smma(vals):
        s = sum(vals[:n])
        prev = s / n
        out = [prev]
        for x in vals[n:]:
            prev = (prev * (n - 1) + x) / n
            out.append(prev)
        return out[-1]

    tr_n = smma(trs)
    plus_n = smma(plus_dm)
    minus_n = smma(minus_dm)
    if tr_n == 0:
        return None
    dip = 100 * (plus_n / tr_n)
    dim = 100 * (minus_n / tr_n)
    if (dip + dim) == 0:
        return None
    dx = 100 * abs(dip - dim) / (dip + dim)
    return dx


def _atr_pct(ohlcv, n=14):
    if len(ohlcv) < n + 2:
        return 0.0

    def tr(h, l, pc):
        return max(h - l, abs(h - pc), abs(l - pc))

    trs = []
    pc = ohlcv[-(n + 1)][4]
    for i in range(len(ohlcv) - n, len(ohlcv)):
        h, l, c = ohlcv[i][2], ohlcv[i][3], ohlcv[i][4]
        trs.append(tr(h, l, pc))
        pc = c
    atr = sum(trs) / len(trs)
    close = ohlcv[-1][4] or 0.0
    return atr / close if close else 0.0


def _bb_width(closes, n=20, k=2.0):
    if len(closes) < n:
        return 0.0
    w = closes[-n:]
    ma = sum(w) / n
    std = (sum((x - ma) ** 2 for x in w) / n) ** 0.5
    upper = ma + k * std
    lower = ma - k * std
    return (upper - lower) / ma if ma else 0.0


def _pass_liquidity_and_spread(tkr):
    qv = 0.0
    if isinstance(tkr.get("info"), dict):
        try:
            qv = float(tkr["info"].get("volCcy24h") or 0.0)
        except Exception:
            qv = 0.0
    if qv == 0.0:
        qv = float(tkr.get("quoteVolume") or tkr.get("quoteVolume24h") or 0.0)
    if qv < UPGRADE["min_quote_volume_24h"]:
        return False
    bid = tkr.get("bid")
    ask = tkr.get("ask")
    if bid and ask and bid > 0:
        spr = (ask - bid) / bid
        if spr > UPGRADE["max_spread"]:
            return False
    return True


logger = logging.getLogger("AUTO_SELL")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
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

exchange = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_API_SECRET,
    "password": OKX_API_PASSPHRASE,
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})

def init_storage_sheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive"
    ]

    creds = ServiceAccountCredentials.from_json_keyfile_name(
        "credentials.json", scope
    )

    client = gspread.authorize(creds)

    # 🔥 Sheet bạn vừa tạo
    sheet = client.open("spot_entry_storage").sheet1
    return sheet


storage_sheet = init_storage_sheet()

def load_entry_prices():
    data = {}
    try:
        records = storage_sheet.get_all_records()

        for row in records:
            symbol = row.get("Symbol")
            price = row.get("Entry Price")

            if symbol and price:
                data[symbol] = {
                    "price": float(price),
                    "stop": row.get("Stop"),
                    "tp": row.get("TP"),
                    "timestamp": row.get("Timestamp")
                }

        logger.info(f"📂 Loaded {len(data)} entries từ Google Sheet")
        return data

    except Exception as e:
        logger.error(f"❌ Lỗi load Google Sheet: {e}")
        return {}


def send_to_telegram(message):
    token = TELEGRAM_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.warning("⚠️ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        res = requests.post(url, data=data, timeout=15)
        if not res.ok:
            logger.warning(f"⚠️ Telegram API lỗi: {res.status_code} - {res.text}")
            return False
        return True
    except Exception as e:
        logger.warning(f"⚠️ Không thể gửi Telegram: {e}")
        return False


def pre_buy_screen_and_sizing(symbol, fallback_usdt):
    sym_slash = symbol.replace("-", "/")
    try:
        tkr = exchange.fetch_ticker(sym_slash)
    except Exception as e:
        logger.info(f"⛔ Bỏ {symbol}: không lấy được ticker ({e})")
        return False, None, None, None, None, "no_ticker"
    if not _pass_liquidity_and_spread(tkr):
        return False, None, None, None, None, "liquidity"
    entry = float(tkr.get("last") or 0.0)
    if entry <= 0:
        return False, None, None, None, None, "bad_price"
    o15 = exchange.fetch_ohlcv(sym_slash, timeframe="15m", limit=120)
    if len(o15) < 40:
        return False, None, None, None, None, "no_ohlcv"
    adx_val = _adx14(o15)
    atrp = _atr_pct(o15)
    closes15 = [x[4] for x in o15]
    bbw = _bb_width(closes15)
    vols = [x[5] for x in o15][-50:]
    vthr = _percentile(vols, UPGRADE["vol_pctile"]) or 0.0
    vol_ok = len(vols) >= 10 and vols[-1] >= max(vthr, sum(vols) / len(vols))

    btc15 = exchange.fetch_ohlcv("BTC/USDT", timeframe="15m", limit=80)
    btc_ok = True
    if len(btc15) >= 3:
        nowp = btc15[-1][4]
        past = btc15[-3][4]
        btc_ok = ((nowp - past) / past) > -UPGRADE["btc_drop_block"]

    choppy_ok = adx_val is not None and adx_val >= UPGRADE["min_adx"] and atrp >= UPGRADE["min_atr_pct"]
    widths = []
    for i in range(20, len(closes15)):
        widths.append(_bb_width(closes15[:i]))
    p25 = _percentile(widths, UPGRADE["min_bbwidth_pctile"]) or 0.0
    bbw_ok = bbw >= p25
    if not (choppy_ok and bbw_ok and vol_ok and btc_ok):
        return False, None, None, None, None, "filters"

    lowN = min(x[3] for x in o15[-10:])
    stop_atr = entry - 1.8 * (atrp * entry)
    stop = max(lowN, stop_atr)
    if stop >= entry:
        return False, None, None, None, None, "rr_invalid"
    tp = entry + UPGRADE["min_rr"] * (entry - stop)

    bal = exchange.fetch_balance()
    free_usdt = float(bal.get("USDT", {}).get("free", 0.0))
    risk_usdt = free_usdt * UPGRADE["risk_per_trade"]
    loss_per_unit = entry - stop
    amt = risk_usdt / loss_per_unit if loss_per_unit > 0 else 0.0
    if amt * entry < 5:
        amt = fallback_usdt / entry
    return True, float(amt), float(entry), float(stop), float(tp), "ok"

def _save_bought_coin(symbol: str, entry_price: float, stop_price, tp_price):
    global spot_entry_prices

    key = symbol.upper().replace("/", "-")

    try:
        records = storage_sheet.get_all_records()

        for i, row in enumerate(records):
            if row.get("Symbol") == key:
                storage_sheet.update(f"B{i+2}:E{i+2}", [[
                    entry_price,
                    stop_price,
                    tp_price,
                    _now_iso()
                ]])
                break
        else:
            storage_sheet.append_row([
                key,
                entry_price,
                stop_price,
                tp_price,
                _now_iso()
            ])

        # update RAM
        spot_entry_prices[key] = {
            "price": entry_price,
            "stop": stop_price,
            "tp": tp_price,
            "timestamp": _now_iso()
        }

        return key, spot_entry_prices[key]

    except Exception as e:
        logger.error(f"❌ Lỗi ghi Google Sheet: {e}")
        return key, {}


def auto_sell_once():
    global spot_entry_prices
    logger.info("🟢 [AUTO SELL WATCHER] Đã khởi động luồng kiểm tra auto sell")

    new_data = load_entry_prices()
    if isinstance(new_data, dict):
        spot_entry_prices = new_data.copy()
    else:
        spot_entry_prices = {}
        logger.warning("⚠️ Dữ liệu load từ JSON không phải dict!")

    try:
        logger.info("🔄 [AUTO SELL] Kiểm tra ví SPOT để chốt lời...")
        balances = exchange.fetch_balance()
        tickers = exchange.fetch_tickers()
        updated_prices = spot_entry_prices.copy()

        for coin, balance_data in balances.items():
            was_updated = False
            try:
                if not isinstance(balance_data, dict):
                    continue
                if "total" not in balance_data:
                    continue

                balance = float(balance_data.get("total", 0))
                if balance <= 0:
                    continue
                if balance < 1:
                    continue
                if coin.upper() == "USDT":
                    continue

                symbol_dash = f"{coin}-USDT"
                symbol_slash = f"{coin}/USDT"
                ticker = tickers.get(symbol_dash) or tickers.get(symbol_slash)
                if not ticker or "last" not in ticker:
                    logger.warning(f"⚠️ Không có giá hiện tại cho {symbol_dash} hoặc {symbol_slash} (ticker=None hoặc thiếu key 'last')")
                    continue

                try:
                    current_price = float(ticker["last"])
                except Exception as e:
                    logger.warning(f"⚠️ Giá hiện tại của {coin} KHÔNG hợp lệ: {ticker.get('last')} ({e})")
                    continue

                entry_data = spot_entry_prices.get(symbol_dash)
                if not isinstance(entry_data, dict):
                    logger.warning(f"⚠️ {symbol_dash} entry_data KHÔNG phải dict: {entry_data}")
                    continue

                entry_price = entry_data.get("price")
                if not isinstance(entry_price, (int, float)):
                    logger.warning(f"⚠️ {symbol_dash} entry_price KHÔNG phải số: {entry_price}")
                    continue

                stop_in = entry_data.get("stop")
                tp_in = entry_data.get("tp")

                if isinstance(tp_in, (int, float)) and current_price >= tp_in:
                    logger.info(f"🎯 TP hit {symbol_dash}: entry={entry_price} tp={tp_in} last={current_price}")
                    try:
                        exchange.create_market_sell_order(symbol_slash, balance)
                        logger.info(f"✅ Đã bán TP {symbol_dash} số lượng {balance}")
                        updated_prices.pop(symbol_dash, None)
                        # xoá khỏi sheet
                        try:
                            records = storage_sheet.get_all_records()
                            for i, row in enumerate(records):
                                if row.get("Symbol") == symbol_dash:
                                    storage_sheet.delete_rows(i + 2)
                                    break
                        except Exception as e:
                            logger.warning(f"⚠️ Không thể xoá {symbol_dash} khỏi sheet: {e}")
                        was_updated = True
                    except Exception as e:
                        logger.error(f"❌ Lỗi bán TP {symbol_dash}: {e}")
                    if was_updated:
                        spot_entry_prices = updated_prices.copy()
                        save_entry_prices(spot_entry_prices)
                    continue

                if isinstance(stop_in, (int, float)) and current_price <= stop_in:
                    logger.info(f"🛑 SL hit {symbol_dash}: entry={entry_price} sl={stop_in} last={current_price}")
                    try:
                        exchange.create_market_sell_order(symbol_slash, balance)
                        logger.info(f"✅ Đã bán SL {symbol_dash} số lượng {balance}")
                        updated_prices.pop(symbol_dash, None)
                        # xoá khỏi sheet
                        try:
                            records = storage_sheet.get_all_records()
                            for i, row in enumerate(records):
                                if row.get("Symbol") == symbol_dash:
                                    storage_sheet.delete_rows(i + 2)
                                    break
                        except Exception as e:
                            logger.warning(f"⚠️ Không thể xoá {symbol_dash} khỏi sheet: {e}")
                        was_updated = True
                    except Exception as e:
                        logger.error(f"❌ Lỗi bán SL {symbol_dash}: {e}")
                    if was_updated:
                        spot_entry_prices = updated_prices.copy()
                        save_entry_prices(spot_entry_prices)
                    continue

                percent_gain = ((current_price - entry_price) / entry_price) * 100
                if percent_gain >= 30:
                    logger.info(f"✅ CHỐT LỜI: {symbol_dash} tăng {percent_gain:.2f}% từ {entry_price} => {current_price}")
                    try:
                        exchange.create_market_sell_order(symbol_slash, balance)
                        logger.info(f"💰 Đã bán {symbol_dash} số lượng {balance} để chốt lời")
                        updated_prices.pop(symbol_dash, None)
                        # xoá khỏi sheet
                        try:
                            records = storage_sheet.get_all_records()
                            for i, row in enumerate(records):
                                if row.get("Symbol") == symbol_dash:
                                    storage_sheet.delete_rows(i + 2)
                                    break
                        except Exception as e:
                            logger.warning(f"⚠️ Không thể xoá {symbol_dash} khỏi sheet: {e}")
                        was_updated = True
                    except Exception as e:
                        logger.error(f"❌ Lỗi khi bán {symbol_dash}: {e}")

                if was_updated:
                    spot_entry_prices = updated_prices.copy()
                    save_entry_prices(spot_entry_prices)
            except Exception as e:
                logger.error(f"❌ Lỗi khi xử lý coin {coin}: {e}")
                continue
    except Exception as e:
        logger.error(f"❌ Lỗi chính trong auto_sell_once(): {e}")


def fetch_sheet():
    try:
        csv_url = SPREADSHEET_URL.replace("/edit#gid=", "/export?format=csv&gid=")
        res = requests.get(csv_url, timeout=20)
        res.raise_for_status()
        return list(csv.reader(res.content.decode("utf-8").splitlines()))
    except Exception as e:
        logger.error(f"❌ Không thể tải Google Sheet: {e}")
        return []


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


def get_short_term_trend(symbol):
    score = 0
    timeframes = ["1h", "4h", "1d"]

    for tf in timeframes:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol.replace("-", "/"), timeframe=tf, limit=50)
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
    return "KHÔNG RÕ"


def _process_buy(symbol, trend_label, usdt_amount=20):
    global spot_entry_prices
    price = float(exchange.fetch_ticker(symbol.replace("-", "/"))["last"])
    amount = round(usdt_amount / price, 6)
    logger.info(f"💰 [{trend_label}] Mua {amount} {symbol} với {usdt_amount} USDT (giá {price})")

    sym_slash = symbol.replace("-", "/")
    passed, amt2, entry2, stop2, tp2, reason = pre_buy_screen_and_sizing(symbol, usdt_amount)
    if not passed:
        logger.info(f"⛔ Bỏ {sym_slash} lý do: {reason}")
        return False

    amount = amt2
    order = exchange.create_market_buy_order(sym_slash, amount)
    logger.info(f"✅ BUY {sym_slash}: amount={amount} ~ {amount * entry2:.2f} USDT @~{entry2}")

    try:
        key, saved_data = _save_bought_coin(symbol, entry2, stop2 if UPGRADE["use_stop_for_spot"] else None, tp2)
        logger.info(f"✅ Đã mua {symbol} theo {trend_label}: {order}")
        logger.info(f"💾 Đã lưu JSON cho {key}: {saved_data}")

        content = json.dumps({key: saved_data}, indent=2, ensure_ascii=False)
        send_to_telegram(f"✅ Đã mua {key} và cập nhật JSON:\n```\n{content}\n```")
    except Exception as e:
        logger.warning(f"⚠️ Không thể gửi Telegram hoặc lưu JSON cho {symbol}: {e}")

    time.sleep(1)
    return True


def run_bot():
    rows = fetch_sheet()

    for i, row in enumerate(rows):
        try:
            if i == 0:
                continue
            if not row or len(row) < 2:
                logger.warning(f"⚠️ Dòng {i} không hợp lệ: {row}")
                continue

            symbol = row[0].strip().upper()
            signal = row[1].strip().upper()
            gia_mua = float(row[2]) if len(row) > 2 and row[2] and row[2] != "Giá" else None
            ngay = row[3].strip() if len(row) > 3 else ""
            da_mua = row[5].strip().upper() if len(row) > 5 else ""

            logger.info(f"🛒 Đang xét mua {symbol}...")

            if not gia_mua or da_mua == "ĐÃ MUA":
                logger.info(f"⏩ Bỏ qua {symbol} do {'đã mua' if da_mua == 'ĐÃ MUA' else 'thiếu giá'}")
                continue

            if signal != "MUA MẠNH":
                logger.info(f"❌ {symbol} bị loại do tín hiệu Sheet = {signal}")
                continue

            if len(row) > 4 and row[4].strip():
                try:
                    freq_minutes = int(row[4].strip())
                    signal_time = datetime.strptime(ngay, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone(timedelta(hours=7))
                    )
                    now_vn = datetime.now(timezone(timedelta(hours=7)))
                    elapsed = (now_vn - signal_time).total_seconds() / 60
                    if elapsed > freq_minutes:
                        logger.info(f"⏱ Bỏ qua {symbol} vì đã quá hạn {freq_minutes} phút (đã qua {int(elapsed)} phút)")
                        continue
                except Exception as e:
                    logger.warning(f"⚠️ Không thể kiểm tra tần suất cho {symbol}: {e}")

            coin_name = symbol.split("-")[0]
            balances = exchange.fetch_balance()
            asset_balance = balances.get(coin_name, {}).get("total", 0)
            if asset_balance and asset_balance > 1:
                logger.info(f"❌ Bỏ qua {symbol} vì đã có {asset_balance} {coin_name} trong ví")
                continue

            trend = get_short_term_trend(symbol)
            logger.info(f"📉 Xu hướng ngắn hạn của {symbol} = {trend}")

            if trend == "TĂNG":
                try:
                    ohlcv = exchange.fetch_ohlcv(symbol.replace("-", "/"), timeframe="1h", limit=30)
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

                    _process_buy(symbol, "TĂNG")
                    continue
                except Exception as e:
                    logger.error(f"❌ Lỗi khi mua {symbol} theo trend TĂNG: {e}")
                    continue

            if trend == "SIDEWAY":
                try:
                    ohlcv = exchange.fetch_ohlcv(symbol.replace("-", "/"), timeframe="1h", limit=30)
                    closes = [c[4] for c in ohlcv]
                    volumes = [c[5] for c in ohlcv]
                    rsi = compute_rsi(closes, period=14)
                    vol = volumes[-1]
                    vol_sma20 = sum(volumes[-20:]) / 20
                    price_now = closes[-1]
                    price_3bars_ago = closes[-4]
                    price_change = (price_now - price_3bars_ago) / price_3bars_ago * 100

                    if rsi > 70 or vol > vol_sma20 * 2 or price_change > 10:
                        logger.info(f"⛔ {symbol} bị loại do dấu hiệu FOMO (RSI={rsi:.2f}, Δgiá 3h={price_change:.1f}%, vol={vol:.0f})")
                        continue
                    if len(closes) < 20:
                        logger.warning(f"⚠️ Không đủ dữ liệu nến cho {symbol}")
                        continue
                    if rsi >= 55 or vol >= vol_sma20:
                        logger.info(f"⛔ {symbol} bị loại (SIDEWAY nhưng không nén đủ mạnh)")
                        continue

                    _process_buy(symbol, "SIDEWAY")
                except Exception as e:
                    logger.error(f"❌ Lỗi khi mua {symbol} theo SIDEWAY: {e}")
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý dòng {i} - {row}: {e}")


def main():
    print(f"🟢 Bắt đầu bot lúc {datetime.now(timezone.utc).isoformat()}")
    run_bot()
    auto_sell_once()


if __name__ == "__main__":
    main()
