
import ccxt
import requests
import pandas as pd
import os
import time
import csv
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# ✅ Lấy biến môi trường
SPREADSHEET_URL = os.environ.get("SPREADSHEET_URL")
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE")

# ✅ Cấu hình OKX
exchange = ccxt.okx({
    "apiKey": OKX_API_KEY,
    "secret": OKX_API_SECRET,
    "password": OKX_API_PASSPHRASE,
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})

# ✅ Đọc Google Sheet public
def fetch_sheet():
    try:
        csv_url = SPREADSHEET_URL.replace("/edit#gid=", "/export?format=csv&gid=")
        res = requests.get(csv_url)
        res.raise_for_status()
        return list(csv.reader(res.content.decode("utf-8").splitlines()))
    except Exception as e:
        logging.error(f"❌ Không thể tải Google Sheet: {e}")
        return []

# ✅ Lấy tín hiệu từ TradingView
def check_tradingview_signal(symbol: str) -> str:
    try:
        url = "https://scanner.tradingview.com/crypto/scan"
        payload = {
            "symbols": {"tickers": [f"BINANCE:{symbol}"]},
            "columns": ["recommendation"]
        }
        res = requests.post(url, json=payload)
        res.raise_for_status()
        signal = res.json()["data"][0]["d"][0]
        return signal.upper()
    except Exception as e:
        logging.warning(f"⚠️ Không lấy được tín hiệu TV cho {symbol}: {e}")
        return "UNKNOWN"

# ✅ Hàm chính chạy bot
def run_bot():
    now = datetime.utcnow()
    rows = fetch_sheet()
    if not rows:
        logging.error("❌ Không có dữ liệu từ Google Sheet")
        return

    header = rows[0]
    rows = rows[1:]
    for i, row in enumerate(rows):
        try:
            coin = row[0].strip().upper()
            signal_sheet = row[1].strip().upper()
            bought = row[5].strip().upper() if len(row) > 5 else ""

            if not coin or "USDT" not in coin:
                continue
            symbol = coin.replace("-", "/")
            symbol_okx = symbol.upper()

            # ✅ Check hợp lệ SPOT
            market = exchange.markets.get(symbol_okx.replace("/", "-"))
            if not market or market.get("spot") != True:
                continue

            logging.info(f"✅ {symbol} là SPOT hợp lệ")

            # ✅ Check tín hiệu từ sheet
            if signal_sheet != "MUA MẠNH" or bought:
                continue

            # ✅ Check tín hiệu TradingView
            tv_signal = check_tradingview_signal(symbol_okx.replace("/", ""))
            if tv_signal not in ["BUY", "STRONG_BUY"]:
                logging.info(f"⛔ {symbol} bị loại do TV = {tv_signal}")
                continue

            # ✅ Lấy giá hiện tại
            ticker = exchange.fetch_ticker(symbol_okx)
            price = ticker["last"]

            # ✅ Đặt lệnh mua nếu chưa có
            amount = round(10 / price, 6)
            logging.info(f"🟢 Đặt lệnh mua {amount} {symbol} (~10 USDT)")
            order = exchange.create_market_buy_order(symbol_okx, amount)
            logging.info(f"✅ Đã mua {symbol}: orderId = {order['id']}")
        except Exception as e:
            logging.warning(f"⚠️ Lỗi tại dòng {i}: {e}")

if __name__ == "__main__":
    run_bot()
