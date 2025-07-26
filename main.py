from datetime import datetime, timedelta, timezone
import threading
import os
import csv
import requests
import logging
import ccxt
import time
import json

    
# Cấu hình logging
# logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s:%(message)s")
# logger = logging.getLogger("AUTO_SELL")
logger = logging.getLogger("AUTO_SELL")
logger.setLevel(logging.DEBUG)  # Luôn bật DEBUG

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

def save_entry_prices(prices_dict):
    try:
        with open(spot_entry_prices_path, "w") as f:
            json.dump(prices_dict, f, indent=2)
        logger.debug(f"💾 Đã ghi file spot_entry_prices.json: {prices_dict}")
    except Exception as e:
        logger.error(f"❌ Lỗi khi lưu file spot_entry_prices.json: {e}")
        
def load_entry_prices():
    if not os.path.exists(spot_entry_prices_path):
        logger.warning("⚠️ File spot_entry_prices.json KHÔNG tồn tại! => Trả về dict rỗng")
        return {}

    try:
        with open(spot_entry_prices_path, "r") as f:
            data = json.load(f)

            if not isinstance(data, dict):
                logger.warning("⚠️ Dữ liệu trong spot_entry_prices.json KHÔNG phải dict => Trả về dict rỗng")
                return {}

            logger.debug(f"📥 Đã load spot_entry_prices.json: {data}")
            return data
    except Exception as e:
        logger.error(f"❌ Lỗi khi đọc file spot_entry_prices.json: {e}")
        return {}
        
def auto_sell_watcher():
    logger.info("🌀 BẮT ĐẦU theo dõi auto sell mỗi 3 phút...")

    while True:
        try:
            # Load entry prices từ file
            spot_entry_prices.clear()
            entry_loaded = load_entry_prices()
            if isinstance(entry_loaded, dict):
                spot_entry_prices.update(entry_loaded)
            else:
                logger.warning("⚠️ File entry_prices.json KHÔNG chứa dict. Bỏ qua cập nhật.")

            balances = exchange.fetch_balance()['total']

            for symbol, amount in balances.items():
                if not symbol.endswith("USDT"):
                    continue

                if amount is None or amount == 0:
                    continue

                # Bỏ stablecoin
                if symbol in ["USDT", "USDC", "DAI", "TUSD", "FDUSD"]:
                    continue

                entry_data = spot_entry_prices.get(symbol)

                # ⚠️ Nếu dữ liệu cũ bị lỗi (không phải dict)
                if not isinstance(entry_data, dict):
                    logger.warning(f"⚠️ {symbol} entry_data KHÔNG phải dict: {entry_data} ({type(entry_data)})")
                    continue

                entry_price = entry_data.get("price")
                entry_time_str = entry_data.get("timestamp")
                logger.debug(f"📦 entry_price={entry_price}, entry_time_str={entry_time_str}")

                # Kiểm tra timestamp đúng định dạng
                entry_time = None
                if isinstance(entry_time_str, str):
                    try:
                        clean_time = entry_time_str.replace("Z", "")
                        entry_time = datetime.fromisoformat(clean_time)
                        logger.debug(f"📅 entry_time của {symbol}: {entry_time}")
                    except Exception as e:
                        logger.warning(f"❌ Không parse được timestamp của {symbol}: {entry_time_str}, lỗi: {e}")
                        continue
                else:
                    logger.warning(f"⚠️ timestamp của {symbol} không phải string: {entry_time_str} ({type(entry_time_str)})")
                    continue

                if entry_price is None:
                    logger.warning(f"⚠️ Không có entry_price cho {symbol}")
                    continue

                # Lấy giá thị trường hiện tại
                ticker = exchange.fetch_ticker(symbol)
                current_price = ticker.get("last")

                if current_price is None:
                    logger.warning(f"⚠️ Không lấy được giá hiện tại của {symbol}")
                    continue

                pnl = (current_price - entry_price) / entry_price * 100
                logger.info(f"📊 {symbol}: Giá mua {entry_price:.6f}, Giá hiện tại {current_price:.6f}, Lợi nhuận {pnl:.2f}%")

                if pnl >= 10:
                    logger.info(f"💰 {symbol} đạt lợi nhuận ≥ 10% ⇒ BÁN toàn bộ {amount}")
                    try:
                        order = exchange.create_market_sell_order(symbol, amount)
                        logger.info(f"✅ Đã bán {symbol} với giá {current_price:.6f}, lệnh: {order}")
                        # Xoá khỏi danh sách entry
                        if symbol in spot_entry_prices:
                            del spot_entry_prices[symbol]
                            save_entry_prices(spot_entry_prices)
                            logger.info(f"🗑 Đã xoá entry của {symbol} sau khi bán")
                    except Exception as e:
                        logger.error(f"❌ Lỗi khi bán {symbol}: {e}")
        except Exception as e:
            logger.error(f"❌ Lỗi trong auto_sell_watcher: {e}")

        logger.info("🕒 Đợi >4 phút để kiểm tra lại...")
        time.sleep(250)
        
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
                    # 🔧 Thêm dòng này để đảm bảo không ghi đè file rỗng
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
    
    # ✅ Khởi động thread trước
    threading.Thread(target=auto_sell_watcher, daemon=True).start()
    logging.info("✅ Đã tạo thread auto_sell_watcher")
    
    # Gọi bot mua SPOT như bình thường
    run_bot()
    logger.info("✅ Đã chạy xong hàm run_bot(), chuẩn bị chuyển sang auto_sell_watcher()...")
    
    # ✅ Giữ chương trình sống (để thread không bị kill)
    while True:
        time.sleep(60)

