import logging
import asyncio
import os
import tempfile
import time
import ccxt.async_support as ccxt
import pandas as pd
from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler
import mplfinance as mpf

import analyzer

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

CONFIDENCE_THRESHOLD = 75       # Raised to reduce low-quality signals
SCAN_TOP_PAIRS = 15
SCAN_INTERVAL = 300             # 5min, aligned with LTF candle period
HTF = "1h"
LTF = "15m"
MACRO_TF = "4h"                 # Macro timeframe for extra confirmation
LEVERAGE_REF = 20
SIGNAL_COOLDOWN = 4 * 3600      # 4 hours cooldown per symbol (in seconds)

processed_candles = {}
signal_cooldowns = {}           # {symbol: last_signal_timestamp}

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


def is_on_cooldown(symbol: str) -> bool:
    """Check if a symbol is still in cooldown period."""
    last_time = signal_cooldowns.get(symbol, 0)
    return (time.time() - last_time) < SIGNAL_COOLDOWN


def set_cooldown(symbol: str):
    """Set cooldown timestamp for a symbol."""
    signal_cooldowns[symbol] = time.time()

# ======================
# TELEGRAM COMMANDS
# ======================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 **Futures Momentum Analyzer v3**\n\n"
        "🚦 **Hard Gates:**\n"
        "- ADX Trending Market\n"
        "- ATR Volatility Expansion\n"
        "- Volume Spike Confirmation\n"
        "- HTF/LTF Trend Alignment\n"
        "- BTC Correlation Filter\n\n"
        "📊 **Scoring Algorithms:**\n"
        "- EMA Stack Alignment\n"
        "- RSI + MACD Momentum\n"
        "- Bollinger Band Squeeze→Expansion\n"
        "- Consecutive Momentum Candles\n"
        "- 4H Timeframe Confirmation\n"
        "- RSI Divergence Detection\n"
        "- Liquidation Heatmap\n"
        "- Structure Breakout\n"
        "- Fibonacci TP/SL\n\n"
        "Gunakan /status untuk melihat config.",
        parse_mode="Markdown"
    )

async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_cooldowns = sum(1 for s in signal_cooldowns if is_on_cooldown(s))
    msg = (
        "⚙️ **Current Config v3**\n\n"
        f"• Confidence ≥ {CONFIDENCE_THRESHOLD}%\n"
        f"• Scan Top Pairs: {SCAN_TOP_PAIRS}\n"
        f"• Interval: {SCAN_INTERVAL}s\n"
        f"• HTF / LTF / Macro: {HTF} / {LTF} / {MACRO_TF}\n"
        f"• Leverage Ref: {LEVERAGE_REF}x\n"
        f"• Cooldown: {SIGNAL_COOLDOWN // 3600}h per symbol\n"
        f"• Symbols on cooldown: {active_cooldowns}\n"
        f"• Filters: 5 gates + 11 scoring algos"
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

async def setcooldown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global SIGNAL_COOLDOWN
    try:
        hours = float(context.args[0])
        SIGNAL_COOLDOWN = int(hours * 3600)
        await update.message.reply_text(f"✅ Cooldown set to {hours}h per symbol")
    except:
        await update.message.reply_text("⚠️ Usage: /setcooldown 4")

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
        mpf.make_addplot(df["EMA_20"], color="cyan", width=1),
        mpf.make_addplot(df["EMA_50"], color="orange", width=1),
        mpf.make_addplot(df["EMA_200"], color="purple", width=1.5),
    ]

    # Add Bollinger Bands
    if "BB_UPPER" in df.columns and "BB_LOWER" in df.columns:
        apds.append(mpf.make_addplot(df["BB_UPPER"], color="gray", width=0.7, linestyle="--"))
        apds.append(mpf.make_addplot(df["BB_LOWER"], color="gray", width=0.7, linestyle="--"))

    # Add MACD histogram as subplot
    if "MACD_HIST" in df.columns:
        macd_colors = ['lime' if v >= 0 else 'red' for v in df["MACD_HIST"]]
        apds.append(mpf.make_addplot(df["MACD_HIST"], type='bar', panel=2, color=macd_colors, ylabel='MACD'))

    # Add RSI as subplot
    if "RSI" in df.columns:
        apds.append(mpf.make_addplot(df["RSI"], panel=3, color="blue", ylabel='RSI', width=1))
        # RSI reference lines (overbought/oversold)
        rsi_50 = pd.Series([50] * len(df), index=df.index)
        apds.append(mpf.make_addplot(rsi_50, panel=3, color="gray", width=0.5, linestyle="--"))

    hlines = [
        signal["price"],
        signal["sl"],
        signal["tp1"],
        signal["tp2"],
        signal["structure"]["high"],
        signal["structure"]["low"],
    ]

    colors = ["blue", "red", "green", "green", "purple", "purple"]

    tp_method = signal.get("tp_method", "ATR")
    title = f"{signal['symbol']} {signal['side']} | Conf: {signal['confidence']}% | TP: {tp_method}"

    filename = f"{safe_symbol(signal['symbol'])}.png"
    path = os.path.join(TEMP_DIR, filename)

    mpf.plot(
        df,
        type="candle",
        style="charles",
        addplot=apds,
        hlines=dict(hlines=hlines, colors=colors, linestyle="--"),
        title=title,
        volume=True,
        figsize=(16, 10),
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

    # Gates summary
    gates_str = "\n".join(f"  ✅ {g}" for g in data.get("gates_passed", []))

    # Momentum details
    mom = data.get("momentum", {})
    mom_str = f"RSI: {mom.get('rsi', 'N/A')} | MACD: {'📈' if mom.get('macd_expanding') else '📉'}"

    # Advanced algorithm summary
    adv = data.get("advanced", {})
    adv_parts = []
    if adv.get("bb_squeeze_breakout"):
        adv_parts.append("🔥 BB Squeeze Breakout")
    elif adv.get("bb_expanding"):
        adv_parts.append("📊 BB Expanding")
    if adv.get("htf4_confirmed"):
        adv_parts.append("✅ 4H Confirmed")
    if adv.get("rsi_divergence"):
        adv_parts.append(f"📐 {adv['rsi_divergence']}")
    adv_str = " | ".join(adv_parts) if adv_parts else "—"

    tp_method = data.get("tp_method", "ATR")
    rr = data.get("rr", 0)

    msg = (
        f"📊 **MOMENTUM SIGNAL v3**\n"
        f"==============================\n"
        f"🪙 **{data['symbol']}**\n"
        f"📈 Bias: **{data['side']}** ({LEVERAGE_REF}x ref)\n"
        f"📍 Entry: {data['price']:.4f}\n\n"
        f"{tp_msg}\n"
        f"🛑 SL: {data['sl']:.4f}\n"
        f"📐 RR: {rr} [{tp_method}]\n\n"
        f"📊 Confidence: **{data['confidence']}%**\n"
        f"📈 Momentum: {mom_str}\n"
        f"🔥 Liquidation: {data['liquidation']['bias']} ({data['liquidation']['score']}%)\n"
        f"🧪 Advanced: {adv_str}\n\n"
        f"🚦 **Gates Passed:**\n{gates_str}\n\n"
        f"🧠 **Reasons:**\n- " + "\n- ".join(data["reasons"]) +
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

        print(f"\n{'='*60}")
        print(f"🔍 Scanning {len(pairs)} pairs (threshold={CONFIDENCE_THRESHOLD}%, v3)")
        print(f"{'='*60}")
        for i, t in enumerate(pairs, start=1):
            print(f"  {i:02d}. {t['symbol']} | Vol: {t.get('quoteVolume',0):,.0f}")

        # ---- Fetch BTC data once for correlation filter ----
        df_btc = await fetch_ohlcv("BTC/USDT:USDT", HTF)
        if not df_btc.empty:
            df_btc = analyzer.calculate_indicators(df_btc)
            print(f"  📊 BTC/USDT loaded for correlation filter")
        else:
            df_btc = None
            print(f"  ⚠️ BTC/USDT data unavailable, correlation filter disabled")

        signals_found = 0
        signals_filtered = 0

        for t in pairs:
            symbol = t["symbol"]

            # Check per-symbol cooldown
            if is_on_cooldown(symbol):
                logging.info(f"  {symbol}: SKIP — cooldown active")
                signals_filtered += 1
                continue

            try:
                df_htf = await fetch_ohlcv(symbol, HTF)
                df_ltf = await fetch_ohlcv(symbol, LTF)
                liqs = await fetch_liquidations(symbol)

                # Fetch 4H data for macro confirmation
                df_4h = await fetch_ohlcv(symbol, MACRO_TF, limit=100)

                if df_htf.empty or df_ltf.empty:
                    continue

                df_htf = analyzer.calculate_indicators(df_htf)
                df_ltf = analyzer.calculate_indicators(df_ltf)

                # Light indicators for 4H (only needs EMA20/50)
                if not df_4h.empty:
                    df_4h = analyzer.calculate_indicators_light(df_4h)
                else:
                    df_4h = None

                res = analyzer.get_signal_score(
                    symbol,
                    df_htf,
                    df_ltf,
                    liquidation_data=liqs,
                    df_btc=df_btc,
                    df_4h=df_4h,
                )

                if not res or res["confidence"] < CONFIDENCE_THRESHOLD:
                    if res:
                        logging.info(f"  {symbol}: Below threshold ({res['confidence']}%)")
                    signals_filtered += 1
                    continue

                last_ts = processed_candles.get(symbol)
                if last_ts == res["timestamp"]:
                    continue

                processed_candles[symbol] = res["timestamp"]
                set_cooldown(symbol)  # Set cooldown after signal fires
                signals_found += 1

                logging.info(f"  🎯 SIGNAL: {symbol} {res['side']} conf={res['confidence']}% rr={res.get('rr',0)}")
                await send_signal(res, df_ltf, context)

            except Exception as e:
                print(f"{symbol} error:", e)

            await asyncio.sleep(0.4)

        print(f"\n📊 Scan complete: {signals_found} signals, {signals_filtered} filtered out")

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
    app.add_handler(CommandHandler("setcooldown", setcooldown_command))

    app.add_error_handler(global_error_handler)
    
    app.job_queue.run_repeating(
        market_scanner,
        interval=SCAN_INTERVAL,
        first=10
    )

    print("🚀 Futures Momentum Analyzer v3 Running")
    print(f"   Confidence ≥ {CONFIDENCE_THRESHOLD}%")
    print(f"   Scan interval: {SCAN_INTERVAL}s")
    print(f"   Cooldown: {SIGNAL_COOLDOWN // 3600}h per symbol")
    print(f"   Timeframes: {HTF} / {LTF} / {MACRO_TF}")
    print(f"   Filters: 5 hard gates + 11 scoring algorithms")
    app.run_polling()
