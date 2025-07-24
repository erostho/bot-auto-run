
import os
import requests
import csv
import logging
from datetime import datetime
import ccxt

logging.basicConfig(level=logging.INFO)

# ✅ Cấu hình OKX
exchange = ccxt.okx({
    "apiKey": os.getenv("OKX_API_KEY"),
    "secret": os.getenv("OKX_API_SECRET"),
    "password": os.getenv("OKX_API_PASSPHRASE"),
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})

# ✅ Đọc Google Sheet
def fetch_sheet():
    try:
        sheet_url = os.getenv("SPREADSHEET_URL")
        if sheet_url is None:
            logging.error("❌ SPREADSHEET_URL không được thiết lập trong biến môi trường")
            return []
        csv_url = sheet_url.replace("/edit#gid=", "/export?format=csv&gid=")
        res = requests.get(csv_url)
        res.raise_for_status()
        return list(csv.reader(res.content.decode("utf-8").splitlines()))
    except Exception as e:
        logging.error(f"❌ Không thể tải Google Sheet: {e}")
        return []

# ✅ Hàm chính
def run_bot():
    logging.info("🤖 Bắt đầu chạy bot SPOT OKX...")
    now = datetime.utcnow()
    rows = fetch_sheet()
    
    if not rows:
        logging.warning("⚠️ Không có dữ liệu từ Google Sheet.")
        return

    header = rows[0]
    logging.info(f"📌 Header: {header}")
    rows = rows[1:]

    for i, row in enumerate(rows):
        try:
            logging.debug(f"🧪 Đang xử lý dòng {i}: {row}")
            if not row or len(row) < 2:
                logging.warning(f"⚠️ Dòng {i} không hợp lệ: {row}")
                continue
            symbol = row[0].strip().upper()  # <-- Phải có dòng này trước khi dùng `symbol`
            logging.info(f"💰 Đang xét mua {symbol}...")
            coin = (row[0] or "").strip().upper()
            signal = (row[1] or "").strip().upper()
            gia_hien_tai = row[2] if len(row) > 2 else ""
            da_mua = (row[5] or "").strip().upper() if len(row) > 5 else ""

            if signal != "MUA MẠNH":
                logging.info(f"⛔ {coin} bị loại do tín hiệu = {signal}")
                continue

            if da_mua == "ĐÃ MUA":
                logging.info(f"✅ {coin} đã mua trước đó → bỏ qua")
                continue
            logging.info(f"🛒 Đang xét mua {coin}...")
            
            # Tín hiệu TV
                try:
                    # 🔁 Chuẩn hóa symbol: DAI-USDT → DAI/USDT
                    symbol_tv = symbol.replace("-", "/").upper()
            
                    url = "https://scanner.tradingview.com/crypto/scan"
                    payload = {
                        "symbols": {
                            "tickers": [f"OKX:{symbol_tv}"]
                        },
                        "columns": ["recommendation"]
                    }
            
                    # 🐛 DEBUG trước khi gửi request
                    logging.debug(f"[DEBUG] 🔍 Gửi yêu cầu TV cho {symbol} (→ {symbol_tv}) với payload: {payload}")
            
                    res = requests.post(url, json=payload, timeout=5)
                    res.raise_for_status()
            
                    data = res.json()
                    logging.debug(f"[DEBUG] 📥 Phản hồi từ TradingView: {data}")
            
                    # ✅ So sánh symbol gửi và symbol trả về
                    returned_symbols = data.get("symbols", [])
                    logging.debug(f"[DEBUG] 🔁 Đối chiếu symbol gửi: OKX:{symbol_tv} ↔ symbols trả về: {returned_symbols}")
            
                    if not data.get("data") or not data["data"][0].get("d"):
                        logging.warning(f"[⚠️] Không có dữ liệu tín hiệu TV cho {symbol_tv}")
                        return None
            
                    return data["data"][0]["d"][0]
            
                except requests.exceptions.RequestException as e:
                    logging.warning(f"⚠️ Lỗi khi gửi yêu cầu TV cho {symbol}: {e}")
                    return None
                except Exception as e:
                    logging.warning(f"⚠️ Lỗi xử lý tín hiệu TV cho {symbol}: {e}")
                    return None
            signal_tv = check_tradingview_signal(symbol)
            if signal_tv not in ["BUY", "STRONG_BUY"]:
                logging.info(f"❌ {symbol} bị loại do tín hiệu TV = {signal_tv}")
                continue
            # Lấy giá thị trường
            try:
                ticker = exchange.fetch_ticker(f"{coin}/USDT")
                last_price = ticker['last']
                logging.info(f"💰 Giá hiện tại {coin}: {last_price}")
            except Exception as e:
                logging.warning(f"⚠️ Không lấy được giá cho {coin}: {e}")
                continue

            # Đặt lệnh mua 10 USDT
            usdt_amount = 10
            amount = round(usdt_amount / last_price, 6)
            logging.info(f"📦 Đặt mua {coin} với số lượng {amount} ({usdt_amount} USDT)")
            # lệnh giả lập:
            # order = exchange.create_market_buy_order(f"{coin}/USDT", amount)

        except Exception as e:
            logging.warning(f"⚠️ Lỗi tại dòng {i}: {e}")

if __name__ == "__main__":
    run_bot()
