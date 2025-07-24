import os
import ccxt
import pandas as pd
from datetime import datetime
from tradingview_ta import TA_Handler, Interval

# Logging setup
logging.basicConfig(
    level=logging.DEBUG,  # thay vì DEBUG/INFO
    format="%(asctime)s - %(levelname)s - %(message)s",
    stream=sys.stdout
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
        handler = TA_Handler(
            symbol=symbol.upper(),
            screener="crypto",
            exchange="OKX",
            interval=Interval.INTERVAL_1_HOUR
        )
        result = handler.get_analysis()
        return result.summary.get("RECOMMENDATION", "")
    except Exception as e:
        print(f"⚠️ Lỗi TV cho {symbol}: {e}")
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

        symbol_spot = coin.upper().replace("/", "-")
        market = exchange.markets.get(symbol_spot)
        
        if not market:
            print(f"⚠️ {symbol_spot} KHÔNG tìm thấy trong exchange.markets")
            
            # Gợi ý các symbol gần giống
            similar = [s for s in exchange.markets.keys() if coin.split("/")[0].upper() in s]
            print(f"🔍 Gợi ý symbol gần giống: {similar}")
            continue
        
        if not market.get("spot"):
            print(f"⚠️ {symbol_spot} TỒN TẠI nhưng KHÔNG PHẢI SPOT trên OKX")
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
        usdt_amount = 10
        amount = round(usdt_amount / price, 6)
        try:
            order = exchange.create_market_buy_order(symbol, amount)
            print(f"✅ Đã MUA {symbol} {amount:.6f} giá ~{price:.4f}")
        except Exception as e:
            print(f"❌ Lỗi MUA {symbol}: {e}")
            continue

    except Exception as e:
        print(f"⚠️ Lỗi tại dòng {i}: {e}")
        continue

# --- Lặp lại để xử lý BÁN ---
for i, row in df.iterrows():
    try:
        coin = str(row.get("Coin")).strip()
        gia_mua = float(row.get("Giá Mua", 0))
        buy_status = str(row.get("Đã mua", "")).strip().upper()
        sell_status = str(row.get("Giá Bán", "")).strip()

        if not coin or buy_status != "RỒI" or not gia_mua:
            continue

        symbol = f"{coin.upper()}/USDT"
        if symbol not in exchange.markets:
            continue

        current_price = exchange.fetch_ticker(symbol)['last']
        if current_price < gia_mua * 1.1:
            continue

        balance = exchange.fetch_balance()
        coin_code = coin.upper()
        amount = balance.get(coin_code, {}).get("free", 0)
        if amount <= 0:
            continue

        order = exchange.create_market_sell_order(symbol, amount)
        print(f"💰 Đã BÁN {symbol} {amount:.6f} giá ~{current_price:.4f}")

    except Exception as e:
        print(f"⚠️ Lỗi bán {coin}: {e}")
        continue

print("✅ Bot SPOT OKX hoàn tất.")
