from telegram import Bot
import asyncio
import logging

logger = logging.getLogger(__name__)

def create_notifier(bot_token, chat_id):
    bot = Bot(token=bot_token)

    async def send_message(msg):
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
            logger.info(f"📩 Сообщение отправлено в Telegram: {msg[:50]}...")
        except Exception as e:
            logger.error(f"[Telegram] Ошибка отправки сообщения: {e}", exc_info=True)

    # Для совместимости с синхронным кодом
    return lambda msg: asyncio.run(send_message(msg))