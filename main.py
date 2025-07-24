import ccxt
import requests
import pandas as pd
import os
import time

# ✅ Cấu hình OKX
exchange = ccxt.okx({
    "apiKey": os.getenv("OKX_API_KEY"),
    "secret": os.getenv("OKX_API_SECRET"),
    "password": os.getenv("OKX_API_PASS"),
    "enableRateLimit": True,
    "options": {"defaultType": "spot"},
})

# ✅ Đọc Google Sheet
sheet_url = os.getenv("SPREADSHEET_URL")  # phải là link public
sheet_csv_url = sheet_url.replace("/edit#gid=", "/export?format=csv&gid=")
df = pd.read_csv(sheet_csv_url)

# ✅ Định nghĩa hàm lấy tín hiệu TradingView
def check_tradingview_signal(symbol: str) -> str:
    try:
        url = "https://scanner.tradingview.com/crypto/scan"
        payload = {
            "symbols": {"tickers": [f"BINANCE:{symbol}"], "query": {"types": []}},
            "columns": ["recommendation"]
        }
        resp = requests.post(url, json=payload)
        data = resp.json()
        signal = data["data"][0]["d"][0]
        return signal.upper()
    except Exception as e:
        print(f"❌ Không lấy được tín hiệu TV {symbol}: {e}")
        return ""

# ✅ Duyệt từng dòng
for i, row in df.iterrows():
    try:
        coin = str(row.get("Coin")).strip()
        gia_mua = float(row.get("Giá", 0))
        buy_status = str(row.get("Đã Mua", "")).strip().upper()
        sell_status = str(row.get("Giá Bán", "")).strip()
        symbol = f"{coin.upper()}/USDT"

        # Bỏ qua nếu đã mua rồi
        if not coin or buy_status == "RỒI" or not gia_mua:
            continue

        # ✅ Check có trong OKX SPOT không
        if symbol not in exchange.markets:
            print(f"❌ Không tìm thấy {symbol} trong OKX SPOT")
            continue

        # ✅ Lấy giá hiện tại
        try:
            price = exchange.fetch_ticker(symbol)['last']
        except Exception as e:
            print(f"⚠️ Không lấy được giá {symbol}: {e}")
            continue

        # ✅ Nếu giá > 5% so với giá mua → bỏ qua
        if price > gia_mua * 1.05:
            print(f"⚠️ Giá {symbol} cao hơn 5% so với giá mua gốc → KHÔNG MUA")
            continue

        # ✅ Check tín hiệu từ TradingView
        tv_symbol = symbol.replace("/", "")
        signal_tv = check_tradingview_signal(tv_symbol)
        print(f"[TV] Tín hiệu cho {tv_symbol} = {signal_tv}")

        if signal_tv not in ["BUY", "STRONG_BUY"]:
            print(f"❌ {symbol} bị loại do tín hiệu TV = {signal_tv}")
            continue

        # ✅ MUA nếu chưa mua
        usdt_amount = 10
        amount = round(usdt_amount / price, 6)
        try:
            order = exchange.create_market_buy_order(symbol, amount)
            print(f"✅ Đã MUA {symbol} {amount:.6f} giá ~{price:.4f}")

            # ✅ Cập nhật lại sheet
            df.at[i, "Đã Mua"] = "RỒI"
            df.at[i, "Giá Mua"] = price
        except Exception as e:
            print(f"❌ Lỗi MUA {symbol}: {e}")
            continue

    except Exception as e:
        print(f"⚠️ Lỗi tại dòng {i}: {e}")
        continue
# --- Xử lý BÁN nếu đã MUA ---
for i, row in df.iterrows():
    try:
        coin = str(row.get("Coin")).strip()
        buy_status = str(row.get("Đã Mua", "")).strip().upper()
        gia_mua = float(row.get("Giá Mua", 0))
        symbol = f"{coin.upper()}/USDT"

        # ✅ Bỏ qua nếu chưa mua hoặc không có giá mua
        if buy_status != "RỒI" or not gia_mua:
            continue

        # ✅ Kiểm tra symbol tồn tại
        if symbol not in exchange.markets:
            print(f"❌ Không tìm thấy {symbol} trong OKX SPOT để bán")
            continue

        # ✅ Lấy giá hiện tại
        try:
            current_price = exchange.fetch_ticker(symbol)['last']
        except Exception as e:
            print(f"⚠️ Không lấy được giá {symbol} để bán: {e}")
            continue

        # ✅ Nếu chưa đủ 10% lợi nhuận → bỏ qua
        if current_price < gia_mua * 1.1:
            continue

        # ✅ Kiểm tra số dư
        balance = exchange.fetch_balance()
        coin_code = coin.upper()
        amount = balance.get(coin_code, {}).get("free", 0)
        if amount <= 0:
            continue

        # ✅ Đặt lệnh bán
        try:
            order = exchange.create_market_sell_order(symbol, amount)
            print(f"💰 Đã BÁN {symbol} {amount:.6f} giá ~{current_price:.4f}")

            # ✅ Ghi lại giá bán
            df.at[i, "Giá Bán"] = current_price
        except Exception as e:
            print(f"❌ Lỗi bán {coin}: {e}")
            continue

    except Exception as e:
        print(f"⚠️ Lỗi khi xử lý bán dòng {i}: {e}")
        continue

# ✅ Ghi lại vào Google Sheet (nếu cần ghi ngược)
# Nếu chỉ chạy 1 chiều (không update sheet) thì bỏ đoạn ghi

print("✅ Bot SPOT OKX hoàn tất.")
