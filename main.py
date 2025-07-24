import os
import csv
import requests
import logging
from datetime import datetime
import ccxt

# Cấu hình logging
logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s:%(name)s: %(message)s")
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

            coin = symbol.replace("-USDT", "")
            logger.info(f"🛒 Đang xét mua {symbol}...")

            # Bỏ qua nếu chưa có giá mua hoặc đã mua rồi
            if not gia_mua or da_mua == "ĐÃ MUA":
                logger.info(f"⏩ Bỏ qua {symbol} do {'đã mua' if da_mua == 'ĐÃ MUA' else 'thiếu giá'}")
                continue

            # Kiểm tra tín hiệu sheet
            if signal != "MUA MẠNH":
                logger.info(f"❌ {symbol} bị loại do tín hiệu Sheet = {signal}")
                continue

            # ✅ Gửi tín hiệu check TradingView trực tiếp
            try:
                tv_symbol = normalize_tv_symbol(symbol)
                url = "https://scanner.tradingview.com/crypto/scan"
                payload = {
                    "symbols": {"tickers": [tv_symbol]},
                    "columns": ["recommendation"]
                }
        
                logging.debug(f"📡 Gửi request TV cho {symbol} → {tv_symbol} với payload: {payload}")
                res = requests.post(url, json=payload, timeout=5)
                res.raise_for_status()
        
                data = res.json()
                logging.debug(f"📊 Phản hồi từ TradingView cho {tv_symbol}: {data}")
        
                if not data.get("data"):
                    return None
                return data["data"][0]["d"][0]
        
            except Exception as e:
                logging.warning(f"⚠️ Lỗi lấy tín hiệu TV cho {symbol}: {e}")
                return None


            # ✅ Mua SPOT
            usdt_amount = 10
            price = exchange.fetch_ticker(symbol.replace("-", "/"))['last']
            quantity = round(usdt_amount / price, 6)

            logger.info(f"🟢 Mua {symbol} với khối lượng {quantity} @ {price}")
            order = exchange.create_market_buy_order(symbol.replace("-", "/"), quantity)

            logger.info(f"✅ Đã mua {symbol}: OrderID = {order['id']}")

        except Exception as e:
            logger.warning(f"❌ Lỗi dòng {i}: {e}")


if __name__ == "__main__":
    logger.info("🚀 Bắt đầu chạy bot SPOT OKX...")
    run_bot()
