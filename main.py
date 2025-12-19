import logging
import asyncio
import os
import ccxt.async_support as ccxt
import pandas as pd
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

import database
import analyzer

# Setup Logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

# Load Env
load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# --- CONFIG GLOBAL ---
# Variabel ini bisa diubah via command Telegram
LEVERAGE = 20
MARGIN_PER_TRADE = 2.0  
CONFIDENCE_THRESHOLD = 50 # Default awal 50%

exchange = ccxt.binanceusdm({'enableRateLimit': True})
processed_candles = {}

# --- COMMAND HANDLERS ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Bot Futures Trading Aktif**\n\n"
        f"Saldo Awal: $10 (Simulasi)\n"
        f"Min Confidence: {CONFIDENCE_THRESHOLD}%\n\n"
        "Ketik /help untuk lihat perintah.",
        parse_mode='Markdown'
    )

async def set_threshold_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Mengubah threshold confidence.
    Cara pakai: /setconf 75
    """
    global CONFIDENCE_THRESHOLD
    
    # Validasi: Pastikan user memasukkan angka
    if not context.args:
        await update.message.reply_text("⚠️ Gunakan format: `/setconf <angka>`\nContoh: `/setconf 75`", parse_mode='Markdown')
        return

    try:
        new_val = int(context.args[0])
        
        # Validasi range 1-100
        if 1 <= new_val <= 100:
            CONFIDENCE_THRESHOLD = new_val
            await update.message.reply_text(f"✅ **Threshold Diupdate!**\nSekarang bot hanya akan entry jika Confidence >= **{new_val}%**", parse_mode='Markdown')
        else:
            await update.message.reply_text("⚠️ Masukkan angka antara 1 - 100.")
            
    except ValueError:
        await update.message.reply_text("⚠️ Error: Harap masukkan angka bulat.")

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    balance = await database.get_balance()
    total_trades, last_trades = await database.get_report_stats()
    
    msg = (
        f"📊 **BACKTEST / PAPER REPORT** 📊\n"
        f"---------------------------------\n"
        f"💰 Wallet Balance: **${balance:.2f}**\n"
        f"📜 Total Signal/Trade: {total_trades}\n"
        f"⚙️ Config Saat Ini:\n"
        f"   • Min Conf: **{CONFIDENCE_THRESHOLD}%**\n"
        f"   • Margin: ${MARGIN_PER_TRADE}\n"
        f"   • Lev: {LEVERAGE}x\n\n"
        f"**Last 5 Trades:**\n"
    )
    
    for t in last_trades:
        icon = "🟢" if t[1] == "LONG" else "🔴"
        msg += f"{icon} {t[0]} @ {t[2]:.4f} ({t[3]})\n"
        
    await update.message.reply_text(msg, parse_mode='Markdown')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **Daftar Perintah:**\n\n"
        "/start - Cek status bot\n"
        "/report - Laporan saldo & performa\n"
        "/setconf <angka> - Atur sensitivitas sinyal (Contoh: /setconf 80)\n"
        "/help - Bantuan"
    )

# --- TRADING LOGIC ---

async def execute_trade(data, context):
    # 1. Cek Saldo
    balance = await database.get_balance()
    if balance < MARGIN_PER_TRADE:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"⚠️ **Saldo tidak cukup!** (${balance:.2f})")

    # 2. Hitung Detail Posisi
    position_size_usd = MARGIN_PER_TRADE * LEVERAGE
    
    trade_data = {
        **data,
        'margin': MARGIN_PER_TRADE,
        'leverage': LEVERAGE,
        'size': position_size_usd
    }
    
    await database.open_trade(trade_data)
    new_balance = await database.get_balance()

    # 3. Format Pesan
    tp_msg = (
        f"1️⃣ {data['tp1']:.4f} (Fib 0.618)\n"
        f"2️⃣ {data['tp2']:.4f} (Fib 1.0)\n"
        f"3️⃣ {data['tp3']:.4f} (Fib 1.618)"
    )
    
    msg = (
        f"⚡ **SIGNAL EXECUTED** (Paper) ⚡\n"
        f"-------------------------------\n"
        f"🪙 **{data['symbol']}** | {data['side']} {LEVERAGE}x\n"
        f"📉 Entry: {data['price']}\n"
        f"💵 Margin: ${MARGIN_PER_TRADE} (Size: ${position_size_usd})\n"
        f"💰 Wallet Sisa: ${new_balance:.2f}\n\n"
        f"🎯 **Target Profit (Fibonacci):**\n{tp_msg}\n\n"
        f"🛑 **Stop Loss:**\n{data['sl']:.4f}\n\n"
        f"📊 Conf: {data['confidence']}% (Thres: {CONFIDENCE_THRESHOLD}%)\n"
        f"📝 Reasons: {', '.join(data['reasons'])}"
    )
    
    await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode='Markdown')

# --- SCANNER TASK ---

async def market_scanner(context: ContextTypes.DEFAULT_TYPE):
    """Fungsi ini berjalan di background setiap X detik"""
    
    # Akses variabel global agar update realtime tanpa restart
    global CONFIDENCE_THRESHOLD 
    
    try:
        # Ambil Top Volume Pairs
        tickers = await exchange.fetch_tickers()
        
        valid_tickers = [
            t for t in tickers.values() 
            if 'USDT' in t['symbol'] and 'USDC' not in t['symbol']
        ]

        sorted_tickers = sorted(
            valid_tickers,
            key=lambda x: x.get('quoteVolume', 0), 
            reverse=True
        )

        symbols = [t['symbol'] for t in sorted_tickers[:15]]

        for symbol in symbols:
            try:
                df_htf = await fetch_ohlcv(symbol, '1h')
                df_ltf = await fetch_ohlcv(symbol, '15m')
                
                df_htf = analyzer.calculate_indicators(df_htf)
                df_ltf = analyzer.calculate_indicators(df_ltf)
                
                res = analyzer.get_signal_score(symbol, df_htf, df_ltf)
                
                # MENGGUNAKAN VARIABEL GLOBAL CONFIDENCE_THRESHOLD
                if res and res['confidence'] >= CONFIDENCE_THRESHOLD:
                    last_time = processed_candles.get(symbol)
                    
                    if last_time != res['timestamp']:
                        processed_candles[symbol] = res['timestamp']
                        await execute_trade(res, context)
                        
            except Exception as e:
                # print(f"Error processing {symbol}: {e}")
                pass 
            
            await asyncio.sleep(0.5) 

    except Exception as e:
        print(f"Scanner Loop Error: {e}")

async def fetch_ohlcv(symbol, tf):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=250)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except:
        return pd.DataFrame()

# --- MAIN APP ---

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(database.init_db())
    
    application = ApplicationBuilder().token(TOKEN).build()
    
    # Tambahkan Command Handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("report", report_command))
    application.add_handler(CommandHandler("help", help_command))
    
    # REGISTER COMMAND BARU DI SINI
    application.add_handler(CommandHandler("setconf", set_threshold_command))
    
    job_queue = application.job_queue
    job_queue.run_repeating(market_scanner, interval=60, first=10)
    
    print("🚀 Bot Started with Dynamic Config...")
    application.run_polling()