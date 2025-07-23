import os
import ccxt
import gspread
import pandas as pd
from datetime import datetime
from tradingview_ta import TA_Handler, Interval
from oauth2client.service_account import ServiceAccountCredentials

# --- Load biến môi trường ---
SPREADSHEET_URL = os.environ.get("SPREADSHEET_URL")  # Public sheet
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE")

# --- Kết nối OKX SPOT ---
exchange = ccxt.okx({
    'apiKey': OKX_API_KEY,
    'secret': OKX_API_SECRET,
    'password': OKX_API_PASSPHRASE,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})

# --- Kết nối Google Sheet PUBLIC ---
gc = gspread.Client(None)
sheet = gc.open_by_url(SPREADSHEET_URL).worksheet("DATA_SPOT")
rows = sheet.get_all_values()
df = pd.DataFrame(rows[1:], columns=rows[0])  # Dòng 1 là header

# --- Duyệt từng dòng coin ---
for idx, row in df.iterrows():
    coin_raw = row["Coin"].strip()
    coin = coin_raw.replace("-", "/")
    coin_tv = coin_raw.replace("-", "")
    signal = row["Tín hiệu"].strip().upper()
    ngay_str = row.get("Ngày", "").strip()
    tan_suat = int(row.get("Tần suất", "60").strip())
    da_mua = row.get("Đã Mua", "").strip()
    da_ban = row.get("Đã Bán", "").strip()
    gia_mua = float(row.get("Giá Mua") or 0)

    sheet_row = idx + 2  # Gspread là 1-index, bỏ header

    # --- Điều kiện MUA ---
    if signal == "MUA MẠNH" and da_mua != "✅":
        try:
            if ngay_str:
                ngay = datetime.strptime(ngay_str, "%Y-%m-%d %H:%M:%S")
                diff_minutes = (datetime.now() - ngay).total_seconds() / 60
                if diff_minutes > tan_suat:
                    print(f"⏱️ Bỏ qua {coin}: quá {int(diff_minutes)} phút")
                    continue

            # Check tín hiệu TradingView
            tv = TA_Handler(
                symbol=coin_tv,
                screener="crypto",
                exchange="OKX",
                interval=Interval.INTERVAL_1_HOUR
            )
            analysis = tv.get_analysis()
            if analysis.summary["RECOMMENDATION"] not in ["BUY", "STRONG_BUY"]:
                print(f"❌ {coin} không đạt tín hiệu TV")
                continue

            ticker = exchange.fetch_ticker(coin)
            price = ticker['last']
            amount = round(10 / price, 5)

            order = exchange.create_market_buy_order(coin, amount)
            print(f"✅ Đã mua {coin} số lượng {amount} tại giá {price}")

            sheet.update_cell(sheet_row, 6, "✅")            # Đã Mua
            sheet.update_cell(sheet_row, 7, str(price))      # Giá Mua

        except Exception as e:
            print(f"❌ Lỗi mua {coin}: {e}")

    # --- Điều kiện BÁN ---
    elif da_mua == "✅" and da_ban != "✅" and gia_mua > 0:
        try:
            ticker = exchange.fetch_ticker(coin)
            current_price = ticker['last']
            if current_price < gia_mua * 1.1:
                continue

            balance = exchange.fetch_balance()
            coin_symbol = coin.split("/")[0]
            amount = balance['free'].get(coin_symbol, 0)
            if amount <= 0:
                continue

            order = exchange.create_market_sell_order(coin, amount)
            print(f"💰 Đã BÁN {coin} tại giá {current_price}")

            sheet.update_cell(sheet_row, 8, "✅")              # Đã Bán
            sheet.update_cell(sheet_row, 9, str(current_price))  # Giá Bán

        except Exception as e:
            print(f"❌ Lỗi bán {coin}: {e}")
