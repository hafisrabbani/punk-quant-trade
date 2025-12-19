import logging
import asyncio
import os
import tempfile
import ccxt.async_support as ccxt
import pandas as pd
from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import mplfinance as mpf

import analyzer
import matplotlib.pyplot as plt

# ======================
# BASIC SETUP
# ======================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

load_dotenv()
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

exchange = ccxt.binanceusdm({"enableRateLimit": True})

TEMP_DIR = tempfile.gettempdir()

# ======================
# GLOBAL DYNAMIC CONFIG
# ======================

CONFIDENCE_THRESHOLD = 60
SCAN_TOP_PAIRS = 15
SCAN_INTERVAL = 60
HTF = "1h"
LTF = "15m"
LEVERAGE_REF = 20

processed_candles = {}

# ======================
# HELPER
# ======================

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """
    Catch ALL unhandled exceptions from:
    - commands
    - jobs
    - callbacks
    - background tasks
    """

    error = context.error

    logging.exception("Unhandled exception", exc_info=error)

    msg = (
        "🚨 **GLOBAL BOT ERROR**\n"
        "------------------------------\n"
        f"❗ **Unhandled Exception**\n\n"
        f"🧾 Error:\n"
        f"`{str(error)[:3500]}`\n\n"
    )

    if update:
        msg += f"📩 Update Type: `{type(update).__name__}`\n"

    try:
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.error("Failed to send global error alert: %s", e)


def safe_symbol(symbol: str) -> str:
    """
    ALPACA/USDT:USDT -> ALPACA_USDT
    BTC/USDT:USDT -> BTC_USDT
    """
    base = symbol.split(":")[0]
    return base.replace("/", "_")

# ======================
# TELEGRAM COMMANDS
# ======================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Futures Market Analyzer Online**\n\n"
        "Signal berbasis:\n"
        "- HTF/LTF Trend\n"
        "- Market Structure\n"
        "- Liquidation Heatmap\n"
        "- RR Filter\n\n"
        "Gunakan /status untuk melihat config.",
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "⚙️ **Current Config**\n\n"
        f"• Confidence ≥ {CONFIDENCE_THRESHOLD}%\n"
        f"• Scan Top Pairs: {SCAN_TOP_PAIRS}\n"
        f"• Interval: {SCAN_INTERVAL}s\n"
        f"• HTF / LTF: {HTF} / {LTF}\n"
        f"• Leverage Ref: {LEVERAGE_REF}x"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def setconf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global CONFIDENCE_THRESHOLD
    try:
        CONFIDENCE_THRESHOLD = int(context.args[0])
        await update.message.reply_text(f"✅ Confidence set to {CONFIDENCE_THRESHOLD}%")
    except:
        await update.message.reply_text("⚠️ Usage: /setconf 70")

async def setpairs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SCAN_TOP_PAIRS
    try:
        SCAN_TOP_PAIRS = int(context.args[0])
        await update.message.reply_text(f"✅ Scan top {SCAN_TOP_PAIRS} pairs")
    except:
        await update.message.reply_text("⚠️ Usage: /setpairs 20")

async def settf_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global HTF, LTF
    try:
        HTF = context.args[0]
        LTF = context.args[1]
        await update.message.reply_text(f"✅ Timeframe set: {HTF} / {LTF}")
    except:
        await update.message.reply_text("⚠️ Usage: /settf 1h 15m")

# ======================
# MARKET DATA
# ======================

async def fetch_ohlcv(symbol, tf, limit=250):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=limit)
        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except:
        return pd.DataFrame()

async def fetch_liquidations(symbol):
    try:
        return await exchange.fetch_liquidations(symbol, limit=50)
    except:
        return []

# ======================
# CHART DRAWING
# ======================

def draw_chart(df, signal):
    df = df.tail(120).copy()
    df.set_index("timestamp", inplace=True)

    apds = [
        mpf.make_addplot(df["EMA_20"], color="blue"),
        mpf.make_addplot(df["EMA_50"], color="orange"),
        mpf.make_addplot(df["EMA_200"], color="purple"),
    ]

    hlines = [
        signal["price"],
        signal["sl"],
        signal["tp1"],
        signal["tp2"],
        signal["structure"]["high"],
        signal["structure"]["low"],
    ]

    colors = ["blue", "red", "green", "green", "purple", "purple"]

    title = f"{signal['symbol']} {signal['side']} | {signal.get('pattern','') or 'No Pattern'}"

    filename = f"{safe_symbol(signal['symbol'])}.png"
    path = os.path.join(TEMP_DIR, filename)

    mpf.plot(
        df,
        type="candle",
        style="charles",
        addplot=apds,
        hlines=dict(hlines=hlines, colors=colors, linestyle="--"),
        title=title,
        volume=False,
        figsize=(12, 6),
        savefig=dict(fname=path, dpi=120, bbox_inches="tight")
    )

    return path


# ======================
# SIGNAL SENDER
# ======================

async def send_signal(data, df_ltf, context):
    tp_msg = (
        f"TP1: {data['tp1']:.4f}\n"
        f"TP2: {data['tp2']:.4f}"
    )

    msg = (
        f"📊 **MARKET ANALYSIS SIGNAL**\n"
        f"------------------------------\n"
        f"🪙 **{data['symbol']}**\n"
        f"📈 Bias: **{data['side']}** ({LEVERAGE_REF}x ref)\n"
        f"📍 Entry: {data['price']:.4f}\n\n"
        f"{tp_msg}\n"
        f"🛑 SL: {data['sl']:.4f}\n\n"
        f"📊 Confidence: **{data['confidence']}%**\n"
        f"🔥 Liquidation: {data['liquidation']['bias']} ({data['liquidation']['score']}%)\n\n"
        f"🧠 Reasons:\n- " + "\n- ".join(data["reasons"]) +
        "\n\n⚠️ *Analysis only, not financial advice*"
    )

    chart_path = draw_chart(df_ltf, data)

    if not os.path.exists(chart_path):
        print("❌ Chart not created:", chart_path)
        return

    with open(chart_path, "rb") as img:
        await context.bot.send_photo(
            chat_id=CHAT_ID,
            photo=InputFile(img),
            caption=msg,
            parse_mode="Markdown"
        )

# ======================
# SCANNER LOOP
# ======================

async def market_scanner(context: ContextTypes.DEFAULT_TYPE):
    global processed_candles

    try:
        tickers = await exchange.fetch_tickers()

        pairs = sorted(
            [t for t in tickers.values() if t["symbol"].endswith("USDT")],
            key=lambda x: x.get("quoteVolume", 0),
            reverse=True
        )[:SCAN_TOP_PAIRS]

        print(f"\nScanning {len(pairs)} pairs...")
        print("-" * 40)
        for i, t in enumerate(pairs, start=1):
            print(f"{i:02d}. {t['symbol']} | Vol: {t.get('quoteVolume',0):,.0f}")

        for t in pairs:
            symbol = t["symbol"]

            try:
                df_htf = await fetch_ohlcv(symbol, HTF)
                df_ltf = await fetch_ohlcv(symbol, LTF)
                liqs = await fetch_liquidations(symbol)

                if df_htf.empty or df_ltf.empty:
                    continue

                df_htf = analyzer.calculate_indicators(df_htf)
                df_ltf = analyzer.calculate_indicators(df_ltf)

                res = analyzer.get_signal_score(
                    symbol,
                    df_htf,
                    df_ltf,
                    liquidation_data=liqs
                )

                if not res or res["confidence"] < CONFIDENCE_THRESHOLD:
                    continue

                last_ts = processed_candles.get(symbol)
                if last_ts == res["timestamp"]:
                    continue

                processed_candles[symbol] = res["timestamp"]
                await send_signal(res, df_ltf, context)

            except Exception as e:
                print(f"{symbol} error:", e)

            await asyncio.sleep(0.4)

    except Exception as e:
        print("Scanner Error:", e)

# ======================
# MAIN
# ======================

if __name__ == "__main__":
    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("setconf", setconf_command))
    app.add_handler(CommandHandler("setpairs", setpairs_command))
    app.add_handler(CommandHandler("settf", settf_command))

    app.add_error_handler(global_error_handler)
    
    app.job_queue.run_repeating(
        market_scanner,
        interval=SCAN_INTERVAL,
        first=10
    )

    print("🚀 Futures Analyzer Bot Running (FULL VERSION)")
    app.run_polling()
