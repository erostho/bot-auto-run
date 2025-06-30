# OKX GRID BOT ASSISTANT (Gộp Đa Khung + 12H)

# ======================================
# 1. CÀI ĐẶT THƯ VIỆN
# ======================================
import pandas as pd
import requests
from datetime import datetime, timedelta
import numpy as np
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread_dataframe import set_with_dataframe
from google.colab import drive
drive.mount('/content/drive')
# ======================================
# 2. KẾT NỐI GOOGLE SHEET
# ======================================
def connect_sheet(sheet_name, worksheet_name):
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("/content/drive/MyDrive/SpotBot/credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open(sheet_name)
    try:
        worksheet = sheet.worksheet(worksheet_name)
    except:
        worksheet = sheet.add_worksheet(title=worksheet_name, rows="1000", cols="20")
    worksheet.clear()
    return worksheet

# ======================================
# 3. LẤY DANH SÁCH COIN TỪ OKX
# ======================================
def get_okx_symbols():
    url = "https://www.okx.com/api/v5/public/instruments?instType=SPOT"
    data = requests.get(url).json()
    return [item["instId"] for item in data["data"] if item["instId"].endswith("USDT")]

# ======================================
# 4. LẤY DỮ LIỆU NẾN
# ======================================
def get_okx_ohlcv(symbol, timeframe):
    try:
        url = f"https://www.okx.com/api/v5/market/candles?instId={symbol}&bar={timeframe}&limit=200"
        data = requests.get(url).json()
        df = pd.DataFrame(data["data"], columns=["ts", "o", "h", "l", "c", "vol", "volCcy", "volCcyQuote", "confirm"])
        df["ts"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms")
        df = df.astype({"o": float, "h": float, "l": float, "c": float, "vol": float})
        df = df.sort_values("ts").reset_index(drop=True)
        return df
    except:
        return None

# ======================================
# 5. PHÂN TÍCH 1 KHUNG (RSI/EMA/Volume)
# ======================================
def analyze_single_timeframe(df):
    if df is None or df.empty or len(df) < 50:
        return 0
    close = df["c"]
    volume = df["vol"]
    ema20 = close.ewm(span=20).mean()
    ema50 = close.ewm(span=50).mean()
    rsi = 100 - (100 / (1 + close.diff().clip(lower=0).rolling(14).mean() /
                        close.diff().clip(upper=0).abs().rolling(14).mean()))
    gap = abs(ema20.iloc[-1] - ema50.iloc[-1]) / ema50.iloc[-1]
    vol_ratio = volume.iloc[-1] / volume.rolling(20).mean().iloc[-1]

    if rsi.iloc[-1] > 60 and ema20.iloc[-1] > ema50.iloc[-1] and gap > 0.01 and vol_ratio > 1.2:
        return 2
    elif rsi.iloc[-1] > 50 and ema20.iloc[-1] > ema50.iloc[-1] and gap > 0.005:
        return 1
    elif rsi.iloc[-1] < 40 and ema20.iloc[-1] < ema50.iloc[-1] and gap > 0.01 and vol_ratio > 1.2:
        return -2
    elif rsi.iloc[-1] < 50 and ema20.iloc[-1] < ema50.iloc[-1] and gap > 0.005:
        return -1
    return 0

# ======================================
# 6. PHÂN TÍCH ĐA KHUNG (1H, 4H, 1D, 1W)
# ======================================
def analyze_multiframe(symbol, df_1h, df_4h, df_1d, df_1w):
    timeframes = {"1H": df_1h, "4H": df_4h, "1D": df_1d, "1W": df_1w}
    total_score, strong_up, strong_down, used_frames = 0, 0, 0, 0

    for tf, df in timeframes.items():
        if df is not None and len(df) >= 50:
            score = analyze_single_timeframe(df)
            total_score += score
            used_frames += 1
            if score == 2: strong_up += 1
            if score == -2: strong_down += 1

    if used_frames == 0:
        return "Không rõ", "", ""
    if total_score >= 6 and strong_up >= 2:
        return "Tăng mạnh","5*", "LONG"
    elif total_score >= 4 and strong_up >= 1:
        return "Tăng nhẹ", "3*", "LONG"
    elif total_score <= -6 and strong_down >= 2:
        return "Giảm mạnh", "5*", "SHORT"
    elif total_score <= -4 and strong_down >= 1:
        return "Giảm nhẹ", "3*", "SHORT"
    else:
        return "Không rõ", "", ""

# ======================================
# 7. PHÂN TÍCH 12H (1H + 4H)
# ======================================
def analyze_intraday(symbol, df_1h, df_4h):
    scores = [analyze_single_timeframe(df_1h), analyze_single_timeframe(df_4h)]
    total = sum(scores)
    if total >= 3:
        return "Tăng mạnh", "5*", "LONG"
    elif total == 2:
        return "Tăng nhẹ", "3*", "LONG"
    elif total <= -3:
        return "Giảm mạnh", "5*", "SHORT"
    elif total == -2:
        return "Giảm nhẹ", "3*", "SHORT"
    else:
        return "Không rõ", "", ""

# ======================================
# 8. PHÂN TÍCH VÀ GHI 2 SHEET
# ======================================
def run_full_analysis():
    sheet_name = "OKX_GRID_ASSIST"
    symbols = get_okx_symbols()
    now = datetime.utcnow() + timedelta(hours=7)
    now_str = now.strftime("%d/%m %H:%M")
    today_str = now.strftime("%Y-%m-%d")

    df_da_khung = []
    df_12h = []
    df_history = []

    for symbol in symbols:
        try:
            df_1h = get_okx_ohlcv(symbol, "1H")
            df_4h = get_okx_ohlcv(symbol, "4H")
            df_1d = get_okx_ohlcv(symbol, "1D")
            df_1w = get_okx_ohlcv(symbol, "1W")

            xu_huong, star, goi_y = analyze_multiframe(symbol, df_1h, df_4h, df_1d, df_1w)
            if xu_huong != "Không rõ":
                entry = [symbol, xu_huong, star, now_str, goi_y, 25 if goi_y == "SHORT" else 30, "SL -3%, TP +8%", today_str]
                df_da_khung.append(entry)
                df_history.append(["DATA"] + entry)

            xu_huong2, star2, goi_y2 = analyze_intraday(symbol, df_1h, df_4h)
            if xu_huong2 != "Không rõ":
                entry2 = [symbol, xu_huong2, star2, now_str, goi_y2, 25, "SL -3%, TP +4%", today_str]
                df_12h.append(entry2)
                df_history.append(["DATA_12H"] + entry2)

        except Exception as e:
            print(f"❌ Lỗi {symbol}: {e}")

    # KẾT NỐI SHEET
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("/content/drive/MyDrive/SpotBot/credentials.json", scope)
    client = gspread.authorize(creds)
    sheet = client.open(sheet_name)

    # ==== XỬ LÝ DATA_HISTORY ====
    try:
        ws_history = sheet.worksheet("DATA_HISTORY")
    except:
        ws_history = sheet.add_worksheet(title="DATA_HISTORY", rows="1000", cols="20")
        ws_history.append_row(["Loại", "Coin", "Xu hướng", "Mức Ưu Tiên", "Thời gian", "Gợi ý", "Số lưới BOT", "SL/TP", "Ngày"])

    df_hist = pd.DataFrame(df_history, columns=["Loại", "Coin", "Xu hướng", "Mức Ưu Tiên", "Thời gian", "Gợi ý", "Số lưới BOT", "SL/TP", "Ngày"])
    existing_data = pd.DataFrame(ws_history.get_all_values()[1:], columns=ws_history.row_values(1))
    combined = pd.concat([existing_data, df_hist], ignore_index=True)
    combined["Ngày"] = pd.to_datetime(combined["Ngày"])
    cutoff = now - timedelta(days=7)
    combined = combined[combined["Ngày"] >= cutoff]
    combined["Ngày"] = combined["Ngày"].dt.strftime("%Y-%m-%d")
    ws_history.clear()
    set_with_dataframe(ws_history, combined)

    # ==== GHI DATA và DATA_12H có TẦN SUẤT ====
    def update_frequency_sheet(ws_name, new_data):
        try:
            ws = sheet.worksheet(ws_name)
        except:
            ws = sheet.add_worksheet(title=ws_name, rows="1000", cols="20")
            ws.append_row(["Coin", "Xu hướng", "Mức Ưu Tiên", "Thời gian", "Gợi ý", "Số lưới BOT", "SL/TP", "TẦN SUẤT", "Ngày"])

        rows = ws.get_all_values()
        if len(rows) <= 1:
            old_df = pd.DataFrame(columns=["Coin", "Xu hướng", "Mức Ưu Tiên", "Thời gian", "Gợi ý", "Số lưới BOT", "SL/TP", "TẦN SUẤT", "Ngày"])
        else:
            old_df = pd.DataFrame(rows[1:], columns=rows[0])

        for entry in new_data:
            coin = entry[0]
            matched = old_df["Coin"] == coin
            if matched.any():
                idx = old_df[matched].index[0]
                try:
                    freq = int(old_df.loc[idx, "TẦN SUẤT"])
                except:
                    freq = 1  # Nếu lỗi (vd cột ghi sai), bắt đầu lại từ 1

                old_df.loc[idx] = entry + [freq + 1]
            else:
            # Chắc chắn dòng mới có đầy đủ 8 cột
                new_row = pd.DataFrame([entry + [1]], columns=["Coin", "Xu hướng", "Mức Ưu Tiên", "Thời gian", "Gợi ý", "Số lưới BOT", "SL/TP", "TẦN SUẤT", "Ngày"])
                old_df = pd.concat([old_df, new_row], ignore_index=True)

        ws.clear()
        set_with_dataframe(ws, old_df)

    update_frequency_sheet("DATA", df_da_khung)
    update_frequency_sheet("DATA_12H", df_12h)

    print(f"✅ Ghi {len(df_da_khung)} coin vào DATA | {len(df_12h)} coin vào DATA_12H | Tổng cộng {len(df_history)} dòng vào DATA_HISTORY")

run_full_analysis()
