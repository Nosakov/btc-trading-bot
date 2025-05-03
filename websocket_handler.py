from binance.websocket.spot.websocket_stream import SpotWebsocketStreamClient
import logging

logger = logging.getLogger(__name__)

class BinanceWebSocketManager:
    def __init__(self, symbol: str, interval: str, callback):
        self.symbol = symbol.lower()
        self.interval = interval
        self.callback = callback
        self.ws_client = None

    def start(self):
        logger.info(f"🚀 Запуск WebSocket: {self.symbol.upper()} | {self.interval}")
        self.ws_client = SpotWebsocketStreamClient(
            on_message=self._on_message,
            on_error=lambda ws, error: self._on_error(error),
            on_close=lambda ws, code, reason: self._on_close(code, reason),
            on_open=lambda ws: self._on_open(),
        )

        # Подписываемся на поток свечей
        self.ws_client.subscribe(stream=f"{self.symbol}@kline_{self.interval}")

    def _on_open(self):
        logger.info("🔌 Соединение открыто")

    def _on_message(self, ws, msg):
        logger.debug("📩 Получено сырое сообщение: %s", msg)
        try:
            self.callback(msg)
        except Exception as e:
            logger.error("❌ Ошибка в обработчике сообщения: %s", e)

    def _on_error(self, error):
        logger.error("❌ Ошибка WebSocket: %s", error)

    def _on_close(self, code=None, reason=None):
        logger.info(f"🔌 Соединение закрыто. Код: {code}, Причина: {reason}")

    def stop(self):
        if self.ws_client:
            self.ws_client.stop()
            logger.info("🛑 WebSocket остановлен")