from telegram import Bot
import asyncio

def create_notifier(bot_token, chat_id):
    bot = Bot(token=bot_token)

    async def send_message(msg):
        await bot.send_message(chat_id=chat_id, text=msg)

    return lambda msg: asyncio.run(send_message(msg))