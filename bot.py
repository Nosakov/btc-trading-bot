import os
import time
import json
import asyncio
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client as BinanceClient
from binance.exceptions import BinanceAPIException
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from io import BytesIO
import mplfinance as mpf
import nest_asyncio


nest_asyncio.apply()

# === Настройки проекта ===
load_dotenv()
SYMBOL = "BTCUSDT"
INTERVAL = "3m"
TRADE_QUANTITY = 0.002  # Количество BTC для торговли

# === Инициализация API клиента (Testnet Futures) ===
BINANCE_FUTURES_API_KEY = os.getenv("BINANCE_FUTURES_API_KEY")
BINANCE_FUTURES_SECRET_KEY = os.getenv("BINANCE_FUTURES_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Подключаемся к тестовой сети
client = BinanceClient(
    api_key=BINANCE_FUTURES_API_KEY,
    api_secret=BINANCE_FUTURES_SECRET_KEY,
    testnet=True
)

# Импорты после инициализации
from websocket_handler import BinanceFuturesWebSocketManager
from strategy import execute_strategy, execute_grid_strategy
from notifier import create_notifier

send_telegram_message = create_notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

# === Хранилище данных ===
df_stream = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

# === Переменные управления позицией ===
active_position = None  # 'long' / 'short' / None
entry_price = 0.0
oco_set = False
position_closed_recently = False
last_position_close_time = 0
STOP_LOSS_PERCENT = 0.003  # 0.3%
TAKE_PROFIT_PERCENT = 0.005  # 0.5%

# === Функция загрузки исторических данных (Testnet Futures) ===
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
    global active_position, entry_price, oco_set, position_closed_recently, last_position_close_time
    try:
        latest_price = df_stream.iloc[-1]['Close']
        if position_closed_recently and time.time() - last_position_close_time < 60:
            print("⏳ Ждём перед новой сделкой...")
            return None

        # Сначала отменяем все старые ордера
        cancel_all_orders(symbol)

        if side == 'buy':
            order = client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='MARKET',
                quantity=quantity
            )
            take_profit = round(latest_price * (1 + TAKE_PROFIT_PERCENT), 2)
            stop_loss = round(latest_price * (1 - STOP_LOSS_PERCENT), 2)
            if take_profit <= 0 or stop_loss <= 0:
                print("⚠️ Неверные значения TP/SL — меньше или равно нулю")
                send_telegram_message("⚠️ [ОРДЕР] TP/SL не могут быть ≤ 0")
                return None

            # Take Profit
            client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='TAKE_PROFIT_MARKET',
                stopPrice=round(take_profit, 2),
                closePosition=True
            )

            # Stop Loss
            client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='STOP_MARKET',
                stopPrice=round(stop_loss, 2),
                closePosition=True
            )

            message = f"📈 [BUY] Куплено {quantity} {symbol}\nЦена: {latest_price:.2f}$\nTP: {take_profit:.2f}$\nSL: {stop_loss:.2f}$"
            send_telegram_message(message)
            active_position = 'long'
            entry_price = latest_price
            oco_set = True
            position_closed_recently = False

        elif side == 'sell' and active_position is None:
            # Продажа шортовой позиции
            order = client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity
            )
            take_profit = round(latest_price * (1 - TAKE_PROFIT_PERCENT), 2)
            stop_loss = round(latest_price * (1 + STOP_LOSS_PERCENT), 2)
            if take_profit <= 0 or stop_loss <= 0:
                print("⚠️ Неверные значения TP/SL — меньше или равно нулю")
                send_telegram_message("⚠️ [ОРДЕР] TP/SL не могут быть ≤ 0")
                return None

            # Take Profit
            client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='TAKE_PROFIT_MARKET',
                stopPrice=round(take_profit, 2),
                closePosition=True
            )

            # Stop Loss
            client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='STOP_MARKET',
                stopPrice=round(stop_loss, 2),
                closePosition=True
            )

            message = f"📉 [SHORT] Продано {quantity} {symbol}\nЦена: {latest_price:.2f}$\nTP: {take_profit:.2f}$\nSL: {stop_loss:.2f}$"
            send_telegram_message(message)
            active_position = 'short'
            entry_price = latest_price
            oco_set = True
            position_closed_recently = False

        elif side == 'sell' and active_position == 'long':
            # Простая продажа без OCO
            order = client.futures_create_order(
                symbol=symbol,
                side='SELL',
                type='MARKET',
                quantity=quantity
            )
            message = f"📉 Продано {quantity} {symbol} по {latest_price:.2f}"
            send_telegram_message(message)
            active_position = None
            entry_price = 0.0
            oco_set = False
            position_closed_recently = True
            last_position_close_time = time.time()

            # Отменяем оставшиеся ордера
            cancel_all_orders(symbol)

        elif side == 'buy' and active_position == 'short':
            # Закрытие шортовой позиции
            order = client.futures_create_order(
                symbol=symbol,
                side='BUY',
                type='MARKET',
                quantity=quantity
            )
            message = f"📈 [COVER] Куплено {quantity} {symbol} для закрытия шорта\nЦена: {latest_price:.2f}"
            send_telegram_message(message)
            active_position = None
            entry_price = 0.0
            oco_set = False
            position_closed_recently = True
            last_position_close_time = time.time()

            # Отменяем оставшиеся ордера
            cancel_all_orders(symbol)

        else:
            print("❌ Неизвестная сторона ордера или состояние")
            return None

        return order

    except BinanceAPIException as e:
        print("❌ Ошибка Binance:", e)
        send_telegram_message(f"❌ [ОРДЕР] Ошибка: {e}")
        oco_set = False
    return None


# === Отмена всех ордеров типа SL/TP ===
def cancel_all_orders(symbol="BTCUSDT"):
    try:
        open_orders = client.futures_get_all_orders(symbol=symbol, limit=50)
        stop_orders = [
            o for o in open_orders
            if o['status'] == 'NEW' and o['type'] in ['TAKE_PROFIT_MARKET', 'STOP_MARKET']
        ]
        if stop_orders:
            print(f"🛑 Отменяем {len(stop_orders)} ордеров")
            for order in stop_orders:
                client.futures_cancel_order(symbol=symbol, orderId=order['orderId'])
            send_telegram_message(f"❌ [ORDERS] {len(stop_orders)} ордеров отменено")
        else:
            print("✅ Нет активных ордеров SL/TP")
    except BinanceAPIException as e:
        print("❌ Ошибка при отмене ордеров:", e)
        send_telegram_message(f"❌ [ORDERS] Не удалось отменить ордера: {e}")


# === Проверка наличия активных ордеров ===
def has_active_orders(symbol="BTCUSDT"):
    try:
        orders = client.futures_get_all_orders(symbol=symbol, limit=50)
        active = [o for o in orders if o['status'] == 'NEW' and o['type'] in ['TAKE_PROFIT_MARKET', 'STOP_MARKET']]
        return len(active) > 0
    except BinanceAPIException as e:
        print("❌ Ошибка проверки ордеров:", e)
        return False


# === Обработка сообщений из WebSocket ===
async def process_message(msg):
    global df_stream, active_position, entry_price, oco_set
    try:
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except json.JSONDecodeError:
                return

        # Сервисное сообщение
        if 'result' in msg and msg['result'] is None:
            print("📡 Сервисное сообщение (subscribed)")
            return

        # Проверяем тип события
        if not (isinstance(msg, dict) and msg.get('e') == 'kline'):
            print("📡 Пропущено несвечное сообщение")
            return

        kline = msg.get('k', {})
        timestamp = int(kline.get('t'))
        open_price = float(kline.get('o'))
        high = float(kline.get('h'))
        low = float(kline.get('l'))
        close_price = float(kline.get('c'))
        volume = float(kline.get('v'))
        is_closed = kline.get('x')

        print(f"🕯️ Свеча: {SYMBOL} | Закрыта: {is_closed} | Цена: {close_price:.2f}")

        # Только если свеча закрыта
        if not is_closed:
            return

        # Проверяем дубликаты
        candle_time = pd.to_datetime(timestamp, unit='ms')
        if candle_time in df_stream.index:
            print("🔁 Эта свеча уже есть — пропускаем")
            return

        # Добавляем новую свечу
        df_new = pd.DataFrame([{
            'Open': open_price,
            'High': high,
            'Low': low,
            'Close': close_price,
            'Volume': volume
        }], index=[candle_time])
        df_combined = pd.concat([df_stream, df_new]).drop_duplicates()
        df_combined.sort_index(inplace=True)
        df_stream = df_combined.tail(1000).copy()

        print(f"📊 Текущее количество свечей: {len(df_stream)}")

        # Если есть активные ордера → запрещаем новые сделки
        if has_active_orders(SYMBOL):
            print("🚫 Нельзя открывать новую позицию: есть активные ордера")
            return

        # Вызываем стратегии
        if len(df_stream) >= 26:
            await execute_strategy(df_stream, send_telegram_message, place_order, SYMBOL)
        if len(df_stream) >= 50:
            await execute_grid_strategy(df_stream, send_telegram_message, place_order, SYMBOL)

        # Периодическая проверка ордеров
        if len(df_stream) % 5 == 0:
            monitor_active_orders(SYMBOL)
    except Exception as e:
        print(f"❌ Ошибка обработки сообщения: {e}")


# === Мониторинг активных ордеров ===
def monitor_active_orders(symbol="BTCUSDT"):
    global oco_set
    try:
        open_orders = client.futures_get_all_orders(symbol=symbol, limit=50)
        open_orders = [o for o in open_orders if o['status'] == 'NEW']
        if open_orders:
            print(f"📊 Найдено {len(open_orders)} активных ордеров")
            for order in open_orders:
                print(f"🧾 ID: {order['orderId']} | Цена: {order['price']}")
            oco_set = True
        else:
            print("✅ Нет активных ордеров")
            oco_set = False
    except BinanceAPIException as e:
        print(f"❌ Ошибка проверки ордеров: {e}")
        oco_set = False


# === Команды Telegram ===
async def get_positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        positions = client.futures_position_information(symbol=SYMBOL)
        for pos in positions:
            if float(pos['positionAmt']) != 0:
                await update.message.reply_text(
                    f"📊 Позиция: {pos['positionSide']} | Размер: {pos['positionAmt']} | Цена входа: {pos['entryPrice']}"
                )
    except BinanceAPIException as e:
        await update.message.reply_text(f"❌ Ошибка получения позиций: {e}")


async def get_orders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        orders = client.futures_get_all_orders(symbol=SYMBOL, limit=50)
        if orders:
            for order in orders:
                await update.message.reply_text(
                    f"🧾 ID: {order['orderId']} | Сторона: {order['side']} | Цена: {order['price']} | Статус: {order['status']}"
                )
        else:
            await update.message.reply_text("✅ Нет активных ордеров")
    except BinanceAPIException as e:
        await update.message.reply_text(f"❌ Ошибка получения ордеров: {e}")


async def check_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        balance = client.futures_account_balance()
        for item in balance:
            if item['asset'] == 'USDT':
                await update.message.reply_text(f"💼 Баланс USDT: {item['balance']} USDT")
    except BinanceAPIException as e:
        await update.message.reply_text(f"❌ Ошибка получения баланса: {e}")


def generate_grid_chart(df, grid_levels=None):
    if len(df) < 50 or not grid_levels:
        return None
    df = df.tail(50).copy()
    buffer = BytesIO()

    lines = []
    for level in grid_levels:
        lines.append({
            'y1': float(level),
            'y2': float(level),
            'x1': df.index[0],  # Начало диапазона
            'x2': df.index[-1],  # Конец диапазона
            'color': 'gray',
            'linestyle': '--'
        })

    mpf.plot(
        df,
        type='candle',
        style='yahoo',
        title=f"{SYMBOL} - Последние 50 свечей",
        alines={'alines': lines},
        volume=False,
        savefig=dict(fname=buffer, dpi=100, bbox_inches='tight'),
        figratio=(10, 6),
        figscale=1.5
    )
    buffer.seek(0)
    return buffer


async def send_grid_chart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    grid_levels = await execute_grid_strategy(df_stream, None, None, SYMBOL, dry_run=True)
    chart_buffer = generate_grid_chart(df_stream, list(map(float, grid_levels)))
    if chart_buffer:
        await context.bot.send_photo(chat_id=update.effective_chat.id, photo=chart_buffer)
    else:
        await update.message.reply_text("❌ Не удалось сгенерировать график")


# === Асинхронный запуск WebSocket ===
async def run_websocket():
    ws_manager = BinanceFuturesWebSocketManager(SYMBOL, INTERVAL, process_message)
    await ws_manager.start()


# === Асинхронный запуск Telegram бота ===
async def run_telegram_bot():
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("positions", get_positions))
    app.add_handler(CommandHandler("orders", get_orders))
    app.add_handler(CommandHandler("gridchart", send_grid_chart))
    app.add_handler(CommandHandler("balance", check_balance))
    print("📡 Telegram бот запущен")
    await app.run_polling()


# === Запуск бота ===
if __name__ == "__main__":
    print("🤖 Бот запущен...")

    # === Проверка API ключей ===
    if not BINANCE_FUTURES_API_KEY or not BINANCE_FUTURES_SECRET_KEY:
        print("❌ Не заданы API ключи")
        send_telegram_message("❌ Не заданы API ключи для Binance")
        exit(1)

    # Проверка подключения к Testnet Futures
    try:
        balance = client.futures_account_balance()
        print("✅ Успешно подключено к тестовой сети")
        print("💼 Баланс фьючерсного аккаунта:", balance[0])
    except BinanceAPIException as e:
        print("❌ Ошибка авторизации:", e)
        exit(1)

    # Загрузка исторических данных до запуска WebSocket
    historical_df = load_historical_data(SYMBOL, INTERVAL, hours=24)
    if not historical_df.empty:
        df_stream = pd.concat([df_stream, historical_df]).drop_duplicates()
        df_stream.sort_index(inplace=True)
        print(f"📊 Исторические данные добавлены | Текущее количество свечей: {len(df_stream)}")
    else:
        print("⚠️ Нет исторических данных")

    async def main():
        bot_task = run_telegram_bot()
        ws_task = run_websocket()
        await asyncio.gather(bot_task, ws_task)

    asyncio.run(main())