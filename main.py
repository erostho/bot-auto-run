import os
import csv
import requests
import logging
from datetime import datetime
import ccxt
import time

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

    if score >= 4:
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

            # ✅ Phân tích xu hướng ngắn hạn thay cho TradingView
            trend = get_short_term_trend(symbol)
            logger.info(f"📈 Xu hướng ngắn hạn của {symbol} = {trend}")

            if trend != "TĂNG":
                logger.info(f"❌ Bỏ qua {symbol} vì xu hướng ngắn hạn = {trend}")
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
