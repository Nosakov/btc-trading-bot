import os
import time
import json
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException
from websocket_handler import BinanceWebSocketManager
from strategy import execute_strategy, execute_grid_strategy
from notifier import create_notifier

# === Настройки проекта ===
load_dotenv()
SYMBOL = "BTCUSDT"
INTERVAL = "1m"
TRADE_QUANTITY = 0.002  # Количество BTC для торговли

# === Инициализация API клиента ===
BINANCE_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_FUTURES_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET_KEY, testnet=True, requests_params={"timeout": 20})

send_telegram_message = create_notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

# === Хранилище данных ===
df_stream = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

# === Переменные управления позицией ===
active_position = None  # 'long' / 'short' / None
entry_price = 0.0
oco_set = False

STOP_LOSS_PERCENT = 0.005  # 0.5%
TAKE_PROFIT_PERCENT = 0.01  # 1%


# === Функция загрузки исторических данных ===
def load_historical_data(symbol="BTCUSDT", interval="1m", hours=24):
    print(f"⏳ Загрузка исторических данных за {hours} часов...")
    end_time = pd.Timestamp.now(tz='UTC')
    start_time = end_time - pd.Timedelta(hours=hours)

    start_ts = int(start_time.timestamp() * 1000)
    end_ts = int(end_time.timestamp() * 1000)

    try:
        klines = client.get_klines(symbol=symbol, interval=interval, startTime=start_ts, endTime=end_ts)

        if not klines:
            print("❌ Нет исторических данных за этот период.")
            return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

        df = pd.DataFrame(klines, columns=[
            'timestamp', 'Open', 'High', 'Low', 'Close', 'Volume',
            'close_time', 'quote_asset_volume', 'number_of_trades',
            'taker_buy_base_volume', 'taker_buy_quote_volume', 'ignore'
        ])

        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        df[['Open', 'High', 'Low', 'Close', 'Volume']] = df[
            ['Open', 'High', 'Low', 'Close', 'Volume']
        ].astype(float)
        df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
        print(f"✅ Загружено {len(df)} исторических свечей")
        return df

    except BinanceAPIException as e:
        print("❌ Ошибка при загрузке исторических данных:", e)
        send_telegram_message(f"❌ [HIST] Не удалось загрузить историю: {e}")
        return pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])


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

            print(f"📈 Куплено {quantity} {symbol} по {price:.2f}")
            message = f"✅ [BUY] Куплено {quantity} {symbol}\nЦена: {price:.2f}$\nTP: {take_profit:.2f}$\nSL: {stop_loss:.2f}$"
            send_telegram_message(message)

            active_position = 'long'
            entry_price = price
            oco_set = True

        elif side == 'sell' and active_position == 'long' and not oco_set:
            # Простая продажа без OCO
            order = client.order_market_sell(symbol=symbol, quantity=quantity)
            message = f"✅ [SELL] Продано {quantity} {symbol}\nЦена: {float(order['fills'][0]['price']):.2f}$"
            send_telegram_message(message)

            active_position = None
            entry_price = 0.0
            oco_set = False

        elif side == 'sell' and active_position is None:
            # Открытие шортовой позиции
            order = client.order_market_sell(symbol=symbol, quantity=quantity)
            price = float(order['fills'][0]['price'])
            take_profit = price * (1 - TAKE_PROFIT_PERCENT)
            stop_loss = price * (1 + STOP_LOSS_PERCENT)

            # Выставляем OCO
            oco_order = client.create_oco_order(
                symbol=symbol,
                side='BUY',
                quantity=quantity,
                price=round(take_profit, 2),
                stopPrice=round(stop_loss, 2),
                stopLimitPrice=round(stop_loss * 1.005, 2),
                stopLimitTimeInForce='GTC'
            )

            print(f"📉 Открытая шорт-позиция: {quantity} {symbol} по {price:.2f}")
            message = f"✅ [SHORT] Продано {quantity} {symbol}\nЦена: {price:.2f}$\nTP: {take_profit:.2f}$\nSL: {stop_loss:.2f}$"
            send_telegram_message(message)

            active_position = 'short'
            entry_price = price
            oco_set = True

        elif side == 'buy' and active_position == 'short' and not oco_set:
            # Закрытие шортовой позиции
            order = client.order_market_buy(symbol=symbol, quantity=quantity)
            message = f"✅ [COVER] Куплено {quantity} {symbol} для закрытия шорта\nЦена: {float(order['fills'][0]['price']):.2f}$"
            send_telegram_message(message)

            active_position = None
            entry_price = 0.0
            oco_set = False

        else:
            print("❌ Неизвестная сторона ордера или состояние позиции")
            return None

        return order

    except BinanceAPIException as e:
        print("❌ Ошибка Binance:", e)
        send_telegram_message(f"❌ [ОРДЕР] Ошибка: {e}")
        oco_set = False
    return None


# === Мониторинг активных ордеров ===
def monitor_active_orders(symbol="BTCUSDT"):
    global oco_set
    try:
        open_orders = client.get_open_orders(symbol=symbol)
        if open_orders:
            print(f"📊 Найдено {len(open_orders)} активных ордеров")
            for order in open_orders:
                print(f"🧾 Ордер ID: {order['orderId']} | Тип: {order['side']} | Цена: {order['price']}")
            oco_set = True
        else:
            print("✅ Нет активных ордеров")
            oco_set = False

    except BinanceAPIException as e:
        print("❌ Ошибка проверки ордеров:", e)


# === Обработка сообщений ===
def process_message(msg):
    global df_stream, active_position, entry_price, oco_set

    try:
        # Если сообщение — строка, парсим как JSON
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except json.JSONDecodeError as ve:
                print("❌ Ошибка парсинга JSON:", ve)
                return

        # Логируем всё пришедшее
        #print("📩 Полное сообщение:", msg)

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

        # Резервное управление позицией
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

        # Периодическая проверка ордеров
        if len(df_stream) % 5 == 0:
            monitor_active_orders(SYMBOL)

    except Exception as e:
        print(f"❌ Ошибка обработки сообщения: {e}")

def cancel_all_orders(symbol="BTCUSDT"):
    try:
        orders = client.get_open_orders(symbol=symbol)
        if orders:
            print(f"🚫 Отменяем {len(orders)} ордеров")
            for order in orders:
                client.cancel_order(symbol=symbol, orderId=order['orderId'])
                send_telegram_message(f"🚫 Ордер {order['orderId']} отменён")
        else:
            print("✅ Нет активных ордеров для отмены")
    except BinanceAPIException as e:
        print("❌ Ошибка отмены ордеров:", e)


# === Запуск бота ===
if __name__ == "__main__":
    print("🤖 Бот запущен...")
    # === Проверка API ключей ===
    if not BINANCE_API_KEY or not BINANCE_SECRET_KEY:
        print("❌ Не заданы API ключи")
        send_telegram_message("❌ Не заданы API ключи для Binance")
        exit(1)

    try:
        account_info = client.get_account()
        print("✅ Успешно подключено к тестовой сети")
        print("💼 Баланс:", account_info['balances'][0])
    except BinanceAPIException as e:
        print("❌ Ошибка подключения к тестовой сети:", e)

    cancel_all_orders(SYMBOL)
    # Загрузка исторических данных до запуска WebSocket
    historical_df = load_historical_data(SYMBOL, INTERVAL, hours=24)

    if not historical_df.empty:
        df_stream = pd.concat([df_stream, historical_df])
        df_stream.sort_index(inplace=True)
        df_stream = df_stream[~df_stream.index.duplicated()]
        print(f"📊 Исторические данные добавлены | Текущее количество свечей: {len(df_stream)}")

        # Можно сразу вызвать стратегию, если хватает данных
        if len(df_stream) >= 26:
            execute_strategy(df_stream, send_telegram_message, place_order, SYMBOL)
        if len(df_stream) >= 50:
            execute_grid_strategy(df_stream, send_telegram_message, place_order, SYMBOL)

    # Запуск WebSocket
    ws_manager = BinanceWebSocketManager(SYMBOL, INTERVAL, process_message)
    ws_manager.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ws_manager.stop()