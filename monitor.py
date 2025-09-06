import asyncio
import logging
import time
import os
import glob
import json
import random
import requests
from datetime import datetime, timezone
from aiogram import Bot

from wallet_manager import WalletManager

# --- Константы ---
# Официальные URL для DexScreener API
# Исправлены URL (убраны лишние пробелы в конце)
DEXSCREENER_TOKEN_PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
DEXSCREENER_TOKEN_PAIRS_URL = "https://api.dexscreener.com/tokens/v1/solana/"

# Разрешенные DEX (по названию пула или маркета)
ALLOWED_DEX = ["raydium", "pumpswap"]

# Максимальное время существования токена (в минутах)
MAX_AGE_MINUTES = 50  # 12 часов
# Желаемое максимальное время (для логирования)
DESIRED_MAX_AGE_MINUTES = 2  # 1 час

# Список для отслеживания уже обработанных токенов
# Используем множество для быстрой проверки
processed_tokens = set()

# Параметры запроса
REQUEST_DELAY = 2.0  # Задержка между запросами (секунды)
MAX_RETRIES = 3

# --- НОВОЕ: ID группы и топика ---
GROUP_ID = -1003071618300
TOPIC_ID = 12
NEWLY_FOUND_TOKENS_FILE = 'data/newly_found_tokens.json' # Новый файл для хранения информации о найденных токенах

# --- ИЗМЕНЕННАЯ: Функция для сохранения информации о найденных токенах ---
def save_found_tokens_info(tokens_list: list):
    """
    Сохраняет информацию о найденных токенах в файл.
    Возвращает список токенов, которые были добавлены в этот раз (новые).
    """
    newly_added_tokens = []
    try:
        # Создаем директорию data, если её нет
        os.makedirs(os.path.dirname(NEWLY_FOUND_TOKENS_FILE), exist_ok=True)

        # Загружаем существующие данные (если есть)
        existing_tokens = []
        existing_token_addresses = set() # Для быстрой проверки дубликатов
        if os.path.exists(NEWLY_FOUND_TOKENS_FILE):
            try:
                with open(NEWLY_FOUND_TOKENS_FILE, 'r') as f:
                    loaded_data = json.load(f)
                    if isinstance(loaded_data, list):
                        existing_tokens = loaded_data
                        # Заполняем множество адресов для быстрой проверки
                        existing_token_addresses = {token.get("address") for token in existing_tokens if token.get("address")}
            except json.JSONDecodeError:
                logging.warning(f"Ошибка чтения {NEWLY_FOUND_TOKENS_FILE}, создается новый файл.")
            except Exception as e:
                logging.warning(f"Неожиданная ошибка при чтении {NEWLY_FOUND_TOKENS_FILE}: {e}")

        # Получаем текущую дату и время для новых записей
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        # Подготавливаем новые записи, избегая дубликатов
        new_token_entries = []
        for token_data in tokens_list:
            token_address = token_data.get("address")
            if not token_address:
                continue
            
            # Проверяем, есть ли токен уже в существующих
            if token_address in existing_token_addresses:
                 # logging.debug(f"Токен {token_address} уже существует в файле, пропускаем.")
                continue
            
            # Добавляем временную метку, если её нет
            if "discovered_at" not in token_data:
                token_data["discovered_at"] = timestamp_str

            new_token_entries.append(token_data)
            newly_added_tokens.append(token_data) # Добавляем в список новых
            existing_token_addresses.add(token_address) # Добавляем в множество для следующих проверок в этом цикле

        # Если есть новые токены для добавления
        if new_token_entries:
            # Добавляем новые записи в начало списка
            updated_tokens = new_token_entries + existing_tokens

            # Ограничиваем количество записей, например, до 50 последних
            if len(updated_tokens) > 50:
                updated_tokens = updated_tokens[:50]

            # Сохраняем обновленный список
            with open(NEWLY_FOUND_TOKENS_FILE, 'w') as f:
                json.dump(updated_tokens, f, indent=2)

            logging.info(f"Добавлено {len(new_token_entries)} новых токенов в {NEWLY_FOUND_TOKENS_FILE}")
        else:
             logging.debug("Нет новых токенов для добавления в файл.")

    except Exception as e:
        logging.error(f"Ошибка сохранения информации о найденных токенах: {e}")
        
    return newly_added_tokens # Возвращаем список токенов, добавленных в этот раз

def check_token_scam_risk(token_address: str) -> dict:
    """
    Проверяет токен на риск скама через DexScreener API
    Возвращает информацию о токене и его риске
    """
    try:
        # Используем DexScreener API для получения информации о парах токена
        url = f"{DEXSCREENER_TOKEN_PAIRS_URL}{token_address}"
        response = requests.get(url, timeout=15)
        if response.status_code == 200:
            data = response.json()
            # Проверяем, есть ли пары для токена
            if not data:
                return {
                    "risk_level": "HIGH",
                    "risk_reason": "Нет пулов на DEX",
                    "message": "Токен не найден на основных DEX",
                    "has_pairs": False,
                    "age_minutes": 0,
                    "token_symbol": "UNKNOWN"
                }
            # Ищем пары на разрешенных DEX
            allowed_pairs = []
            for pair in data:
                dex_id = pair.get("dexId", "").lower()
                if any(allowed_dex in dex_id for allowed_dex in ALLOWED_DEX):
                    allowed_pairs.append(pair)
            if not allowed_pairs:
                return {
                    "risk_level": "HIGH",
                    "risk_reason": "Токен не торгуется на разрешенных DEX",
                    "message": "Токен не торгуется на Raydium или PumpSwap",
                    "has_pairs": True,
                    "age_minutes": 0,
                    "token_symbol": "UNKNOWN"
                }
            # Берем первую подходящую пару для анализа
            first_pair = allowed_pairs[0]
            # Получаем информацию о токене
            base_token = first_pair.get("baseToken", {})
            quote_token = first_pair.get("quoteToken", {})
            # Проверяем ликвидность
            liquidity_usd = first_pair.get("liquidity", {}).get("usd", 0)
            volume_24h = first_pair.get("volume", {}).get("h24", 0)
            # Проверяем время создания (с проверкой на корректность)
            created_at = first_pair.get("pairCreatedAt", 0)
            current_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            if created_at and 0 < created_at < current_time_ms:
                # Корректное время создания
                created_time = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                time_diff = datetime.now(timezone.utc) - created_time
                age_hours = time_diff.total_seconds() / 3600
                age_minutes = age_hours * 60
            else:
                # Некорректное время создания - используем 0
                age_hours = 0
                age_minutes = 0
                logging.warning(f"Некорректное время создания для токена {token_address}: {created_at}")
            
            risk_score = 0
            risk_reasons = []
            # Риск 1: Низкая ликвидность
            if liquidity_usd < 1000:
                risk_score += 3
                risk_reasons.append(f"Низкая ликвидность (${liquidity_usd:,.2f})")
            # Риск 2: Низкий объем торговли
            if volume_24h < 100:
                risk_score += 2
                risk_reasons.append(f"Низкий объем торговли (${volume_24h:,.2f})")
            # Риск 3: Слишком новый токен (менее 1 часа)
            if age_hours < 1:
                risk_score += 2
                risk_reasons.append(f"Очень новый токен ({age_hours:.1f} ч)")
            # Риск 4: Нет информации о токене
            if not base_token.get("name") or not base_token.get("symbol"):
                risk_score += 4
                risk_reasons.append("Нет метаданных токена")
            # Риск 5: Плохое соотношение покупок/продаж
            txns = first_pair.get("txns", {})
            h1_buys = txns.get("h1", {}).get("buys", 0)
            h1_sells = txns.get("h1", {}).get("sells", 0)
            if h1_sells > 0 and h1_buys > 0:
                buy_sell_ratio = h1_buys / h1_sells
                if buy_sell_ratio < 0.5:
                    risk_score += 2
                    risk_reasons.append("Высокая продажная активность")
            # Определение уровня риска
            if risk_score >= 6:
                risk_level = "HIGH"
                risk_message = "Высокий риск скама"
            elif risk_score >= 3:
                risk_level = "MEDIUM"
                risk_message = "Средний риск скама"
            else:
                risk_level = "LOW"
                risk_message = "Низкий риск скама"
            return {
                "risk_level": risk_level,
                "risk_reason": ", ".join(risk_reasons) if risk_reasons else "Нет проблем",
                "message": risk_message,
                "has_pairs": True,
                "liquidity_usd": liquidity_usd,
                "volume_24h": volume_24h,
                "age_hours": age_hours,
                "age_minutes": age_minutes,
                "token_name": base_token.get("name", "Unknown"),
                "token_symbol": base_token.get("symbol", "UNKNOWN"),
                "quote_token": quote_token.get("symbol", "SOL"),
                "dexes": [pair.get("dexId", "Unknown") for pair in allowed_pairs[:3]],
                "price_change_h24": first_pair.get("priceChange", {}).get("h24", 0),
                "price_usd": first_pair.get("priceUsd", 0),
                "created_at_timestamp": created_at
            }
        else:
            return {
                "risk_level": "HIGH",
                "risk_reason": "Не удалось получить данные от DexScreener",
                "message": "Ошибка получения информации о токене",
                "has_pairs": False,
                "age_minutes": 0,
                "token_symbol": "UNKNOWN"
            }
    except Exception as e:
        logging.error(f"Ошибка проверки токена {token_address} на скам: {e}")
        return {
            "risk_level": "UNKNOWN",
            "risk_reason": "Ошибка сети",
            "message": "Не удалось проверить токен",
            "has_pairs": False,
            "age_minutes": 0,
            "token_symbol": "UNKNOWN"
        }

def get_new_tokens_from_dexscreener():
    """
    Получает новые токены с помощью DexScreener API.
    Возвращает список адресов новых токенов и список данных о них.
    """
    new_tokens_addresses = []
    new_tokens_data = [] # Список словарей с данными токенов
    try:
        # Получаем последние токены через профили
        response = requests.get(
            DEXSCREENER_TOKEN_PROFILES_URL,
            timeout=15
        )
        if response.status_code == 200:
            data = response.json()
            # Фильтруем только Solana токены
            solana_tokens = [token for token in data if token.get("chainId") == "solana"]
            temp_tokens_for_saving = [] # Список данных токенов, найденных за эту итерацию
            for token in solana_tokens:
                token_address = token.get("tokenAddress")
                if not token_address:
                    continue
                # Проверяем, что токен уже не обработан
                if token_address in processed_tokens:
                    continue
                # Проверяем, что токен прошел фильтры скама
                scam_info = check_token_scam_risk(token_address)
                if not scam_info["has_pairs"]:
                    continue
                # Безопасно получаем symbol токена
                token_symbol = scam_info.get("token_symbol", "UNKNOWN")
                # Используем возраст из scam_info
                age_minutes = scam_info.get("age_minutes", 0)
                # Для отладки выводим информацию о времени создания
                logging.debug(f"Токен {token_symbol}: created_at={scam_info.get('created_at_timestamp', 0)}, age_minutes={age_minutes}")
                risk_level = scam_info.get("risk_level", "UNKNOWN")
                if risk_level != "HIGH":
                    if age_minutes <= MAX_AGE_MINUTES:
                        if age_minutes <= DESIRED_MAX_AGE_MINUTES:
                            logging.info(f"🎯 Найден молодой токен {token_symbol}: {token_address} (возраст: {age_minutes:.1f} мин)")
                        else:
                            logging.info(f"🆕 Найден токен {token_symbol}: {token_address} (возраст: {age_minutes:.1f} мин)")
                        
                        new_tokens_addresses.append(token_address)
                        processed_tokens.add(token_address)

                        # --- НОВОЕ: Собираем данные токена для сохранения ---
                        token_name = scam_info.get("token_name", "Unknown")
                        price_usd = scam_info.get("price_usd", 0)
                        # Собираем полный словарь данных для сохранения
                        token_data_for_file = {
                            "address": token_address,
                            "name": token_name,
                            "symbol": token_symbol,
                            "price_usd": price_usd,
                            # "discovered_at" будет добавлен в save_found_tokens_info
                        }
                        temp_tokens_for_saving.append(token_data_for_file)

                    else:
                        logging.info(f"⏰ Пропускаем старый токен {token_symbol}: {age_minutes:.1f} мин > {MAX_AGE_MINUTES} мин")
            
            # --- НОВОЕ: Сохраняем все найденные токены этой итерации ---
            if temp_tokens_for_saving:
                 newly_added_tokens = save_found_tokens_info(temp_tokens_for_saving)
                 # Формируем список данных для возврата (только новых токенов)
                 # Это нужно, чтобы buy_token получил только те, что действительно новые
                 # Можно вернуть адреса и данные, но для упрощения вернем адреса и соберем данные внутри monitor_new_tokens при необходимости
                 # Или можно переделать логику, чтобы buy_token работал с адресами, а данные брал из файла или из scam_info
                 # Пока что вернем адреса и собранные данные этой итерации
                 new_tokens_data = temp_tokens_for_saving # Или newly_added_tokens, если нужно только последние

        else:
            logging.error(f"DexScreener API error {response.status_code}: {response.text}")
    except Exception as e:
        logging.error(f"Ошибка при получении новых токенов из DexScreener: {e}")
    
    # Возвращаем адреса и собранные данные
    return new_tokens_addresses, new_tokens_data

async def monitor_new_tokens(bot: Bot):
    """Мониторит появление новых токенов с помощью DexScreener."""
    logging.info("Запуск монитора новых токенов...")
    logging.info(f"Используется источник данных: DexScreener API")
    logging.info(f"Отслеживаются DEX: {', '.join(ALLOWED_DEX)}")
    logging.info(f"Максимальный возраст токена: {MAX_AGE_MINUTES} минут")
    logging.info(f"Желаемый максимальный возраст: {DESIRED_MAX_AGE_MINUTES} минут")
    
    while True:
        try:
            logging.info("Начало проверки новых токенов через DexScreener...")
            # Получаем список новых токенов из DexScreener
            # new_tokens - это список адресов
            # new_tokens_data - это список словарей с данными
            new_tokens, new_tokens_data = get_new_tokens_from_dexscreener()
            
            if new_tokens:
                # --- НОВОЕ: Отправляем ОДНО сообщение в группу со списком всех новых токенов ---
                try:
                    # Загружаем информацию о ВСЕХ недавно найденных токенах из файла
                    # (или можно использовать new_tokens_data, если она содержит нужные данные)
                    all_recent_tokens_info = []
                    if os.path.exists(NEWLY_FOUND_TOKENS_FILE):
                        try:
                            with open(NEWLY_FOUND_TOKENS_FILE, 'r') as f:
                                loaded_data = json.load(f)
                                if isinstance(loaded_data, list):
                                    # Берем токены, найденные в этой итерации
                                    # Предполагаем, что они вставлены в начало
                                    # Простой способ: возьмем первые N, где N - количество новых токенов этой итерации
                                    # Более надежный: сопоставим по адресам
                                    # Но проще использовать new_tokens_data, собранную в get_new_tokens_from_dexscreener
                                    # all_recent_tokens_info = loaded_data[:len(new_tokens)] 
                                    all_recent_tokens_info = new_tokens_data # Используем данные, собранные в get_new_tokens
                                else:
                                    logging.warning(f"{NEWLY_FOUND_TOKENS_FILE} содержит не список.")
                        except json.JSONDecodeError:
                            logging.error(f"Ошибка чтения {NEWLY_FOUND_TOKENS_FILE} для отправки в группу.")
                        except Exception as e:
                            logging.error(f"Неожиданная ошибка при чтении {NEWLY_FOUND_TOKENS_FILE} для отправки в группу: {e}")

                    if all_recent_tokens_info:
                        message_lines = ["🚀 <b>Новые токены найдены!</b>"]
                        for token_info in all_recent_tokens_info:
                            try:
                                # --- Преобразование price_usd в float ---
                                price_usd_value = token_info.get('price_usd', 0)
                                if isinstance(price_usd_value, str):
                                    price_usd_float = float(price_usd_value)
                                else:
                                    price_usd_float = float(price_usd_value)
                                price_usd_formatted = f"{price_usd_float:.6f}"
                            except (ValueError, TypeError):
                                logging.warning(f"Невозможно преобразовать цену {token_info.get('price_usd')} в число для токена {token_info.get('address')}. Используется 'N/A'.")
                                price_usd_formatted = "N/A"

                            message_lines.append(
                                f"\n 🪙 <b>{token_info.get('name', 'Unknown')} ({token_info.get('symbol', 'UNKNOWN')})</b>\n"
                                f"📬 Адрес: <code>{token_info.get('address', 'N/A')}</code>\n"
                                f"💰 Цена: ${price_usd_formatted}\n"
                                f"🕒 Обнаружен: {token_info.get('discovered_at', 'N/A')}\n"
                                f"------------------------"
                            )
                        # Убираем последний разделитель "---"
                        if message_lines and message_lines[-1] == "---":
                            message_lines.pop()
                        message_text = "\n".join(message_lines) + "\n\n#new_tokens #solana #memecoin"

                        await bot.send_message(
                            chat_id=GROUP_ID,
                            message_thread_id=TOPIC_ID, # Указываем ID топика
                            text=message_text,
                            parse_mode="HTML"
                        )
                        logging.info(f"Сообщение о {len(all_recent_tokens_info)} новых токенах отправлено в группу {GROUP_ID}, топик {TOPIC_ID}")
                    else:
                        logging.warning("Нет информации о недавно найденных токенах для отправки в группу.")
                        
                except Exception as e:
                    logging.error(f"Ошибка отправки сообщения в группу о новых токенах: {e}")

                # --- Старая логика покупки ---
                # Используем адреса токенов для покупки
                for token_address in new_tokens: 
                    logging.info(f"🚀 Пытаемся купить токен: {token_address}")
                    wm = WalletManager()
                    user_config_files = glob.glob('config/wallets/*.json')
                    for config_file in user_config_files:
                        try:
                            user_id = int(os.path.basename(config_file).split('.')[0])
                            user_wallets = wm.get_user_wallets(user_id)
                            for wallet_name in user_wallets.keys():
                                from trader import buy_token
                                await buy_token(user_id, wallet_name, token_address, bot)
                        except Exception as e:
                            logging.error(f"Ошибка обработки пользователя из файла {config_file}: {e}")
            else:
                logging.info("Новые токены не найдены")

            # Проверка целей продажи для ВСЕХ пользователей и кошельков
            wm = WalletManager()
            user_config_files = glob.glob('config/wallets/*.json')
            for config_file in user_config_files:
                try:
                    user_id = int(os.path.basename(config_file).split('.')[0])
                    user_wallets = wm.get_user_wallets(user_id)
                    for wallet_name in user_wallets.keys():
                        from trader import check_and_sell_tokens
                        await check_and_sell_tokens(user_id, wallet_name, bot)
                except Exception as e:
                    logging.error(f"Ошибка проверки продаж для пользователя из файла {config_file}: {e}")

            # Увеличиваем интервал проверки
            check_interval = 30  # Проверяем каждые 30 секунд
            logging.info(f"Ожидание {check_interval} секунд перед следующей проверкой...")
            await asyncio.sleep(check_interval)
        except Exception as e:
            logging.error(f"Ошибка в цикле мониторинга: {e}")
            wait_time = 60  # При ошибке ждем 60 секунд
            logging.info(f"Ожидание {wait_time} секунд перед повторной попыткой...")
            await asyncio.sleep(wait_time)

async def run_monitor(bot: Bot):
    """Функция для запуска монитора."""
    await monitor_new_tokens(bot)
