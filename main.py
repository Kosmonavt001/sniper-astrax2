import asyncio
import logging
import json
import os
from aiogram import Bot
from botTG import run_bot
from monitor import run_monitor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    logger.info("Запуск Solana Meme Coin Bot и Монитора...")
    directories = [
        'config', 
        'config/wallets', 
        'data', 
        'data/purchased_tokens', 
        'photo'
    ]
    
    for directory in directories:
        os.makedirs(directory, exist_ok=True)
        logger.info(f"Директория {directory} создана или уже существует")
    bot_config_path = 'config/bot_config.json'
    if not os.path.exists(bot_config_path):
        default_config = {
            "password": "admin123",
            "bot_token": "YOUR_BOT_TOKEN_HERE"
        }
        with open(bot_config_path, 'w') as f:
            json.dump(default_config, f, indent=2)
        logger.warning(f"Создан файл конфигурации по умолчанию: {bot_config_path}")
        logger.warning("Пожалуйста, добавьте ваш токен Telegram бота в этот файл!")
        return
    try:
        with open(bot_config_path, 'r') as f:
            bot_config = json.load(f)
        bot_token = bot_config.get('bot_token', '')
    except Exception as e:
        logger.error(f"Ошибка загрузки конфигурации бота: {e}")
        return
    if not bot_token or bot_token == "YOUR_BOT_TOKEN_HERE":
        logger.error("❌ Не найден токен бота в config/bot_config.json")
        logger.info("Добавьте ваш токен в файл config/bot_config.json")
        return
    bot = Bot(token=bot_token)
    try:
        await asyncio.gather(
            run_bot(bot),
            run_monitor(bot)
        )
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nБот остановлен")
    except Exception as e:
        print(f"Ошибка запуска: {e}")
