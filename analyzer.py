import pandas_ta as ta
import pandas as pd
import numpy as np

# =========================
# INDICATORS
# =========================

def calculate_indicators(df):
    if df.empty or len(df) < 200:
        return df

    df['EMA_200'] = ta.ema(df['close'], length=200)
    df['EMA_50']  = ta.ema(df['close'], length=50)
    df['EMA_20']  = ta.ema(df['close'], length=20)
    df['RSI'] = ta.rsi(df['close'], length=14)
    df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)

    adx = ta.adx(df['high'], df['low'], df['close'], length=14)
    df['ADX'] = adx['ADX_14'] if adx is not None else 0

    df['Vol_MA'] = ta.sma(df['volume'], length=20)
    return df


# =========================
# MARKET STRUCTURE (HTF)
# =========================

def detect_structure(df, lookback=20):
    """
    Simple HH/LL structure zone
    """
    high = df['high'].rolling(lookback).max().iloc[-2]
    low = df['low'].rolling(lookback).min().iloc[-2]
    return high, low


# =========================
# LIQUIDATION HEATMAP
# =========================

def liquidation_heatmap(liqs):
    """
    Binance USD-M liquidation data
    """
    if not liqs:
        return 50, "NEUTRAL"

    long_liq = 0.0   # LONG positions liquidated
    short_liq = 0.0  # SHORT positions liquidated

    for l in liqs:
        amt = float(l.get('amount', 0))
        if l.get('side') == 'sell':
            long_liq += amt
        elif l.get('side') == 'buy':
            short_liq += amt

    total = long_liq + short_liq
    if total == 0:
        return 50, "NEUTRAL"

    # Normalize to 0–100
    ratio = (short_liq - long_liq) / total
    score = int((ratio + 1) * 50)

    if score >= 60:
        bias = "BULLISH"
    elif score <= 40:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return score, bias


# =========================
# Candlestick Patterns
# =========================
def detect_candle_pattern(df):
    """
    Detect simple high-probability candlestick patterns
    Return: (pattern_name, bias, score)
    """
    if len(df) < 3:
        return None, None, 0

    prev = df.iloc[-2]
    prev2 = df.iloc[-3]

    body = abs(prev['close'] - prev['open'])
    candle_range = prev['high'] - prev['low']

    # Avoid division by zero
    if candle_range == 0:
        return None, None, 0

    upper_wick = prev['high'] - max(prev['close'], prev['open'])
    lower_wick = min(prev['close'], prev['open']) - prev['low']

    # =========================
    # Bullish Engulfing
    # =========================
    if (
        prev2['close'] < prev2['open'] and
        prev['close'] > prev['open'] and
        prev['close'] > prev2['open'] and
        prev['open'] < prev2['close']
    ):
        return "Bullish Engulfing", "LONG", 15

    # =========================
    # Bearish Engulfing
    # =========================
    if (
        prev2['close'] > prev2['open'] and
        prev['close'] < prev['open'] and
        prev['open'] > prev2['close'] and
        prev['close'] < prev2['open']
    ):
        return "Bearish Engulfing", "SHORT", 15

    # =========================
    # Hammer
    # =========================
    if lower_wick > body * 2 and upper_wick < body:
        return "Hammer", "LONG", 10

    # =========================
    # Shooting Star
    # =========================
    if upper_wick > body * 2 and lower_wick < body:
        return "Shooting Star", "SHORT", 10

    # =========================
    # Doji
    # =========================
    if body / candle_range < 0.1:
        return "Doji", "NEUTRAL", -5

    return None, None, 0


# =========================
# SIGNAL ENGINE
# =========================

def get_signal_score(symbol, df_htf, df_ltf, liquidation_data=None):
    if len(df_htf) < 210 or len(df_ltf) < 210:
        return None

    htf = df_htf.iloc[-2]
    ltf = df_ltf.iloc[-2]
    prev = df_ltf.iloc[-3]

    score = 0
    reasons = []

    # ---- TREND ----
    htf_bull = htf['close'] > htf['EMA_200']
    ltf_bull = ltf['close'] > ltf['EMA_200']

    if htf_bull and ltf_bull:
        bias = "LONG"
        score += 30
        reasons.append("Trend Bullish HTF+LTF")
    elif not htf_bull and not ltf_bull:
        bias = "SHORT"
        score += 30
        reasons.append("Trend Bearish HTF+LTF")
    else:
        bias = "LONG" if htf_bull else "SHORT"
        score -= 20
        reasons.append("Trend HTF/LTF Not Aligned")

    # ---- STRUCTURE ----
    struct_high, struct_low = detect_structure(df_htf)
    if bias == "LONG" and ltf['close'] > struct_low:
        score += 15
        reasons.append("Bullish Structure")
    elif bias == "SHORT" and ltf['close'] < struct_high:
        score += 15
        reasons.append("Bearish Structure")

    # ---- LIQUIDATION ----
    liq_score, liq_bias = liquidation_heatmap(liquidation_data)
    if bias == "LONG" and liq_bias == "BULLISH":
        score += 20
        reasons.append("Short Liquidation Sweep")
    elif bias == "SHORT" and liq_bias == "BEARISH":
        score += 20
        reasons.append("Long Liquidation Sweep")
    else:
        score -= 5
        reasons.append("Liquidation Neutral")

    # ---- ENTRY ----
    trigger = False
    if bias == "LONG" and ltf['close'] > ltf['EMA_20']:
        trigger = True
        reasons.append("Above EMA20")
    elif bias == "SHORT" and ltf['close'] < ltf['EMA_20']:
        trigger = True
        reasons.append("Below EMA20")
    else:
        score -= 10
        reasons.append("Weak Entry Area")

    pattern_name, pattern_bias, pattern_score = detect_candle_pattern(df_ltf)

    # ---- CANDLE PATTERN ----
    if pattern_name:
        if pattern_bias == bias:
            score += pattern_score
            reasons.append(f"Candle Pattern: {pattern_name}")
        elif pattern_bias == "NEUTRAL":
            score += pattern_score
            reasons.append(f"Candle Pattern: {pattern_name} (Indecision)")
        else:
            score -= 10
            reasons.append(f"Candle Pattern Against Bias: {pattern_name}")

    # ---- TP SL ----
    atr = htf['ATR']
    entry = ltf['close']
    if bias == "LONG":
        sl = entry - atr * 1.2
        tp1 = entry + atr * 2
        tp2 = entry + atr * 3
    else:
        sl = entry + atr * 1.2
        tp1 = entry - atr * 2
        tp2 = entry - atr * 3

    rr = abs(tp1 - entry) / abs(entry - sl)
    if rr >= 2:
        score += 25
        reasons.append(f"RR Strong ({rr:.2f})")
    elif rr >= 1.5:
        score += 10
        reasons.append(f"RR Moderate ({rr:.2f})")
    else:
        score -= 10
        reasons.append(f"RR Weak ({rr:.2f})")

    return {
        "symbol": symbol,
        "side": bias,
        "confidence": max(min(score, 100), 0),
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "reasons": reasons,
        "timestamp": str(ltf['timestamp']),
        "structure": {
            "high": struct_high,
            "low": struct_low
        },
        "liquidation": {
            "score": liq_score,
            "bias": liq_bias
        },
        "pattern": pattern_name,
    }
