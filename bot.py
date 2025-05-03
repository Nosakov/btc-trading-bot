import os
import time
import json
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException

# === Настройки проекта ===
load_dotenv()
SYMBOL = "BTCUSDT"
INTERVAL = "1m"
TRADE_QUANTITY = 0.002  # Количество BTC для торговли

# === Инициализация API клиента ===
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET_KEY)

# Импорты после инициализации
from websocket_handler import BinanceWebSocketManager
from strategy import execute_strategy, execute_grid_strategy
from notifier import create_notifier

send_telegram_message = create_notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

# === Хранилище данных ===
df_stream = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

# === Переменные управления позицией ===
active_position = None  # 'long' / 'short' / None
entry_price = 0.0
oco_set = False

STOP_LOSS_PERCENT = 0.005  # 0.5%
TAKE_PROFIT_PERCENT = 0.01  # 1%


# === Функция размещения ордера с TP и SL ===
def place_order(symbol, side, quantity):
    global active_position, entry_price, oco_set

    try:
        if side == 'buy':
            order = client.order_market_buy(symbol=symbol, quantity=quantity)
            price = float(order['fills'][0]['price'])
            take_profit = price * (1 + TAKE_PROFIT_PERCENT)
            stop_loss = price * (1 - STOP_LOSS_PERCENT)

            # Выставляем OCO
            oco_order = client.create_oco_order(
                symbol=symbol,
                side='SELL',
                quantity=quantity,
                price=round(take_profit, 2),
                stopPrice=round(stop_loss, 2),
                stopLimitPrice=round(stop_loss * 0.995, 2),
                stopLimitTimeInForce='GTC'
            )

            oco_set = True  # Теперь OCO установлен
            active_position = 'long'
            entry_price = price

        elif side == 'sell' and active_position == 'long' and not oco_set:
            # Если OCO не был установлен, продаем просто
            order = client.order_market_sell(symbol=symbol, quantity=quantity)
            active_position = None
            entry_price = 0.0
            oco_set = False

        elif side == 'sell' and active_position is None:
            # Выставляем шорт и инвертированный OCO
            order = client.order_market_sell(symbol=symbol, quantity=quantity)
            price = float(order['fills'][0]['price'])
            take_profit = price * (1 - TAKE_PROFIT_PERCENT)
            stop_loss = price * (1 + STOP_LOSS_PERCENT)

            oco_order = client.create_oco_order(
                symbol=symbol,
                side='BUY',
                quantity=quantity,
                price=round(take_profit, 2),
                stopPrice=round(stop_loss, 2),
                stopLimitPrice=round(stop_loss * 1.005, 2),
                stopLimitTimeInForce='GTC'
            )

            oco_set = True
            active_position = 'short'
            entry_price = price

        elif side == 'buy' and active_position == 'short' and not oco_set:
            order = client.order_market_buy(symbol=symbol, quantity=quantity)
            active_position = None
            entry_price = 0.0
            oco_set = False

        else:
            print("❌ Неизвестная сторона ордера")
            return None

        return order

    except BinanceAPIException as e:
        print("❌ Ошибка Binance:", e)
        send_telegram_message(f"❌ [ОРДЕР] Ошибка: {e}")
        oco_set = False
    return None


def monitor_active_orders(symbol="BTCUSDT"):
    global oco_set

    try:
        open_orders = client.get_open_orders(symbol=symbol)

        if open_orders:
            print(f"📊 Найдено {len(open_orders)} активных ордеров")
            for order in open_orders:
                print(
                    f"🧾 Ордер ID: {order['orderId']} | Тип: {order['side']} | Цена: {order['price']}")

            oco_set = True
        else:
            print("✅ Нет активных ордеров")
            oco_set = False

    except BinanceAPIException as e:
        print("❌ Ошибка проверки ордеров:", e)
        #send_telegram_message(f"❌ [ОРДЕР] Ошибка при получении активных ордеров: {e}")


# === Обработка сообщений ===
def process_message(msg):
    global df_stream, active_position, entry_price

    try:
        # Если сообщение — строка, парсим как JSON
        if isinstance(msg, str):
            #print("📩 Получена строка, пробуем распарсить как JSON...")
            try:
                msg = json.loads(msg)
            except json.JSONDecodeError as ve:
                print("❌ Ошибка парсинга JSON:", ve)
                return

        # Логируем всё пришедшее
        # print("📩 Полное сообщение:", msg)

        # Сервисные сообщения
        if 'result' in msg and msg['result'] is None:
            print("📡 Сервисное сообщение (subscribed)")
            return

        # Проверяем тип события
        if not (isinstance(msg, dict) and msg.get('e') == 'kline'):
            print("📡 Пропущено несвечное сообщение")
            return

        kline = msg.get('k', {})
        symbol = kline.get('s')
        timestamp = int(kline.get('t'))
        open_price = float(kline.get('o'))
        high = float(kline.get('h'))
        low = float(kline.get('l'))
        close_price = float(kline.get('c'))
        volume = float(kline.get('v'))
        is_closed = kline.get('x')

        print(f"🕯️ Свеча: {symbol} | Закрыта: {is_closed} | Цена: {close_price:.2f}")

        # Только если свеча закрыта
        if not is_closed:
            return

        # Проверяем дубликаты
        candle_time = pd.to_datetime(timestamp, unit='ms')
        if candle_time in df_stream.index:
            print("🔁 Эта свеча уже есть — пропускаем")
            return

        # Добавляем в DataFrame
        df_new = pd.DataFrame([{
            'Open': open_price,
            'High': high,
            'Low': low,
            'Close': close_price,
            'Volume': volume
        }], index=[candle_time])

        df_combined = pd.concat([df_stream, df_new])
        df_combined.sort_index(inplace=True)
        df_combined = df_combined[~df_combined.index.duplicated()]

        df_stream = df_combined.copy()

        print(f"📊 Текущее количество свечей: {len(df_stream)}")

        # Вызываем стратегии
        if len(df_stream) >= 26:
            execute_strategy(df_stream, send_telegram_message, place_order, SYMBOL)

        if len(df_stream) >= 50:
            execute_grid_strategy(df_stream, send_telegram_message, place_order, SYMBOL)

        if len(df_stream) % 5 == 0:
            monitor_active_orders(SYMBOL)

        # Резервное управление позицией (на случай сбоя OCO)
        latest_price = df_stream.iloc[-1]['Close']

        if active_position == 'long':
            current_return = (latest_price - entry_price) / entry_price
            if current_return <= -(STOP_LOSS_PERCENT + 0.001):
                print("🛑 [Резерв] STOP LOSS достигнут (LONG)")
                place_order(SYMBOL, 'sell', TRADE_QUANTITY)

            elif current_return >= TAKE_PROFIT_PERCENT + 0.01:
                print("🎯 [Резерв] TAKE PROFIT достигнут (LONG)")
                place_order(SYMBOL, 'sell', TRADE_QUANTITY)

        elif active_position == 'short':
            current_return = (entry_price - latest_price) / entry_price
            if current_return <= -(STOP_LOSS_PERCENT + 0.001):
                print("🛑 [Резерв] STOP LOSS достигнут (SHORT)")
                place_order(SYMBOL, 'buy', TRADE_QUANTITY)

            elif current_return >= TAKE_PROFIT_PERCENT + 0.01:
                print("🎯 [Резерв] TAKE PROFIT достигнут (SHORT)")
                place_order(SYMBOL, 'buy', TRADE_QUANTITY)

    except Exception as e:
        print("❌ Ошибка обработки сообщения:", e)


# === Запуск бота ===
if __name__ == "__main__":
    print("🤖 Бот запущен...")

    ws_manager = BinanceWebSocketManager(SYMBOL, INTERVAL, process_message)
    ws_manager.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ws_manager.stop()