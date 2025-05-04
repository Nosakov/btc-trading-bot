import asyncio
import json
import logging
import websockets

logger = logging.getLogger(__name__)

class BinanceFuturesWebSocketManager:
    def __init__(self, symbol: str, interval: str, callback):
        self.symbol = symbol.lower()
        self.interval = interval
        self.callback = callback
        self.connected = False
        self.websocket = None

    async def start(self):
        stream = f"{self.symbol}@kline_{self.interval}"
        url = f"wss://stream.binancefuture.com/ws/{stream}"
        logger.info(f"🚀 Подключение к WebSocket: {url}")

        while True:
            try:
                async with websockets.connect(url) as ws:
                    self.websocket = ws
                    self.connected = True
                    logger.info("🔌 Соединение установлено")
                    await self._listen(ws)
            except Exception as e:
                logger.error(f"❌ Ошибка WebSocket: {e}")
                self.connected = False
                logger.info("🔄 Переподключение через 5 секунд...")
                await asyncio.sleep(5)

    async def _listen(self, ws):
        try:
            while True:
                message = await ws.recv()
                logger.debug("📩 Получено сырое сообщение: %s", message[:200] + "..." if len(message) > 200 else message)
                try:
                    msg = json.loads(message)
                    await self.callback(msg)
                except json.JSONDecodeError as ve:
                    logger.error("❌ Ошибка парсинга JSON: %s", ve)
                except Exception as e:
                    logger.error("❌ Ошибка в обработчике: %s", e)
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("⚠️ Соединение закрыто: %s", e)

    async def stop(self):
        if self.websocket:
            await self.websocket.close()
            self.connected = False
            logger.info("🛑 WebSocket остановлен")