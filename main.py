from datetime import datetime, timedelta, timezone
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
spot_entry_prices_path = os.path.join(os.path.dirname(__file__), "spot_entry_prices.json")        
def load_entry_prices():
    spot_entry_prices_path = os.path.join(os.path.dirname(__file__), "spot_entry_prices.json") 
    try:
        if not os.path.exists(spot_entry_prices_path):
            logger.warning(f"⚠️ File {spot_entry_prices_path} KHÔNG tồn tại! => Trả về dict rỗng.")
            return {}
        with open(spot_entry_prices_path, "r") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(f"⚠️ Dữ liệu trong {spot_entry_prices_path} KHÔNG phải dict: {type(data)}")
                return {}
            logger.debug(f"📥 Đã load JSON từ file: {json.dumps(data, indent=2)}")  # 👈 Log toàn bộ json
            return data
    except Exception as e:
        logger.error(f"❌ Lỗi khi load {spot_entry_prices_path}: {e}")
        return {}
        
def auto_sell_once():
    global spot_entry_prices
    logging.info("🟢 [AUTO SELL WATCHER] Đã khởi động luồng kiểm tra auto sell")

    # Load lại dữ liệu

    new_data = load_entry_prices()
    if isinstance(new_data, dict):
        spot_entry_prices.update(new_data)
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
                and amount >= 1
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
            try:
                if not isinstance(balance_data, dict):
                    logger.warning(f"⚠️ {coin} không phải dict: {balance_data}")
                    continue
        
                balance = balance_data.get("total", 0)
                if not balance or balance <= 0:
                    continue
                    
                symbol_dash = f"{coin}-USDT"
                symbol_slash = f"{coin}/USDT"
                # Ưu tiên symbol có trong tickers
                ticker = tickers.get(symbol_dash) or tickers.get(symbol_slash)
                
                if not ticker:
                    logger.warning(f"⚠️ Không có giá hiện tại cho {symbol_dash} hoặc {symbol_slash}")
                    continue
        
                # Các bước xử lý tiếp theo...
                current_price = ticker["last"]
                logger.debug(f"🔍 Đang kiểm tra coin: {coin}, symbol: {symbol}, entry_keys: {list(spot_entry_prices.keys())}")
                if not isinstance(symbol, str):
                    logger.warning(f"⚠️ symbol không phải string: {symbol} ({type(symbol)})")
                    continue
                entry_data = spot_entry_prices.get(symbol.upper())

                
                # ✅ Kiểm tra dữ liệu entry_data phải là dict
                if not isinstance(entry_data, dict):
                    logger.warning(f"⚠️ {symbol} entry_data KHÔNG phải dict: {entry_data}")
                    continue
                
                # ✅ Lấy giá mua ban đầu
                entry_price = entry_data.get("price")
                if not isinstance(entry_price, (int, float)):
                    logger.warning(f"⚠️ {symbol} entry_price không phải số: {entry_price}")
                    continue
                
                # ✅ Tính phần trăm lời
                percent_gain = ((current_price - entry_price) / entry_price) * 100
                # ✅ Kiểm tra nếu đạt mức chốt lời, Sau khi bán xong, xoá coin khỏi danh sách theo dõi
                was_updated = False  # ✅ Thêm biến cờ theo dõi
                if percent_gain >= 20:
                    logger.info(f"✅ CHỐT LỜI: {symbol} tăng {percent_gain:.2f}% từ {entry_price} => {current_price}")
                    try:
                        exchange.create_market_sell_order(symbol, balance)
                        logger.info(f"💰 Đã bán {symbol} số lượng {balance} để chốt lời")
                        updated_prices.pop(symbol, None)     # ✅ Xoá khỏi danh sách theo dõi
                        was_updated = True                   # ✅ Đánh dấu có thay đổi
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
                    order = exchange.create_market_buy_order(symbol, amount)
                    logger.info(f"✅ Đã mua {symbol} theo TĂNG: {order}")
                    # Giả sử sau khi vào lệnh mua thành công:
                    # ✅ Load lại dữ liệu cũ để tránh mất dữ liệu các coin khác
                    spot_entry_prices.update(load_entry_prices())
                    spot_entry_prices[symbol] = {
                        "price": price,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    save_entry_prices(spot_entry_prices)
                    time.sleep(1) # đảm bảo file được ghi hoàn toàn
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
                    # ✅ Load lại dữ liệu cũ để tránh mất dữ liệu các coin khác
                    spot_entry_prices.update(load_entry_prices())
                    spot_entry_prices[symbol] = {
                        "price": price,
                        "timestamp": datetime.utcnow().isoformat() + "Z"
                    }
                    save_entry_prices(spot_entry_prices)
                    time.sleep(1) # đảm bảo file được ghi hoàn toàn
                except Exception as e:
                    logger.error(f"❌ Lỗi khi mua {symbol} theo SIDEWAY: {e}")            
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý dòng {i} - {row}: {e}")
            
def save_entry_prices(prices_dict):
    try:
        with open("spot_entry_prices.json", "w") as f:
            json.dump(prices_dict, f, indent=2)
            f.flush()  # 🔁 Đảm bảo ghi xong
            os.fsync(f.fileno())  # 💾 Ghi ra đĩa thật (tránh ghi tạm vào cache)
        logger.debug("💾 Đã ghi file spot_entry_prices.json xong.")
        logger.debug(f"📦 Nội dung file: \n{json.dumps(prices_dict, indent=2)}")
    except Exception as e:
        logger.error(f"❌ Lỗi khi lưu file spot_entry_prices.json: {e}")
        
def main():
    now = datetime.utcnow()
    minute = now.minute
    hour = now.hour

    print(f"🕰️ Bắt đầu lúc {now.isoformat()}")
    # ✅ Chỉ chạy run_bot nếu phút hiện tại chia hết 60 (ví dụ: 00:00, 01:00, 02:00...)
    if minute == 0:
        run_bot()
        logger.info("🟢 Bắt đầu chạy auto_sell_once() sau run_bot()")
        auto_sell_once()
    else:
        print(f"⌛ Chưa đến thời điểm chạy run_bot(), phút hiện tại = {minute}")
        logger.info("🟢 Bắt đầu chạy auto_sell_once() khi KHÔNG có run_bot()")
        auto_sell_once()    
if __name__ == "__main__":
    main()
