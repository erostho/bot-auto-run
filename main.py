import gspread
import pandas as pd
from datetime import datetime, timedelta
from oauth2client.service_account import ServiceAccountCredentials
from gspread_dataframe import set_with_dataframe

# ==== CẤU HÌNH GOOGLE SHEET ====
SHEET_NAME = "OKX_GRID_ASSIST"
SHEET_DATA = "DATA"
SHEET_DATA_12H = "DATA_12H"
SHEET_HISTORY = "DATA_HISTORY"

# ==== KẾT NỐI GOOGLE SHEET ====
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
client = gspread.authorize(creds)
sheet = client.open(SHEET_NAME)

now = datetime.utcnow() + timedelta(hours=7)
now_str = now.strftime("%d/%m %H:%M")
today_str = now.strftime("%Y-%m-%d")

# ==== GIẢ LẬP PHÂN TÍCH COIN (bạn thay bằng logic thật nếu cần) ====
df_result = pd.DataFrame([
    ["BTCUSDT", "Tăng mạnh", "⭐⭐⭐⭐", now_str, "LONG", 30, "SL -3%, TP +5%", today_str],
    ["ETHUSDT", "Giảm mạnh", "⭐⭐⭐⭐", now_str, "SHORT", 24, "SL -3%, TP +4%", today_str],
], columns=["Coin", "Xu hướng", "Mức Ưu Tiên", "Thời gian", "Gợi ý", "Số lưới BOT", "SL/TP", "Ngày"])

# ==== CẬP NHẬT SHEET HISTORY (luôn thêm dòng mới) ====
try:
    ws_hist = sheet.worksheet(SHEET_HISTORY)
except:
    ws_hist = sheet.add_worksheet(title=SHEET_HISTORY, rows="2000", cols="20")
    ws_hist.append_row(df_result.columns.tolist())

for _, row in df_result.iterrows():
    ws_hist.append_row(row.tolist())

# ==== CẬP NHẬT SHEET DATA (ghi tiếp, tăng TẦN SUẤT nếu trùng coin) ====
try:
    ws_data = sheet.worksheet(SHEET_DATA)
except:
    ws_data = sheet.add_worksheet(title=SHEET_DATA, rows="1000", cols="20")
    ws_data.append_row(df_result.columns.tolist() + ["TẦN SUẤT"])

old = pd.DataFrame(ws_data.get_all_records())
new = df_result.copy()
new["TẦN SUẤT"] = 1

if not old.empty:
    for i, row in new.iterrows():
        coin = row["Coin"]
        match = old[old["Coin"] == coin]
        if not match.empty:
            idx = match.index[0]
            new.at[i, "TẦN SUẤT"] = int(match.iloc[0]["TẦN SUẤT"]) + 1

    df_final = pd.concat([old[~old["Coin"].isin(new["Coin"])], new], ignore_index=True)
else:
    df_final = new

ws_data.clear()
set_with_dataframe(ws_data, df_final)

print(f"✅ Đã cập nhật Sheet lúc {now_str}")