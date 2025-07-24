from datetime import datetime, timedelta, timezone
import threading
import os
import csv
import requests
import logging
import ccxt
import time
import json

# json_path = "spot_entry_prices.json"
# if os.path.exists(json_path):
#    os.remove(json_path)
#    print("✅ Đã xoá file spot_entry_prices.json do nghi ngờ lỗi dữ liệu")
    
# Cấu hình logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s:%(message)s")
logger = logging.getLogger()

# Đọc biến môi trường
SPREADSHEET_URL = os.environ.get("SPREADSHEET_URL")
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE")

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
    timeframes = ["1h", "4h", "1d", "1w"]

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
                    elapsed = (now_vn - signal_time).total_seconds() / 60
                    if elapsed > freq_minutes:
                        logger.info(f"⏱ Bỏ qua {symbol} vì đã quá hạn {freq_minutes} phút (đã qua {int(elapsed)} phút)")
                        continue
                except Exception as e:
                    logger.warning(f"⚠️ Không thể kiểm tra tần suất cho {symbol}: {e}")

            # ✅ Phân tích xu hướng ngắn hạn thay cho TradingView
            trend = get_short_term_trend(symbol)
            logger.info(f"📈 Xu hướng ngắn hạn của {symbol} = {trend}")

            if trend != "TĂNG":
                logger.info(f"❌ Bỏ qua {symbol} vì xu hướng ngắn hạn = {trend}")
                continue

            # ✅ Kiểm tra nếu đã có coin trong ví Spot
            coin_name = symbol.split("-")[0]
            balances = exchange.fetch_balance()
            asset_balance = balances.get(coin_name, {}).get('total', 0)

            if asset_balance and asset_balance > 0:
                logger.info(f"❌ Bỏ qua {symbol} vì đã có {asset_balance} {coin_name} trong ví")
                continue

            # ✅ Nếu tới đây thì đủ điều kiện mua SPOT
            try:
                usdt_amount = 10
                price = exchange.fetch_ticker(symbol)['last']
                amount = round(usdt_amount / price, 6)

                logger.info(f"💰 Đặt lệnh mua {amount} {symbol} với tổng {usdt_amount} USDT (giá {price})")
                order = exchange.create_market_buy_order(symbol, amount)
                logger.info(f"✅ Đã mua {symbol}: {order}")
            except Exception as e:
                logger.error(f"❌ Lỗi khi mua {symbol}: {e}")
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý dòng {i} - {row}: {e}")

if __name__ == "__main__":
    run_bot()

spot_entry_prices_path = "spot_entry_prices.json"
# Tải lại giá mua từ file nếu có
def load_entry_prices():
    try:
        with open(spot_entry_prices_path, "r") as f:
            return json.load(f)
    except Exception:
        return {}

# Lưu lại sau khi bán xong
def save_entry_prices(data):
    with open(spot_entry_prices_path, "w") as f:
        json.dump(data, f)

def auto_sell_watcher():
    global spot_entry_prices
    spot_entry_prices = load_entry_prices()

    while True:
        try:
            logger.info("🔁 [AUTO SELL] Kiểm tra ví SPOT để chốt lời...")
            balances = exchange.fetch_balance()
            tickers = exchange.fetch_tickers()

            updated_prices = spot_entry_prices.copy()

            for coin, balance_data in balances.items():
                try:
                    for coin, balance_data in balances.items():
                        if not isinstance(balance_data, dict):
                            logger.warning(f"⚠️ {coin} không phải dict: {balance_data}")
                            continue
                        balance = balance_data.get("total", 0)
                    if not balance or balance <= 0:
                        continue

                    # Tìm symbol tương ứng
                    logger.debug(f"🧾 [AUTO SELL] Xét coin: {coin} | Số dư: {balance}")
                    symbol = f"{coin}-USDT"
                    if symbol not in tickers:
                        continue

                    current_price = tickers[symbol]['last']

                    # Phải có giá mua hợp lệ
                    entry_str = spot_entry_prices.get(symbol)
                    try:
                        if not entry_str or not isinstance(entry_str, (int, float, str)):
                            logger.warning(f"⚠️ Không có giá mua hợp lệ cho {symbol}: '{entry_str}'")
                            continue
                        
                        try:
                            entry_price = float(entry_str)
                            logger.debug(f"📊 {symbol}: Giá mua = {entry_price}, Giá hiện tại = {current_price}, Target = {entry_price * 1.1}")
                        except ValueError:
                            logger.warning(f"⚠️ Không thể convert giá mua {entry_str} thành float cho {symbol}")
                            continue
                    except Exception:
                        logger.warning(f"⚠️ Giá mua không hợp lệ cho {symbol}: '{entry_str}'")
                        continue

                    target_price = entry_price * 1.1
                    if current_price >= target_price:
                        logger.info(f"🚀 BÁN {symbol}: giá hiện tại {current_price} > {target_price} (entry {entry_price})")
                        order = exchange.create_market_sell_order(symbol, balance)
                        logger.info(f"✅ Đã bán {symbol}: {order}")
                        updated_prices.pop(symbol, None)  # Xoá sau khi đã bán
                    else:
                        logger.debug(f"⏳ {symbol} chưa đủ lời: {current_price} < {target_price}")
                except Exception as e:
                    logger.warning(f"⚠️ Lỗi khi xử lý {coin}: {e}")

            save_entry_prices(updated_prices)
            spot_entry_prices = updated_prices

        except Exception as e:
            logger.error(f"❌ Lỗi AUTO SELL: {e}")

        time.sleep(180)


# Gọi thread auto bán sau run_bot
if __name__ == "__main__":
    threading.Thread(target=auto_sell_watcher, daemon=True).start()
    run_bot()
    # ✅ Giữ chương trình sống (để thread không bị kill)
    while True:
        time.sleep(60)
