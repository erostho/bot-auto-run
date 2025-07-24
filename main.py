import os
import ccxt
import pandas as pd
import logging
from datetime import datetime
from tradingview_ta import TA_Handler, Interval

# Logging setup
logging.basicConfig(
    level=logging.DEBUG,  # thay vì DEBUG/INFO
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# --- Load biến môi trường ---
SPREADSHEET_URL = os.environ.get("SPREADSHEET_URL")
OKX_API_KEY = os.environ.get("OKX_API_KEY")
OKX_API_SECRET = os.environ.get("OKX_API_SECRET")
OKX_API_PASSPHRASE = os.environ.get("OKX_API_PASSPHRASE")

# --- Kiểm tra biến môi trường ---
if not SPREADSHEET_URL or not OKX_API_KEY:
    print("❌ Thiếu biến môi trường")
    exit()

# --- Kết nối OKX SPOT ---
exchange = ccxt.okx({
    'apiKey': OKX_API_KEY,
    'secret': OKX_API_SECRET,
    'password': OKX_API_PASSPHRASE,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot'}
})
exchange.load_markets()
# --- Đọc Google Sheet public (CSV) ---
try:
    df = pd.read_csv(SPREADSHEET_URL)
except Exception as e:
    print(f"❌ Không thể đọc Sheet: {e}")
    exit()

# --- Hàm check tín hiệu từ TradingView ---
def check_tradingview_signal(symbol: str) -> str:
    try:
        # ⚠️ Ghép USDT nếu chưa có, không dùng dấu "/"
        tv_symbol = symbol.upper()
        if not tv_symbol.endswith("USDT"):
            tv_symbol += "USDT"

        print(f"🔍 [TV] Đang kiểm tra tín hiệu TradingView cho: {tv_symbol}")

        handler = TA_Handler(
            symbol=tv_symbol,
            screener="crypto",
            exchange="OKX",
            interval=Interval.INTERVAL_1_HOUR
        )

        result = handler.get_analysis()
        recommendation = result.summary.get("RECOMMENDATION", "")
        print(f"✅ [TV] Tín hiệu cho {tv_symbol} = {recommendation}")
        return recommendation

    except Exception as e:
        print(f"⚠️ [TV] Lỗi khi lấy tín hiệu cho {symbol} ({tv_symbol}): {e}")
        return ""

# --- Duyệt từng dòng coin ---
now = datetime.utcnow()

for i, row in df.iterrows():
    try:
        coin = str(row.get("Coin")).strip()
        signal = str(row.get("Tín hiệu")).strip().upper()
        buy_status = str(row.get("Đã mua", "")).strip().upper()
        date_str = str(row.get("Ngày", "")).strip()
        gia_mua = row.get("Giá Mua", None)

        if not coin or signal != "MUA MẠNH":
            continue
        if buy_status == "RỒI":
            continue

        # Kiểm tra thời gian tín hiệu còn trong 60 phút
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            if (now - dt).total_seconds() > 60 * 60:
                continue
        except:
            print(f"⚠️ Lỗi thời gian cho {coin}, bỏ qua")
            continue
        # ✅ Chuẩn hóa tên coin: bỏ đuôi -USDT nếu có, viết hoa
        coin = coin.upper().replace("-USDT", "").replace("/USDT", "")
        symbol_spot = f"{coin}/USDT"
        market = exchange.markets.get(symbol_spot)
        if not market:
            # Thử lại với định dạng PEPE-USDT nếu dạng / không có
            alt_symbol = symbol_spot.replace("/", "-")
            market = exchange.markets.get(alt_symbol)
            if market:
                print(f"🔁 Đổi qua symbol: {alt_symbol}")
                symbol_spot = alt_symbol
        
        if not market or market.get("spot") != True:
            print(f"⚠️ {symbol_spot} KHÔNG tìm thấy trong exchange.markets")
            # Gợi ý các symbol gần giống để debug
            suggestions = [s for s in exchange.markets.keys() if coin.upper() in s and "USDT" in s]
            print(f"👉 Gợi ý symbol gần giống: {suggestions}")
            continue
        
        # ✅ Nếu qua được thì là SPOT hợp lệ
        print(f"✅ {symbol_spot} là SPOT hợp lệ")

        # ✅ Check tín hiệu từ TradingView
        tv_signal = check_tradingview_signal(coin)
        if tv_signal not in ["BUY", "STRONG_BUY"]:
            print(f"⛔ {coin} bị loại do TV = {tv_signal}")
            continue

        # ✅ Lấy giá hiện tại
        try:
            price = exchange.fetch_ticker(symbol)['last']
        except:
            print(f"⚠️ Không lấy được giá {symbol}")
            continue

        # --- MUA nếu chưa mua ---
        print(f"🔍 Đang xét mua {symbol}...")
        
        if buy_status == "RỒI":
            print(f"⏭ Đã mua rồi {symbol}, bỏ qua")
            continue
        
        if symbol not in exchange.markets:
            print(f"⛔ {symbol} không tồn tại trên sàn OKX")
            continue
        
        try:
            price = exchange.fetch_ticker(symbol)['last']
            print(f"💰 Giá hiện tại của {symbol}: {price}")
        except Exception as e:
            print(f"❌ Không lấy được giá {symbol}: {e}")
            continue
        
        # Nếu có giới hạn giá trong sheet:
        if price > gia_mua * 1.05:
            print(f"⚠️ Giá {symbol} cao hơn 5% so với giá mua gốc → KHÔNG MUA")
            continue
        
        # Tín hiệu TradingView
        try:
            signal_tv = check_tradingview_signal(symbol.replace("/", ""))
            print(f"[TV] Tín hiệu TradingView của {symbol}: {signal_tv}")
        except Exception as e:
            print(f"❌ Lỗi lấy tín hiệu TradingView: {e}")
            continue
        
        if signal_tv not in ["BUY", "STRONG_BUY"]:
            print(f"🚫 {symbol} bị loại do tín hiệu TV = {signal_tv}")
            continue
        
        # Tính amount và đặt lệnh
        usdt_amount = 10
        amount = round(usdt_amount / price, 6)
        
        try:
            order = exchange.create_market_buy_order(symbol, amount)
            print(f"🚀 Đã MUA {symbol} {amount:.6f} giá ~{price:.4f}")
        except Exception as e:
            print(f"❌ Lỗi MUA {symbol}: {e}")
            continue

        # Tạo lệnh bán
        order = exchange.create_market_sell_order(symbol, amount)
        print(f"🍑 ĐÃ BÁN {symbol} {amount:.6f} giá ~{current_price:.4f}")

    except Exception as e:
        print(f"⚠️ Lỗi bán {coin}: {e}")

print("✅ Bot SPOT OKX hoàn tất.")
