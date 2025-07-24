import os
import csv
import requests
import logging
from datetime import datetime
import ccxt

# Cấu hình logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s:%(name)s: %(message)s")
logger = logging.getLogger()

# Lấy biến môi trường
API_KEY = os.getenv("OKX_API_KEY")
API_SECRET = os.getenv("OKX_API_SECRET")
API_PASS = os.getenv("OKX_API_PASS")
SHEET_URL = os.getenv("GOOGLE_SHEET_PUBLIC_CSV")  # link CSV của sheet public

# Kết nối OKX
exchange = ccxt.okx({
    "apiKey": API_KEY,
    "secret": API_SECRET,
    "password": API_PASS,
    "enableRateLimit": True,
    "options": {
        "defaultType": "spot"
    }
})


def fetch_sheet():
    try:
        response = requests.get(SHEET_URL)
        response.raise_for_status()
        decoded = response.content.decode('utf-8')
        reader = csv.reader(decoded.splitlines())
        return list(reader)[1:]  # bỏ dòng tiêu đề
    except Exception as e:
        logger.error(f"❌ Lỗi khi đọc Google Sheet: {e}")
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
            symbol_tv = symbol.replace("-", "").upper()
            url = "https://scanner.tradingview.com/crypto/scan"
            payload = {
                "symbols": {"tickers": [f"OKX:{symbol_tv}"], "query": {"types": []}},
                "columns": ["recommendation"]
            }

            logger.debug(f"📡 Gửi request TV: {payload}")
            try:
                res = requests.post(url, json=payload, timeout=5)
                res.raise_for_status()
                data = res.json()
                logger.debug(f"🎯 Phản hồi TV: {data}")

                if not data.get("data") or not data["data"][0]["d"]:
                    logger.info(f"❌ {symbol} bị loại do không có tín hiệu TV")
                    continue

                signal_tv = data["data"][0]["d"][0]
                if signal_tv not in ["BUY", "STRONG_BUY"]:
                    logger.info(f"❌ {symbol} bị loại do tín hiệu TV = {signal_tv}")
                    continue
                logger.info(f"✅ Tín hiệu TV OK: {symbol} = {signal_tv}")

            except Exception as e:
                logger.warning(f"⚠️ Lỗi lấy tín hiệu TV cho {symbol}: {e}")
                continue

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
