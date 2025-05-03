import os
import time
import json
import pandas as pd
from dotenv import load_dotenv
from binance.client import Client as BinanceClient

load_dotenv()
SYMBOL = "BTCUSDT"
INTERVAL = "1m"

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

client = BinanceClient(BINANCE_API_KEY, BINANCE_SECRET_KEY)

# Импорты после загрузки переменных окружения
from websocket_handler import BinanceWebSocketManager
from strategy import execute_strategy
from notifier import create_notifier

send_telegram_message = create_notifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)

df_stream = pd.DataFrame(columns=['Open', 'High', 'Low', 'Close', 'Volume'])

def process_message(msg):
    global df_stream

    try:
        # Парсинг сообщения
        if isinstance(msg, str):
            msg = json.loads(msg)

        if not (isinstance(msg, dict) and msg.get('e') == 'kline'):
            return

        kline = msg.get('k', {})
        timestamp = int(kline.get('t'))
        open_price = float(kline.get('o'))
        high = float(kline.get('h'))
        low = float(kline.get('l'))
        close_price = float(kline.get('c'))
        volume = float(kline.get('v'))
        is_closed = kline.get('x')

        print(f"蜡 Свеча: {kline.get('s')} | Цена: {close_price:.2f} | Закрыта: {is_closed}")

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
        df_stream = df_combined[~df_combined.index.duplicated()]

        # Теперь можно вызывать стратегию
        if len(df_stream) >= 26:
            execute_strategy(df_stream, send_telegram_message, SYMBOL)

    except Exception as e:
        print("❌ Ошибка обработки сообщения:", e)

if __name__ == "__main__":
    print("🤖 Бот запущен...")

    ws_manager = BinanceWebSocketManager(SYMBOL, INTERVAL, process_message)
    ws_manager.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ws_manager.stop()