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

spot_entry_prices = {}  # ✅ khai báo biến toàn cục
spot_entry_prices_path = "spot_entry_prices.json"

def auto_sell_watcher():
    logging.info("🟢 [AUTO SELL WATCHER] Đã khởi động luồng kiểm tra auto sell")
    spot_entry_prices = load_entry_prices()
    while True:
        try:
            logger.info("🔁 [AUTO SELL] Kiểm tra ví SPOT để chốt lời...")
            balances = exchange.fetch_balance()
            tickers = exchange.fetch_tickers()

            updated_prices = spot_entry_prices.copy()

            for coin, balance_data in balances.items():
                try:
                    if not isinstance(balance_data, dict):
                        logger.warning(f"⚠️ {coin} không phải dict: {balance_data}")
                        continue

                    balance = balance_data.get("total", 0)
                    if not balance or balance <= 0:
                        continue

                    logger.debug(f"🧮 [AUTO SELL] Xét coin: {coin} | Số dư: {balance}")
                    symbol = f"{coin}-USDT"
                    if symbol not in tickers:
                        continue

                    current_price = tickers[symbol]['last']

                    # Lấy entry từ dict giá mua
                    entry_data = spot_entry_prices.get(symbol)
                    logger.debug(f"📦 [DEBUG] entry_str cho {symbol}: {entry_data} ({type(entry_data)})")

                    if not entry_data:
                        logger.warning(f"⚠️ Không có giá mua cho {symbol}")
                        continue

                    if isinstance(entry_data, dict):
                        entry_price = entry_data.get("price")
                        logger.debug(f"📦 [DEBUG] Đã lấy giá từ dict cho {symbol}: {entry_price}")
                    else:
                        entry_price = entry_data

                    if not isinstance(entry_price, (int, float, str)):
                        logger.warning(f"⚠️ entry_str cho {symbol} không hợp lệ: {entry_price}")
                        continue

                    try:
                        entry_price = float(entry_price)
                    except ValueError:
                        logger.warning(f"⚠️ Không thể convert giá mua {entry_price} thành float cho {symbol}")
                        continue

                    target_price = entry_price * 1.1

                    if current_price >= target_price:
                        logger.info(f"🚀 BÁN {symbol}: giá hiện tại {current_price} > {target_price} (entry {entry_price})")
                        order = exchange.create_market_sell_order(symbol, balance)
                        logger.info(f"✅ Đã bán {symbol}: {order}")
                        updated_prices.pop(symbol, None)
                    else:
                        logger.debug(f"⏳ {symbol} chưa đủ lời: {current_price} < {target_price}")

                except Exception as e:
                    logger.warning(f"⚠️ Lỗi khi xử lý {coin}: {e}")

            save_entry_prices(updated_prices)
            spot_entry_prices = updated_prices

        except Exception as e:
            logger.error(f"❌ Lỗi AUTO SELL: {e}")

        time.sleep(180)
        
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
                    elapsed = (now_vn - signal_time).total_seconds() / 60
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
                    usdt_amount = 10
                    price = exchange.fetch_ticker(symbol)['last']
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
                    order = exchange.create_market_buy_order(symbol, amount)
                    logger.info(f"✅ Đã mua {symbol} theo TĂNG: {order}")
                    # Giả sử sau khi vào lệnh mua thành công:
                    spot_entry_prices[symbol] = {
                        "price": price,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    save_entry_prices(spot_entry_prices)
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
                    usdt_amount = 10
                    price = exchange.fetch_ticker(symbol)['last']
                    amount = round(usdt_amount / price, 6)
                    logger.info(f"💰 [SIDEWAY] Mua {amount} {symbol} với {usdt_amount} USDT (giá {price})")
                    order = exchange.create_market_buy_order(symbol, amount)
                    logger.info(f"✅ Đã mua {symbol} theo SIDEWAY: {order}")
                    # Giả sử sau khi vào lệnh mua thành công:
                    spot_entry_prices[symbol] = {
                        "price": price,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    save_entry_prices(spot_entry_prices)
                    
                except Exception as e:
                    logger.error(f"❌ Lỗi khi mua {symbol} theo SIDEWAY: {e}")            
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý dòng {i} - {row}: {e}")
if __name__ == "__main__":
    logger.info("🚀 Khởi động bot SPOT OKX")
    
    # Gọi bot mua SPOT như bình thường
    run_bot()
    logger.info("✅ Đã chạy xong hàm run_bot(), chuẩn bị chuyển sang auto_sell_watcher()...")
        
    # Gọi hàm auto_sell_watcher trong thread riêng
    threading.Thread(target=auto_sell_watcher, daemon=True).start()
    
    # ✅ Giữ chương trình sống (để thread không bị kill)
    while True:
        time.sleep(60)

