import os
import ccxt
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from tradingview_ta import TA_Handler, Interval, Exchange

# --- Kết nối Google Sheet ---
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open("OKX_BOT_FUTURE").worksheet("DATA_SPOT")
rows = sheet.get_all_values()

# --- Kết nối OKX SPOT ---
exchange = ccxt.okx({
    'apiKey': os.environ.get("OKX_API_KEY"),
    'secret': os.environ.get("OKX_API_SECRET"),
    'password': os.environ.get("OKX_API_PASSPHRASE"),
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# --- Duyệt danh sách coin ---
for idx, row in enumerate(rows[1:], start=2):  # Bỏ header
    coin_raw = row[0].strip()  # PEPE-USDT
    coin = coin_raw.replace("-", "/")          # PEPE/USDT
    coin_tv = coin_raw.replace("-", "")        # PEPEUSDT
    signal = row[1].strip().upper()
    da_mua = row[6].strip() if len(row) > 6 else ""
    gia_mua = float(row[7]) if len(row) > 7 and row[7] else None
    da_ban = row[8].strip() if len(row) > 8 else ""

    # ------------------ MUA COIN ------------------
    if signal == "MUA MẠNH" and da_mua != "✅":
        try:
            # ✅ Xác nhận tín hiệu từ TradingView
            tv = TA_Handler(
                symbol=coin_tv,
                screener="crypto",
                exchange="OKX",
                interval=Interval.INTERVAL_1_HOUR
            )
            analysis = tv.get_analysis()
            recommendation = analysis.summary["RECOMMENDATION"]

            if recommendation not in ["BUY", "STRONG_BUY"]:
                print(f"❌ {coin} không khớp tín hiệu TV ({recommendation})")
                continue

            print(f"✅ TV xác nhận: {coin} ({recommendation})")

            # ✅ Tính số lượng coin = 10 USDT
            ticker = exchange.fetch_ticker(coin)
            price = ticker['last']
            usdt_amount = 10
            amount = round(usdt_amount / price, 5)

            # ✅ Đặt lệnh mua
            order = exchange.create_market_buy_order(coin, amount)
            print(f"✅ Đã mua {amount} {coin} với giá ~{price}")

            # ✅ Ghi lại Google Sheet
            sheet.update_cell(idx, 7, "✅")           # G - Đã Mua
            sheet.update_cell(idx, 8, str(price))     # H - Giá Mua

        except Exception as e:
            print(f"❌ Lỗi mua {coin}: {e}")

    # ------------------ BÁN COIN ------------------
    elif da_mua == "✅" and da_ban != "✅" and gia_mua:
        try:
            ticker = exchange.fetch_ticker(coin)
            current_price = ticker['last']
            target_price = gia_mua * 1.1  # +10%

            if current_price < target_price:
                continue  # Chưa đạt TP

            # ✅ Kiểm tra số dư
            balance = exchange.fetch_balance()
            coin_symbol = coin.split("/")[0]
            amount = balance['free'].get(coin_symbol, 0)

            if amount <= 0:
                print(f"⚠️ Không còn {coin_symbol} để bán.")
                continue

            # ✅ Đặt lệnh bán
            order = exchange.create_market_sell_order(coin, amount)
            print(f"💰 Đã BÁN {amount} {coin} với giá ~{current_price} (+10%)")

            # ✅ Ghi lại Google Sheet
            sheet.update_cell(idx, 9, "✅")              # I - Đã Bán
            sheet.update_cell(idx, 10, str(current_price))  # J - Giá Bán

        except Exception as e:
            print(f"❌ Lỗi bán {coin}: {e}")
