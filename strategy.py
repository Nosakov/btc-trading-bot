import pandas as pd
import numpy as np

TRADE_QUANTITY = 0.002

def calculate_indicators(df):
    if len(df) < 26:
        print("⚠️ Недостаточно данных для анализа")
        return df

    # RSI
    delta = df['Close'].diff(1)
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=14).mean()
    avg_loss = loss.rolling(window=14).mean()
    rs = avg_gain / avg_loss
    df['rsi'] = 100 - (100 / (1 + rs))

    # MACD
    df['ema12'] = df['Close'].ewm(span=12, adjust=False).mean()
    df['ema26'] = df['Close'].ewm(span=26, adjust=False).mean()
    df['macd_line'] = df['ema12'] - df['ema26']
    df['signal_line'] = df['macd_line'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['signal_line']

    return df


def execute_strategy(df, send_telegram_message, place_order_func, symbol="BTCUSDT"):
    df = calculate_indicators(df)

    latest = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(latest['rsi']) or pd.isna(latest['macd_line']):
        print("⚠️ Индикаторы ещё не готовы")
        return

    print(f"📉 RSI: {latest['rsi']:.2f}")
    print(f"📉 MACD: {latest['macd_line']:.2f} | Signal: {latest['signal_line']:.2f}")

    # Покупка
    if prev['macd_line'] < prev['signal_line'] and latest['macd_line'] > latest['signal_line'] and \
            latest['rsi'] < 30:
        message = f"🟢 [RSI+MACD] Покупка {symbol}\nЦена: {latest['Close']:.2f}$\nRSI: {latest['rsi']:.2f}"
        send_telegram_message(message)
        place_order_func(symbol, 'buy', TRADE_QUANTITY)

    # Продажа
    elif prev['macd_line'] > prev['signal_line'] and latest['macd_line'] < latest['signal_line'] and \
            latest['rsi'] > 70:
        message = f"🔴 [RSI+MACD] Продажа {symbol}\nЦена: {latest['Close']:.2f}$\nRSI: {latest['rsi']:.2f}"
        send_telegram_message(message)
        place_order_func(symbol, 'sell', TRADE_QUANTITY)


def calculate_grid_levels(df, grid_size=50, num_levels=5):
    """
    Автоматическое построение сетки уровней вокруг средней цены
    """
    latest_price = df['Close'].iloc[-1]
    avg_price = df['Close'].rolling(window=grid_size).mean().iloc[-1]

    step = avg_price * 0.001  # Шаг 0.1%
    levels = [round(avg_price - step * i, 2) for i in range(num_levels, 0, -1)] + \
             [round(avg_price + step * i, 2) for i in range(1, num_levels + 1)]

    return {
        'avg_price': avg_price,
        'latest_price': latest_price,
        'levels': sorted(levels)
    }


def detect_grid_signal(df, grid_info, send_telegram_message, place_order_func, symbol="BTCUSDT"):
    """
    Проверяем, находится ли цена около одного из уровней сетки
    """
    latest_price = df['Close'].iloc[-1]
    levels = grid_info['levels']

    for level in levels:
        if abs(latest_price - level) < 1:  # Если цена рядом с уровнем
            if latest_price < level:
                message = f"🟢 [GRID] Цена ниже уровня {level} | BUY"
                place_order_func(symbol, 'buy', TRADE_QUANTITY)
            else:
                message = f"🔴 [GRID] Цена выше уровня {level} | SELL"
                place_order_func(symbol, 'sell', TRADE_QUANTITY)

            send_telegram_message(message)


def execute_grid_strategy(df, send_telegram_message, place_order_func, symbol="BTCUSDT",
                          grid_size=50, num_levels=5):
    """
    Основная функция сеточной стратегии
    """
    if len(df) < grid_size:
        print(f"⏳ Нужно больше данных для сетки ({len(df)} / {grid_size})")
        return

    grid_info = calculate_grid_levels(df, grid_size=grid_size, num_levels=num_levels)
    print(f"📊 Уровни сетки: {grid_info['levels']}")

    detect_grid_signal(df, grid_info, send_telegram_message, place_order_func, symbol=symbol)