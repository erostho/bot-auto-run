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

            logger.info(f"🛒 Đang xét mua {symbol}...")

            # Bỏ qua nếu chưa có giá mua hoặc đã mua rồi
            if not gia_mua or da_mua == "ĐÃ MUA":
                logger.info(f"⏩ Bỏ qua {symbol} do {'đã mua' if da_mua == 'ĐÃ MUA' else 'thiếu giá'}")
                continue

            # Kiểm tra tín hiệu sheet
            if signal != "MUA MẠNH":
                logger.info(f"❌ {symbol} bị loại do tín hiệu Sheet = {signal}")
                continue

            # ✅ Tạo tv_symbol trực tiếp mà không cần normalize
            tv_symbol = f"BINANCE:{symbol.replace('-', '')}"

            url = "https://scanner.tradingview.com/crypto/scan"
            payload = {
                "symbols": {"tickers": [tv_symbol]},
                "columns": ["recommendation"]
            }

            logging.debug(f"📡 Gửi request TV cho {symbol} → {tv_symbol} với payload: {payload}")
            res = requests.post(url, json=payload, timeout=5)
            res.raise_for_status()

            data = res.json()
            logging.debug(f"📊 Phản hồi từ TradingView cho {tv_symbol}: {data}")

            if not data.get("data"):
                logger.warning(f"⚠️ Không nhận được tín hiệu từ TradingView cho {symbol}")
                continue

            recommendation = data["data"][0]["d"][0]
            logger.info(f"📈 Tín hiệu TradingView cho {symbol} = {recommendation}")

            if recommendation not in ["BUY", "STRONG_BUY"]:
                logger.info(f"❌ Loại {symbol} do tín hiệu TradingView = {recommendation}")
                continue

            # ✅ Nếu tới đây thì hợp lệ → tiến hành mua SPOT
            try:
                usdt_amount = 10  # số USDT muốn mua
                price = exchange.fetch_ticker(symbol)['last']
                amount = round(usdt_amount / price, 6)  # khối lượng coin muốn mua
            
                logger.info(f"💰 Đặt lệnh mua {amount} {symbol} với tổng {usdt_amount} USDT (giá {price})")
            
                order = exchange.create_market_buy_order(symbol, amount)
                logger.info(f"✅ Đã mua {symbol}: {order}")
            
                # Ghi log vào sheet hoặc cập nhật trạng thái “ĐÃ MUA” (nếu có xử lý thêm)
            except Exception as e:
                logger.error(f"❌ Lỗi khi mua {symbol}: {e}")
        except Exception as e:
            logger.error(f"❌ Lỗi khi xử lý dòng {i} - {row}: {e}")
