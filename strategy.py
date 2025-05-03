import pandas as pd
import numpy as np

def calculate_indicators(df):
    if len(df) < 26:
        print("⚠️ Недостаточно данных для анализа")
        return df

    delta = df['Close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    df['ema12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['ema26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['macd_line'] = df['ema12'] - df['ema26']
    df['signal_line'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['signal_line']

    return df

def execute_strategy(df, send_telegram_message, symbol="BTCUSDT"):
    df = calculate_indicators(df)

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(latest['rsi']) or pd.isna(latest['macd_line']):
        print("⚠️ Индикаторы ещё не рассчитаны")
        return

    print(f"📉 RSI: {latest['rsi']:.2f}")
    print(f"📉 MACD: {latest['macd_line']:.2f} | Signal: {latest['signal_line']:.2f}")

    # Покупка
    if prev['macd_line'] < prev['signal_line'] and latest['macd_line'] > latest['signal_line'] and latest['rsi'] < 30:
        message = f"🟢 [RSI+MACD] Покупка {symbol}\nЦена: {latest['Close']:.2f}$\nRSI: {latest['rsi']:.2f}"
        send_telegram_message(message)

    # Продажа
    elif prev['macd_line'] > prev['signal_line'] and latest['macd_line'] < latest['signal_line'] and latest['rsi'] > 70:
        message = f"🔴 [RSI+MACD] Продажа {symbol}\nЦена: {latest['Close']:.2f}$\nRSI: {latest['rsi']:.2f}"
        send_telegram_message(message)