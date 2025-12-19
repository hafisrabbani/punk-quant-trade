import pandas_ta as ta
import pandas as pd
import numpy as np

def calculate_indicators(df):
    if df.empty or len(df) < 200: return df
    try:
        df['EMA_200'] = ta.ema(df['close'], length=200)
        df['EMA_50']  = ta.ema(df['close'], length=50)
        df['EMA_20']  = ta.ema(df['close'], length=20)
        df['RSI'] = ta.rsi(df['close'], length=14)
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        
        adx = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx is not None and not adx.empty:
            df['ADX'] = adx['ADX_14']
        else:
            df['ADX'] = 0
            
        df['Vol_MA'] = ta.sma(df['volume'], length=20)
    except:
        pass
    return df

def get_signal_score(symbol, df_htf, df_ltf):
    if len(df_htf) < 210 or len(df_ltf) < 210: return None
    
    htf = df_htf.iloc[-2]
    ltf = df_ltf.iloc[-2]
    
    if pd.isna(htf['EMA_200']) or pd.isna(ltf['RSI']): return None

    score = 0
    reasons = [] # <--- Awalnya kosong
    
    # 1. Tentukan Trend & Bias
    htf_bull = htf['close'] > htf['EMA_200']
    ltf_bull = ltf['close'] > ltf['EMA_200']
    
    if htf_bull and ltf_bull: 
        bias = "LONG"
    elif not htf_bull and not ltf_bull: 
        bias = "SHORT"
    else: 
        return None

    # Masukkan alasan dasar
    reasons.append(f"Trend {bias} (HTF+LTF)") 

    # 2. Scoring
    score += 30 
    
    # EMA Structure
    if bias == "LONG" and ltf['EMA_20'] > ltf['EMA_50']: 
        score += 20
        reasons.append("EMA Golden Cross") # <--- Tambahkan ini
    elif bias == "SHORT" and ltf['EMA_20'] < ltf['EMA_50']: 
        score += 20
        reasons.append("EMA Death Cross") # <--- Tambahkan ini
    
    # RSI Filter
    if bias == "LONG" and 40 <= ltf['RSI'] <= 65: 
        score += 20
        reasons.append("RSI Bullish Zone") # <--- Tambahkan ini
    elif bias == "SHORT" and 35 <= ltf['RSI'] <= 60: 
        score += 20
        reasons.append("RSI Bearish Zone") # <--- Tambahkan ini
    
    # ADX & Volume
    if ltf.get('ADX', 0) > 25: 
        score += 20
        reasons.append("Strong Momentum (ADX)") # <--- Tambahkan ini

    if ltf['volume'] > ltf.get('Vol_MA', 0): 
        score += 10
        reasons.append("High Volume") # <--- Tambahkan ini

    # 3. Entry Trigger Check
    prev = df_ltf.iloc[-3]
    trigger = False
    trigger_reason = ""

    if bias == "LONG":
        if (prev['EMA_20'] <= prev['EMA_50'] and ltf['EMA_20'] > ltf['EMA_50']):
            trigger = True
            trigger_reason = "EMA Cross Entry"
        elif (prev['close'] < prev['EMA_20'] and ltf['close'] > ltf['EMA_20']):
            trigger = True
            trigger_reason = "Price Break EMA20"

    elif bias == "SHORT":
        if (prev['EMA_20'] >= prev['EMA_50'] and ltf['EMA_20'] < ltf['EMA_50']):
            trigger = True
            trigger_reason = "EMA Cross Entry"
        elif (prev['close'] > prev['EMA_20'] and ltf['close'] < ltf['EMA_20']):
            trigger = True
            trigger_reason = "Price Break EMA20"
            
    if not trigger: return None
    
    reasons.append(trigger_reason) # Tambahkan alasan trigger

    # --- 4. ADVANCED TP STRATEGY (FIBONACCI) ---
    curr_price = ltf['close']
    atr = ltf['ATR'] if not pd.isna(ltf['ATR']) else (curr_price * 0.01)
    
    swing_dist = atr * 2 

    if bias == "LONG":
        sl = curr_price - swing_dist
        risk_range = curr_price - sl
        tp1 = curr_price + (risk_range * 0.618)
        tp2 = curr_price + (risk_range * 1.0)
        tp3 = curr_price + (risk_range * 1.618)
        
    else: # SHORT
        sl = curr_price + swing_dist
        risk_range = sl - curr_price
        tp1 = curr_price - (risk_range * 0.618)
        tp2 = curr_price - (risk_range * 1.0)
        tp3 = curr_price - (risk_range * 1.618)

    return {
        'symbol': symbol,
        'side': bias,
        'confidence': score,
        'price': curr_price,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'tp3': tp3,
        'reasons': reasons, # Sekarang list ini sudah berisi data string
        'timestamp': str(ltf['timestamp']),
        'rsi': ltf['RSI']
    }