# talib_alternative.py - Implementación alternativa de indicadores TA
import numpy as np
import pandas as pd

def EMA(prices, period):
    """Implementación alternativa de EMA"""
    return pd.Series(prices).ewm(span=period, adjust=False).mean().values

def RSI(prices, period=14):
    """Implementación alternativa de RSI"""
    series = pd.Series(prices)
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50).values  # Rellenar NaN con 50 (neutral)

def ATR(highs, lows, closes, period=14):
    """Implementación alternativa de ATR"""
    high = pd.Series(highs)
    low = pd.Series(lows)
    close = pd.Series(closes)
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    return atr.fillna(tr.mean() if not tr.empty else 0).values