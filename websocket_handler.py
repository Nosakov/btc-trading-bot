import json
import logging
import websocket

logger = logging.getLogger(__name__)

class BinanceWebSocketManager:
    def __init__(self, symbol: str, interval: str, callback):
        self.symbol = symbol.lower()
        self.interval = interval
        self.callback = callback
        self.ws = None

    def start(self):
        logger.info(f"🚀 Запуск WebSocket: {self.symbol.upper()} | {self.interval}")
        stream = f"{self.symbol}@kline_{self.interval}"

        # Проверка URL потока
        url = f"wss://stream.binancefuture.com/ws/{stream}"
        print(f"🔗 Подписываемся на поток: {url}")

        # Запуск WebSocket
        self.ws = websocket.WebSocketApp(
            url,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open
        )

        # Можно добавить опции reconnect
        self.ws.run_forever()

    def _on_open(self, ws):
        logger.info("🔌 Соединение открыто")

    def _on_message(self, ws, message):
        try:
            msg = json.loads(message)
            logger.debug("📩 Получено сообщение: %s", msg)

            # Передаем сообщение в обработчик стратегии
            self.callback(msg)

        except json.JSONDecodeError as ve:
            logger.error("❌ Ошибка парсинга JSON: %s", ve)
        except Exception as e:
            logger.error("❌ Ошибка в обработчике сообщения: %s", e)

    def _on_error(self, ws, error):
        logger.error("❌ Ошибка WebSocket: %s", error)

    def _on_close(self, ws, close_status_code, close_msg):
        logger.info("🔌 Соединение закрыто")
        logger.info(f"📝 Код: {close_status_code}, Сообщение: {close_msg}")

    def stop(self):
        if self.ws:
            self.ws.close()
            logger.info("🛑 WebSocket остановлен")