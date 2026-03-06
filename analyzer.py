import pandas_ta as ta
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

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

    # ATR smoothed average for volatility expansion detection
    df['ATR_SMA'] = ta.sma(df['ATR'], length=20)

    adx = ta.adx(df['high'], df['low'], df['close'], length=14)
    if adx is not None:
        df['ADX'] = adx['ADX_14']
        df['DI_PLUS'] = adx['DMP_14']
        df['DI_MINUS'] = adx['DMN_14']
    else:
        df['ADX'] = 0
        df['DI_PLUS'] = 0
        df['DI_MINUS'] = 0

    # MACD for momentum confirmation
    macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
    if macd is not None:
        df['MACD'] = macd['MACD_12_26_9']
        df['MACD_SIGNAL'] = macd['MACDs_12_26_9']
        df['MACD_HIST'] = macd['MACDh_12_26_9']
    else:
        df['MACD'] = 0
        df['MACD_SIGNAL'] = 0
        df['MACD_HIST'] = 0

    # Bollinger Bands for squeeze/expansion detection
    bb = ta.bbands(df['close'], length=20, std=2)
    if bb is not None:
        df['BB_UPPER'] = bb['BBU_20_2.0']
        df['BB_LOWER'] = bb['BBL_20_2.0']
        df['BB_MID'] = bb['BBM_20_2.0']
        # Bandwidth = (upper - lower) / middle
        df['BB_WIDTH'] = (df['BB_UPPER'] - df['BB_LOWER']) / df['BB_MID']
        df['BB_WIDTH_SMA'] = ta.sma(df['BB_WIDTH'], length=20)
    else:
        df['BB_UPPER'] = 0
        df['BB_LOWER'] = 0
        df['BB_MID'] = 0
        df['BB_WIDTH'] = 0
        df['BB_WIDTH_SMA'] = 0

    df['Vol_MA'] = ta.sma(df['volume'], length=20)
    return df


def calculate_indicators_light(df):
    """
    Lightweight indicator calculation for higher timeframes (4H)
    that only need EMA20/EMA50 for trend confirmation.
    """
    if df.empty or len(df) < 60:
        return df

    df['EMA_50'] = ta.ema(df['close'], length=50)
    df['EMA_20'] = ta.ema(df['close'], length=20)
    df['EMA_200'] = ta.ema(df['close'], length=50)  # Use 50 as proxy on 4H
    return df


# =========================
# FILTER: VOLATILITY GATE
# =========================

def check_volatility_expansion(df, threshold=1.2):
    """
    ATR must be > threshold × ATR_SMA (20-period).
    Returns True if volatility is expanding (good for momentum).
    """
    row = df.iloc[-2]
    atr = row.get('ATR', 0)
    atr_sma = row.get('ATR_SMA', 0)

    if pd.isna(atr) or pd.isna(atr_sma) or atr_sma == 0:
        return False, 0.0

    ratio = atr / atr_sma
    return ratio >= threshold, round(ratio, 2)


# =========================
# FILTER: MARKET REGIME (ADX)
# =========================

def check_market_regime(df, min_adx=25):
    """
    ADX > min_adx = trending market, good for momentum signals.
    ADX <= min_adx = ranging/choppy, skip signals.
    """
    row = df.iloc[-2]
    adx = row.get('ADX', 0)

    if pd.isna(adx):
        return False, 0.0

    return adx >= min_adx, round(float(adx), 1)


# =========================
# FILTER: VOLUME SPIKE
# =========================

def check_volume_spike(df, threshold=1.5):
    """
    Current volume must be > threshold × 20-period volume SMA.
    Confirms institutional participation.
    """
    row = df.iloc[-2]
    vol = row.get('volume', 0)
    vol_ma = row.get('Vol_MA', 0)

    if pd.isna(vol) or pd.isna(vol_ma) or vol_ma == 0:
        return False, 0.0

    ratio = vol / vol_ma
    return ratio >= threshold, round(ratio, 2)


# =========================
# FILTER: EMA STACK ALIGNMENT
# =========================

def check_ema_stack(df):
    """
    Proper EMA stack alignment:
      LONG:  EMA20 > EMA50 > EMA200
      SHORT: EMA20 < EMA50 < EMA200
    Returns ('LONG', True), ('SHORT', True), or (None, False)
    """
    row = df.iloc[-2]
    ema20 = row.get('EMA_20', 0)
    ema50 = row.get('EMA_50', 0)
    ema200 = row.get('EMA_200', 0)

    if any(pd.isna(v) for v in [ema20, ema50, ema200]):
        return None, False

    if ema20 > ema50 > ema200:
        return 'LONG', True
    elif ema20 < ema50 < ema200:
        return 'SHORT', True
    else:
        return None, False


# =========================
# FILTER: MOMENTUM (RSI + MACD)
# =========================

def check_momentum(df, bias):
    """
    RSI momentum shift: RSI should be > 55 for LONG, < 45 for SHORT.
    MACD histogram expanding in bias direction.
    Returns (rsi_ok, macd_ok, details_dict)
    """
    row = df.iloc[-2]
    prev = df.iloc[-3]

    rsi = row.get('RSI', 50)
    macd_hist = row.get('MACD_HIST', 0)
    prev_macd_hist = prev.get('MACD_HIST', 0)

    if pd.isna(rsi):
        rsi = 50
    if pd.isna(macd_hist):
        macd_hist = 0
    if pd.isna(prev_macd_hist):
        prev_macd_hist = 0

    if bias == 'LONG':
        rsi_ok = rsi > 55
        macd_ok = macd_hist > 0 and macd_hist > prev_macd_hist
    else:
        rsi_ok = rsi < 45
        macd_ok = macd_hist < 0 and macd_hist < prev_macd_hist

    return rsi_ok, macd_ok, {
        'rsi': round(float(rsi), 1),
        'macd_hist': round(float(macd_hist), 4),
        'macd_expanding': macd_ok
    }


# =========================
# FILTER: BTC CORRELATION
# =========================

def check_btc_correlation(symbol, df_btc, bias):
    """
    Altcoins should not trade against BTC trend.
    BTC trend = close vs EMA50 on HTF.
    - BTC bearish + altcoin LONG = block
    - BTC bullish + altcoin SHORT = block
    - BTC/USDT itself is exempt.
    Returns (is_aligned, btc_trend_str)
    """
    # BTC itself is always exempt
    if 'BTC' in symbol.split('/')[0]:
        return True, "EXEMPT"

    if df_btc is None or df_btc.empty or len(df_btc) < 60:
        return True, "NO_DATA"

    row = df_btc.iloc[-2]
    btc_close = row.get('close', 0)
    btc_ema50 = row.get('EMA_50', 0)

    if pd.isna(btc_close) or pd.isna(btc_ema50) or btc_ema50 == 0:
        return True, "NO_DATA"

    btc_bullish = btc_close > btc_ema50

    if btc_bullish:
        btc_trend = "BULLISH"
        aligned = (bias == "LONG")  # OK to go long when BTC is bullish
    else:
        btc_trend = "BEARISH"
        aligned = (bias == "SHORT")  # OK to go short when BTC is bearish

    return aligned, btc_trend


# =========================
# FILTER: RSI DIVERGENCE
# =========================

def check_rsi_divergence(df, bias, lookback=14):
    """
    Detect RSI divergence over the last `lookback` candles.
    - Bullish divergence: price lower low + RSI higher low → reversal up
    - Bearish divergence: price higher high + RSI lower high → reversal down

    Returns (divergence_type, score_impact)
      divergence_type: 'BULLISH_DIV', 'BEARISH_DIV', or None
      score_impact: positive if divergence supports bias, negative if against
    """
    if len(df) < lookback + 5:
        return None, 0

    window = df.iloc[-(lookback + 2):-1]  # Use completed candles

    prices_high = window['high'].values
    prices_low = window['low'].values
    rsi_vals = window['RSI'].values

    # Clean NaN
    if any(pd.isna(rsi_vals)):
        return None, 0

    # Find swing highs/lows in the window
    mid = len(prices_high) // 2

    # Compare first half peak/trough vs second half peak/trough
    first_high = np.max(prices_high[:mid])
    second_high = np.max(prices_high[mid:])
    first_high_rsi = rsi_vals[:mid][np.argmax(prices_high[:mid])]
    second_high_rsi = rsi_vals[mid:][np.argmax(prices_high[mid:])]

    first_low = np.min(prices_low[:mid])
    second_low = np.min(prices_low[mid:])
    first_low_rsi = rsi_vals[:mid][np.argmin(prices_low[:mid])]
    second_low_rsi = rsi_vals[mid:][np.argmin(prices_low[mid:])]

    # Bearish divergence: higher high in price, lower high in RSI
    if second_high > first_high and second_high_rsi < first_high_rsi - 3:
        if bias == "SHORT":
            return "BEARISH_DIV", 5  # Supports short bias
        else:
            return "BEARISH_DIV", -5  # Against long bias

    # Bullish divergence: lower low in price, higher low in RSI
    if second_low < first_low and second_low_rsi > first_low_rsi + 3:
        if bias == "LONG":
            return "BULLISH_DIV", 5  # Supports long bias
        else:
            return "BULLISH_DIV", -5  # Against short bias

    return None, 0


# =========================
# FILTER: BOLLINGER BAND SQUEEZE → EXPANSION
# =========================

def check_bb_squeeze_expansion(df):
    """
    Detect Bollinger Band squeeze-to-expansion transition.
    Squeeze: bandwidth < its 20-SMA (compressed)
    Expansion: bandwidth > its 20-SMA (expanding)

    Look for transition: previous candle was in squeeze, current is expanding.
    Returns (is_expanding, was_squeeze, bandwidth_ratio)
    """
    if len(df) < 25:
        return False, False, 0.0

    curr = df.iloc[-2]
    prev = df.iloc[-3]

    bw = curr.get('BB_WIDTH', 0)
    bw_sma = curr.get('BB_WIDTH_SMA', 0)
    prev_bw = prev.get('BB_WIDTH', 0)
    prev_bw_sma = prev.get('BB_WIDTH_SMA', 0)

    if any(pd.isna(v) or v == 0 for v in [bw, bw_sma, prev_bw, prev_bw_sma]):
        return False, False, 0.0

    is_expanding = bw > bw_sma
    was_squeeze = prev_bw < prev_bw_sma

    ratio = round(bw / bw_sma, 2) if bw_sma > 0 else 0.0

    return is_expanding, was_squeeze, ratio


# =========================
# FILTER: CONSECUTIVE MOMENTUM CANDLES
# =========================

def check_consecutive_candles(df, bias, min_count=2, lookback=3):
    """
    Check if at least `min_count` of the last `lookback` candles
    closed in the bias direction with body > ATR * 0.3.
    Confirms real directional pressure, not indecision.
    """
    if len(df) < lookback + 3:
        return False, 0

    atr = df.iloc[-2].get('ATR', 0)
    if pd.isna(atr) or atr == 0:
        return False, 0

    min_body = atr * 0.3
    count = 0

    for i in range(2, 2 + lookback):  # Start from -2 (completed candles)
        row = df.iloc[-i]
        body = row['close'] - row['open']

        if bias == 'LONG' and body > min_body:
            count += 1
        elif bias == 'SHORT' and body < -min_body:
            count += 1

    return count >= min_count, count


# =========================
# FILTER: 4H TIMEFRAME CONFIRMATION
# =========================

def check_higher_tf_confirmation(df_4h, bias):
    """
    Check 4H timeframe for macro trend confirmation.
    LONG: EMA20 > EMA50 on 4H
    SHORT: EMA20 < EMA50 on 4H
    Returns (confirmed, ema20_val, ema50_val)
    """
    if df_4h is None or df_4h.empty or len(df_4h) < 55:
        return False, 0, 0

    row = df_4h.iloc[-2]
    ema20 = row.get('EMA_20', 0)
    ema50 = row.get('EMA_50', 0)

    if pd.isna(ema20) or pd.isna(ema50):
        return False, 0, 0

    if bias == 'LONG':
        confirmed = ema20 > ema50
    else:
        confirmed = ema20 < ema50

    return confirmed, round(float(ema20), 4), round(float(ema50), 4)


# =========================
# FIBONACCI TP/SL CALCULATOR
# =========================

def calculate_fib_tp_sl(df, bias, lookback=30):
    """
    Calculate TP/SL using Fibonacci extensions from recent swing.
    Finds recent swing high/low in the lookback window.
    - LONG: swing low → swing high, TP at 1.618/2.618 ext, SL at 0.786 retrace
    - SHORT: swing high → swing low, TP at 1.618/2.618 ext, SL at 0.786 retrace

    Falls back to ATR-based levels if swing is too small.
    """
    if len(df) < lookback + 5:
        return None

    window = df.iloc[-(lookback + 1):-1]
    atr = df.iloc[-2].get('ATR', 0)
    entry = df.iloc[-2]['close']

    if pd.isna(atr) or atr == 0:
        return None

    swing_high = window['high'].max()
    swing_low = window['low'].min()
    swing_range = swing_high - swing_low

    # If swing is too small (less than 1 ATR), fallback to ATR-based
    if swing_range < atr * 0.5:
        return None

    if bias == 'LONG':
        # Fib extensions from swing low to swing high
        sl = entry - swing_range * 0.786
        tp1 = entry + swing_range * 1.618
        tp2 = entry + swing_range * 2.618

        # Sanity: SL shouldn't be too far (max 2× ATR from entry)
        max_sl = entry - atr * 2.0
        sl = max(sl, max_sl)

    else:
        # Fib extensions from swing high to swing low
        sl = entry + swing_range * 0.786
        tp1 = entry - swing_range * 1.618
        tp2 = entry - swing_range * 2.618

        # Sanity: SL shouldn't be too far
        max_sl = entry + atr * 2.0
        sl = min(sl, max_sl)

    return {
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'swing_high': swing_high,
        'swing_low': swing_low,
        'swing_range': swing_range,
        'method': 'FIBONACCI'
    }


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
# BREAKOUT DETECTION
# =========================

def check_breakout(df, struct_high, struct_low, bias):
    """
    Check if price has broken out above structure high (LONG)
    or below structure low (SHORT).
    Requires candle CLOSE beyond the level, not just a wick.
    """
    row = df.iloc[-2]
    close = row['close']

    if bias == 'LONG':
        return close > struct_high
    else:
        return close < struct_low


# =========================
# LIQUIDATION HEATMAP
# =========================

def liquidation_heatmap(liqs):
    """
    Binance USD-M liquidation data
    """
    if not liqs:
        return 50, "NEUTRAL"

    long_liq = 0.0
    short_liq = 0.0

    for l in liqs:
        amt = float(l.get('amount', 0))
        if l.get('side') == 'sell':
            long_liq += amt
        elif l.get('side') == 'buy':
            short_liq += amt

    total = long_liq + short_liq
    if total == 0:
        return 50, "NEUTRAL"

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

    if candle_range == 0:
        return None, None, 0

    upper_wick = prev['high'] - max(prev['close'], prev['open'])
    lower_wick = min(prev['close'], prev['open']) - prev['low']

    # Bullish Engulfing
    if (
        prev2['close'] < prev2['open'] and
        prev['close'] > prev['open'] and
        prev['close'] > prev2['open'] and
        prev['open'] < prev2['close']
    ):
        return "Bullish Engulfing", "LONG", 10

    # Bearish Engulfing
    if (
        prev2['close'] > prev2['open'] and
        prev['close'] < prev['open'] and
        prev['open'] > prev2['close'] and
        prev['close'] < prev2['open']
    ):
        return "Bearish Engulfing", "SHORT", 10

    # Hammer
    if lower_wick > body * 2 and upper_wick < body:
        return "Hammer", "LONG", 5

    # Shooting Star
    if upper_wick > body * 2 and lower_wick < body:
        return "Shooting Star", "SHORT", 5

    # Doji
    if body / candle_range < 0.1:
        return "Doji", "NEUTRAL", -5

    return None, None, 0


# ================================================================
# SIGNAL ENGINE (v3) — with advanced algorithms
# ================================================================

def get_signal_score(symbol, df_htf, df_ltf, liquidation_data=None,
                     df_btc=None, df_4h=None):
    """
    Advanced signal engine v3 with:
    - 5 hard gates (ADX, ATR, trend, volume, BTC correlation)
    - Momentum scoring (RSI, MACD, BB, consecutive candles, divergence)
    - 4H timeframe confirmation
    - Fibonacci TP/SL
    Returns signal dict or None.
    """
    if len(df_htf) < 210 or len(df_ltf) < 210:
        return None

    htf = df_htf.iloc[-2]
    ltf = df_ltf.iloc[-2]

    score = 0
    reasons = []
    gates_passed = []

    # ============================================================
    # HARD GATE 1: Market Regime (ADX)
    # ============================================================
    is_trending, adx_val = check_market_regime(df_htf, min_adx=25)
    if not is_trending:
        logger.info(f"  {symbol}: SKIP — ADX too low ({adx_val}), ranging market")
        return None
    gates_passed.append(f"ADX Trending ({adx_val})")

    # ============================================================
    # HARD GATE 2: Volatility Expansion (ATR)
    # ============================================================
    vol_expanding, atr_ratio = check_volatility_expansion(df_ltf, threshold=1.2)
    if not vol_expanding:
        logger.info(f"  {symbol}: SKIP — ATR not expanding ({atr_ratio}x)")
        return None
    gates_passed.append(f"Volatility Expanding ({atr_ratio}x)")

    # ============================================================
    # HARD GATE 3: Trend Alignment (HTF + LTF)
    # ============================================================
    htf_bull = htf['close'] > htf['EMA_200']
    ltf_bull = ltf['close'] > ltf['EMA_200']

    if htf_bull and ltf_bull:
        bias = "LONG"
    elif not htf_bull and not ltf_bull:
        bias = "SHORT"
    else:
        logger.info(f"  {symbol}: SKIP — HTF/LTF trend not aligned")
        return None
    gates_passed.append(f"Trend Aligned ({bias})")

    # ============================================================
    # HARD GATE 4: Volume Spike
    # ============================================================
    vol_spike, vol_ratio = check_volume_spike(df_ltf, threshold=1.5)
    if not vol_spike:
        logger.info(f"  {symbol}: SKIP — Volume below average ({vol_ratio}x)")
        return None
    gates_passed.append(f"Volume Spike ({vol_ratio}x)")

    # ============================================================
    # HARD GATE 5: BTC Correlation (altcoins only)
    # ============================================================
    btc_aligned, btc_trend = check_btc_correlation(symbol, df_btc, bias)
    if not btc_aligned:
        logger.info(f"  {symbol}: SKIP — Against BTC trend ({btc_trend}), bias={bias}")
        return None
    gates_passed.append(f"BTC Correlation ({btc_trend})")

    # ============================================================
    # All hard gates passed — now compute score
    # ============================================================
    logger.info(f"  {symbol}: All gates passed, computing score...")

    # ---- TREND SCORE (20pts) ----
    score += 20
    reasons.append(f"Trend {bias} HTF+LTF (ADX={adx_val})")

    # ---- EMA STACK ALIGNMENT (10pts) ----
    ema_bias, ema_aligned = check_ema_stack(df_ltf)
    if ema_aligned and ema_bias == bias:
        score += 10
        reasons.append("EMA Stack Aligned (20>50>200)" if bias == "LONG" else "EMA Stack Aligned (20<50<200)")
    else:
        score -= 5
        reasons.append("EMA Stack Not Aligned")

    # ---- MARKET STRUCTURE (15pts) ----
    struct_high, struct_low = detect_structure(df_htf)
    is_breakout = check_breakout(df_ltf, struct_high, struct_low, bias)

    if is_breakout:
        score += 15
        reasons.append("Structure Breakout Confirmed")
    elif bias == "LONG" and ltf['close'] > struct_low:
        score += 8
        reasons.append("Above Structure Support")
    elif bias == "SHORT" and ltf['close'] < struct_high:
        score += 8
        reasons.append("Below Structure Resistance")
    else:
        score -= 5
        reasons.append("Weak Structure Position")

    # ---- MOMENTUM RSI + MACD (15pts) ----
    rsi_ok, macd_ok, mom_details = check_momentum(df_ltf, bias)

    if rsi_ok and macd_ok:
        score += 15
        reasons.append(f"Strong Momentum (RSI={mom_details['rsi']}, MACD expanding)")
    elif rsi_ok or macd_ok:
        score += 7
        indicator = "RSI" if rsi_ok else "MACD"
        reasons.append(f"Partial Momentum ({indicator} confirms, RSI={mom_details['rsi']})")
    else:
        score -= 5
        reasons.append(f"Weak Momentum (RSI={mom_details['rsi']})")

    # ---- VOLATILITY BONUS (10pts) ----
    score += 10
    reasons.append(f"Volatility Expansion ({atr_ratio}x ATR)")

    # ---- VOLUME BONUS (10pts) ----
    score += 10
    reasons.append(f"Volume Spike ({vol_ratio}x avg)")

    # ---- BOLLINGER BAND SQUEEZE → EXPANSION (8pts) ----
    bb_expanding, bb_was_squeeze, bb_ratio = check_bb_squeeze_expansion(df_ltf)
    if bb_expanding and bb_was_squeeze:
        score += 8
        reasons.append(f"🔥 BB Squeeze Breakout ({bb_ratio}x bandwidth)")
    elif bb_expanding:
        score += 4
        reasons.append(f"BB Expanding ({bb_ratio}x bandwidth)")
    else:
        reasons.append("BB Not Expanding")

    # ---- CONSECUTIVE MOMENTUM CANDLES (8pts) ----
    consec_ok, consec_count = check_consecutive_candles(df_ltf, bias, min_count=2, lookback=3)
    if consec_ok:
        score += 8
        reasons.append(f"Consecutive Momentum ({consec_count}/3 candles)")
    else:
        score -= 3
        reasons.append(f"Weak Candle Flow ({consec_count}/3 candles)")

    # ---- 4H TIMEFRAME CONFIRMATION (8pts) ----
    htf4_confirmed, htf4_ema20, htf4_ema50 = check_higher_tf_confirmation(df_4h, bias)
    if htf4_confirmed:
        score += 8
        reasons.append(f"4H Trend Confirmed (EMA20={htf4_ema20})")
    else:
        score -= 3
        if df_4h is not None and not df_4h.empty:
            reasons.append(f"4H Trend Not Confirmed")
        else:
            reasons.append("4H Data Unavailable")

    # ---- RSI DIVERGENCE (±5pts) ----
    div_type, div_impact = check_rsi_divergence(df_ltf, bias)
    if div_type:
        score += div_impact
        if div_impact > 0:
            reasons.append(f"✅ {div_type} supports {bias}")
        else:
            reasons.append(f"⚠️ {div_type} against {bias}")

    # ---- LIQUIDATION (15pts) ----
    liq_score, liq_bias = liquidation_heatmap(liquidation_data)
    if bias == "LONG" and liq_bias == "BULLISH":
        score += 15
        reasons.append("Short Liquidation Sweep")
    elif bias == "SHORT" and liq_bias == "BEARISH":
        score += 15
        reasons.append("Long Liquidation Sweep")
    elif liq_bias == "NEUTRAL":
        score += 0
        reasons.append("Liquidation Neutral")
    else:
        score -= 5
        reasons.append("Liquidation Against Bias")

    # ---- CANDLE PATTERN (±10pts bonus) ----
    pattern_name, pattern_bias, pattern_score = detect_candle_pattern(df_ltf)
    if pattern_name:
        if pattern_bias == bias:
            score += pattern_score
            reasons.append(f"Candle Pattern: {pattern_name}")
        elif pattern_bias == "NEUTRAL":
            score += pattern_score
            reasons.append(f"Candle Pattern: {pattern_name} (Indecision)")
        else:
            score -= 5
            reasons.append(f"Candle Pattern Against Bias: {pattern_name}")

    # ---- TP / SL (Fibonacci-first, ATR fallback) ----
    fib_levels = calculate_fib_tp_sl(df_ltf, bias, lookback=30)

    if fib_levels:
        sl = fib_levels['sl']
        tp1 = fib_levels['tp1']
        tp2 = fib_levels['tp2']
        tp_method = "FIBONACCI"
    else:
        # ATR fallback
        atr = htf['ATR']
        entry_price = ltf['close']
        if bias == "LONG":
            sl = entry_price - atr * 1.2
            tp1 = entry_price + atr * 2
            tp2 = entry_price + atr * 3
        else:
            sl = entry_price + atr * 1.2
            tp1 = entry_price - atr * 2
            tp2 = entry_price - atr * 3
        tp_method = "ATR"

    entry = ltf['close']

    # R:R scoring (15pts)
    rr = abs(tp1 - entry) / abs(entry - sl) if abs(entry - sl) > 0 else 0
    if rr >= 2.5:
        score += 15
        reasons.append(f"RR Excellent ({rr:.2f}) [{tp_method}]")
    elif rr >= 2:
        score += 12
        reasons.append(f"RR Strong ({rr:.2f}) [{tp_method}]")
    elif rr >= 1.5:
        score += 8
        reasons.append(f"RR Moderate ({rr:.2f}) [{tp_method}]")
    else:
        score -= 5
        reasons.append(f"RR Weak ({rr:.2f}) [{tp_method}]")

    # ---- FINAL ----
    confidence = max(min(score, 100), 0)

    return {
        "symbol": symbol,
        "side": bias,
        "confidence": confidence,
        "price": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr": round(rr, 2),
        "tp_method": tp_method,
        "reasons": reasons,
        "gates_passed": gates_passed,
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
        "momentum": mom_details,
        "advanced": {
            "btc_trend": btc_trend,
            "bb_expanding": bb_expanding,
            "bb_squeeze_breakout": bb_expanding and bb_was_squeeze,
            "consecutive_candles": consec_count,
            "htf4_confirmed": htf4_confirmed,
            "rsi_divergence": div_type,
            "fib_levels": fib_levels,
        }
    }
