# trader.py
import base64
import logging
import json
import os
import time
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import Transaction
import base58
import requests
from wallet_manager import WalletManager
from filters import check_liquidity_and_sellability, is_potential_scam
from solana_utils import get_sol_usdt_price, get_token_price_usdt, get_token_metadata, get_jupiter_swap_transaction
from aiogram import Bot
import asyncio
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Исправлен URL без лишних пробелов
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
PURCHASED_TOKENS_DIR = 'data/purchased_tokens'
# URL Jupiter API также исправлены
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"

def get_user_config(user_id: int, wallet_name: str) -> dict:
    """Получает конфигурацию пользователя"""
    wm = WalletManager()
    return wm.get_wallet_config(user_id, wallet_name)

async def get_token_current_price(token_address: str) -> float:
    """
    Получает текущую цену токена в USDT через DexScreener API
    """
    try:
        # Используем DexScreener API для получения текущей цены
        # Исправлен URL без лишних пробелов
        url = f"https://api.dexscreener.com/tokens/v1/solana/{token_address}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            # Проверяем, является ли data списком
            if isinstance(data, list):
                pairs = data
            elif isinstance(data, dict) and "pairs" in data:
                # Если это словарь, извлекаем список пар
                pairs = data["pairs"]
            else:
                logger.warning(f"Неожиданный формат данных от DexScreener для {token_address}: {type(data)}")
                pairs = []

            # Ищем пару с SOL в качестве квотируемого токена
            for pair in pairs:
                quote_token = pair.get("quoteToken", {})
                if quote_token.get("symbol", "").upper() == "SOL":
                    price_usd = pair.get("priceUsd", "0")
                    try:
                        return float(price_usd)
                    except (TypeError, ValueError):
                        continue

            # Если не нашли пару с SOL, попробуем получить цену через Jupiter (или другую функцию)
            # get_token_price_usdt из solana_utils.py тоже использует DexScreener, поэтому это дублирование.
            # Лучше просто вернуть 0.0 или попробовать другой метод, если он есть.
            logger.warning(f"Не найдена пара SOL для токена {token_address} через DexScreener. Попробуем альтернативный метод.")
            # Попробуем альтернативный метод из solana_utils
            return get_token_price_usdt(token_address)

        logger.warning(f"Не удалось получить текущую цену для токена {token_address} через DexScreener. Status: {response.status_code}")
        return 0.0
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении текущей цены для {token_address}: {e}")
        return 0.0
    except Exception as e:
        logger.error(f"Ошибка получения текущей цены для {token_address}: {e}")
        return 0.0

async def get_purchased_tokens_info(user_id: int, wallet_name: str) -> list:
    """
    Получает информацию о купленных токенах с актуальной ценой и профитом
    """
    purchased_tokens_file = os.path.join(PURCHASED_TOKENS_DIR, f"{user_id}_{wallet_name}.json")
    tokens_info = []

    if not os.path.exists(purchased_tokens_file):
        return tokens_info

    try:
        with open(purchased_tokens_file, 'r') as f:
            purchased_tokens = json.load(f)

        for token_address, token_data in purchased_tokens.items():
            current_price = await get_token_current_price(token_address)

            if current_price <= 0:
                logger.warning(f"Не удалось получить текущую цену для токена {token_address}")
                # Можно добавить логику для отображения "N/A" или последней известной цены
                # Пока что пропускаем токен, если цена не получена
                continue

            # --- ИЗМЕНЕНО: Получение цены покупки ---
            # В новой системе цена покупки хранится в 'purchase_price_usdt'
            purchase_price = token_data.get('purchase_price_usdt', 0)

            if purchase_price <= 0:
                logger.warning(f"Цена покупки для токена {token_address} некорректна: {purchase_price}")
                continue

            # Рассчитываем профит
            profit_percent = ((current_price - purchase_price) / purchase_price) * 100 if purchase_price > 0 else 0
            multiplier = current_price / purchase_price if purchase_price > 0 else 0

            # Определяем статус
            status = "🔴" # По умолчанию
            if multiplier >= 4:
                status = "🟢 x4+ (Цель достигнута)"
            elif multiplier >= 3:
                status = "🟢 x3+ (Цель достигнута)"
            elif multiplier >= 2:
                status = "🟢 x2+ (Цель достигнута)"
            elif multiplier >= 1.5:
                status = "🟡 x1.5"
            elif multiplier >= 1.2:
                status = "🟡 x1.2"
            elif multiplier >= 1.1:
                status = "🟡 x1.1"
            elif multiplier > 1.0:
                status = "🟡 >1.0"
            elif multiplier == 1.0:
                status = "⚪ 1.0"
            else: # multiplier < 1.0
                status = "🔴 <1.0"

            tokens_info.append({
                "address": token_address,
                "name": token_data.get('name', 'Unknown'),
                "symbol": token_data.get('symbol', 'UNKNOWN'),
                "purchase_price": purchase_price,
                "current_price": current_price,
                "profit_percent": profit_percent,
                "multiplier": multiplier,
                "status": status,
                # --- ИЗМЕНЕНО: Получение цели продажи ---
                # В новой системе используется одна цель
                "target_price": token_data.get('target_price_usdt', 0),
                "tx_signature": token_data.get('tx_signature', '')
            })

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON из файла {purchased_tokens_file}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при получении информации о купленных токенах из {purchased_tokens_file}: {e}")

    return tokens_info

async def buy_token(user_id: int, wallet_name: str, token_address: str, bot: Bot):
    """Покупает токен, если он проходит фильтры."""
    logger.info(f"Попытка покупки токена {token_address} для {user_id}/{wallet_name}")
    wm = WalletManager()
    # 1. Получение метаданных токена для проверки имени/символа
    token_metadata = get_token_metadata(token_address)
    token_name = token_metadata.get('name', 'Unknown')
    token_symbol = token_metadata.get('symbol', 'UNKNOWN')
    # 2. Фильтрация
    if is_potential_scam(token_address, token_name, token_symbol):
        logger.warning(f"Токен {token_address} ({token_name}) заблокирован фильтрами.")
        # Отправка уведомления пользователю
        try:
            await bot.send_message(
                user_id,
                f"❌ Покупка токена <b>{token_name} ({token_symbol})</b> отменена.\n"
                f"Причина: Токен заблокирован фильтрами (потенциально скам).",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id} о блокировке токена: {e}")
        return False
    # 3. Получение конфигурации пользователя и кошелька
    wallet_config = wm.get_wallet_config(user_id, wallet_name)
    if not wallet_config:
        logger.error(f"Конфигурация кошелька {wallet_name} для пользователя {user_id} не найдена.")
        return False

    # --- ИЗМЕНЕНО: Получение процента от баланса ---
    trade_percentage = wallet_config.get('trade_percentage', 1.0) # По умолчанию 1%
    if trade_percentage <= 0 or trade_percentage > 100:
        logger.error(f"Неверный процент от баланса для кошелька {wallet_name}: {trade_percentage}%")
        return False

    # 4. Получение данных кошелька
    private_key_b58 = wm.get_wallet_private_key(user_id, wallet_name)
    wallet_address = wm.get_wallet_address(user_id, wallet_name)
    if not private_key_b58 or not wallet_address:
        logger.error(f"Данные кошелька {wallet_name} для пользователя {user_id} не найдены.")
        return False
    try:
        keypair = Keypair.from_bytes(base58.b58decode(private_key_b58))
    except Exception as e:
        logger.error(f"Ошибка декодирования приватного ключа для {wallet_name}: {e}")
        return False

    # 5. Получение текущей цены токена в USDT
    current_price_usdt = await get_token_current_price(token_address)
    if current_price_usdt <= 0:
        logger.error(f"Не удалось получить текущую цену токена {token_address} в USDT.")
        # Отправка уведомления пользователю
        try:
            await bot.send_message(
                user_id,
                f"❌ Покупка токена <b>{token_name} ({token_symbol})</b> отменена.\n"
                f"Причина: Не удалось получить текущую цену токена.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id} об ошибке цены: {e}")
        return False

    # --- ИЗМЕНЕНО: Расчет суммы покупки на основе процента от баланса ---
    # Получаем баланс кошелька
    wallet_balance_sol = wm.get_wallet_balance_solana(wallet_address)
    if wallet_balance_sol <= 0:
        logger.error(f"Баланс кошелька {wallet_name} ({wallet_address}) нулевой или не может быть получен.")
        # Отправка уведомления пользователю
        try:
            await bot.send_message(
                user_id,
                f"❌ Покупка токена <b>{token_name} ({token_symbol})</b> отменена.\n"
                f"Причина: Баланс кошелька нулевой или не может быть получен.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id} об ошибке баланса: {e}")
        return False

    # Получаем цену SOL в USDT
    sol_price_usdt = get_sol_usdt_price()
    if sol_price_usdt <= 0:
        logger.error("Не удалось получить цену SOL в USDT.")
        # Отправка уведомления пользователю
        try:
            await bot.send_message(
                user_id,
                f"❌ Покупка токена <b>{token_name} ({token_symbol})</b> отменена.\n"
                f"Причина: Не удалось получить цену SOL в USDT.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id} об ошибке цены SOL: {e}")
        return False

    # Рассчитываем сумму покупки в SOL
    wallet_balance_usdt = wallet_balance_sol * sol_price_usdt
    purchase_amount_usdt = wallet_balance_usdt * (trade_percentage / 100.0)
    purchase_amount_sol = purchase_amount_usdt / sol_price_usdt

    logger.info(f"Кошелек {wallet_name}: Баланс {wallet_balance_sol:.6f} SOL ({wallet_balance_usdt:.6f} USDT). "
                f"Покупка на {trade_percentage}% = {purchase_amount_sol:.6f} SOL ({purchase_amount_usdt:.6f} USDT).")

    # 6. Создание и отправка транзакции покупки через Jupiter
    try:
        # --- ИЗМЕНЕНО: Используем рассчитанную сумму в lamports ---
        sol_to_spend_lamports = int(purchase_amount_sol * 1_000_000_000) # Преобразование в lamports

        # Получаем транзакцию для обмена SOL -> Токен
        swap_transaction = get_jupiter_swap_transaction(
            input_mint="So11111111111111111111111111111111111111112",  # SOL
            output_mint=token_address,
            amount=sol_to_spend_lamports, # Передаем в lamports
            slippage=100,  # Увеличен до 1% для новых токенов
            user_public_key=wallet_address
        )
        if not swap_transaction:
            error_msg = f"❌ Не удалось создать маршрут обмена для токена {token_address}. " \
                        f"Возможно, токен слишком новый или отсутствует ликвидность."
            logger.warning(error_msg)
            # Отправка уведомления пользователю
            try:
                await bot.send_message(
                    user_id,
                     f"❌ Покупка токена <b>{token_name} ({token_symbol})</b> не удалась.\n"
                     f"Причина: Не найден маршрут обмена (возможно, нет ликвидности или токен слишком новый).",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю {user_id} о неудачной покупке: {e}")
            return False

        # Декодируем сырую транзакцию из base64
        # Добавляем обработку возможных проблем с base64
        # В функции buy_token, замените блок декодирования:
        # Декодируем сырую транзакцию из base64
        # В функции buy_token, замените блок декодирования:
        # Декодируем сырую транзакцию из base64
        try:
            tx_data = swap_transaction['tx'].strip()
            
            # Убедимся, что строка base64 имеет правильный формат
            # Добавляем padding если нужно
            missing_padding = len(tx_data) % 4
            if missing_padding:
                tx_data += '=' * (4 - missing_padding)
            
            # Проверяем, является ли строка корректной base64
            import base64
            try:
                raw_txn = base64.b64decode(tx_data)
                logger.info(f"Успешно декодирована транзакция длиной {len(raw_txn)} байт")
            except Exception as e:
                logger.error(f"Ошибка декодирования base64: {e}")
                # Попробуем обработать как есть
                try:
                    raw_txn = base64.b64decode(tx_data.encode('utf-8'))
                except Exception as e2:
                    logger.error(f"Вторая попытка декодирования также не удалась: {e2}")
                    # Если все еще неудачно, попробуем обрезать и добавить padding
                    tx_cleaned = tx_data.replace('\n', '').replace(' ', '')
                    missing_pad = len(tx_cleaned) % 4
                    if missing_pad:
                        tx_cleaned += '=' * (4 - missing_pad)
                    try:
                        raw_txn = base64.b64decode(tx_cleaned)
                        logger.info(f"Успешно декодирована после очистки транзакция длиной {len(raw_txn)} байт")
                    except Exception as e3:
                        logger.error(f"Третья попытка декодирования также не удалась: {e3}")
                        raise
        except Exception as e:
            logger.error(f"Ошибка при декодировании транзакции: {e}")
            raise

        # Создаем объект транзакции из байтов
        transaction = Transaction.from_bytes(raw_txn)
        # Подписываем транзакцию своим ключом
        transaction.sign([keypair])
        # Преобразуем подписанную транзакцию обратно в сырые байты для отправки
        raw_signed_txn = bytes(transaction)
        # Отправляем подписанную транзакцию
        client = Client(SOLANA_RPC_URL)
        tx_sig = client.send_raw_transaction(raw_signed_txn)
        logger.info(f"Транзакция покупки отправлена: {tx_sig}")
        # Проверка подтверждения транзакции
        client.confirm_transaction(tx_sig, commitment=Confirmed)
        logger.info(f"Покупка токена {token_address} для {user_id}/{wallet_name} завершена успешно.")

        # 7. Сохранение информации о покупке
        # --- ИЗМЕНЕНО: Рассчитываем цели продажи на основе процента прибыли ---
        profit_percentage = wallet_config.get('profit_percentage', 100.0) # По умолчанию 100% (x2)
        multiplier_for_target = 1 + (profit_percentage / 100.0)
        target_price_usdt = current_price_usdt * multiplier_for_target

        purchased_tokens_file = os.path.join(PURCHASED_TOKENS_DIR, f"{user_id}_{wallet_name}.json")
        purchased_tokens = {}
        if os.path.exists(purchased_tokens_file):
            try:
                with open(purchased_tokens_file, 'r') as f:
                    purchased_tokens = json.load(f)
            except json.JSONDecodeError:
                logger.warning(f"Ошибка чтения файла {purchased_tokens_file}, создается новый.")

        # --- ИЗМЕНЕНО: Сохраняем рассчитанные цели ---
        purchased_tokens[token_address] = {
            "name": token_name,
            "symbol": token_symbol,
            "purchase_price_usdt": current_price_usdt, # Цена покупки = текущая цена
            "current_price_usdt": current_price_usdt,
            "target_price_usdt": target_price_usdt, # Новая цель
            "profit_percentage_target": profit_percentage, # Сохраняем цель для отображения
            "purchase_amount_usdt": purchase_amount_usdt, # Сохраняем сумму покупки
            "purchase_time": str(time.time()),
            "tx_signature": str(tx_sig)
        }

        # Убедимся, что директория существует
        os.makedirs(os.path.dirname(purchased_tokens_file), exist_ok=True)
        with open(purchased_tokens_file, 'w') as f:
            json.dump(purchased_tokens, f, indent=2)

        # Отправка уведомления в Telegram
        try:
            await bot.send_message(
                user_id,
                f"✅ Куплен токен <b>{token_name} ({token_symbol})</b>\n"
                f"💰 Цена покупки: <b>{current_price_usdt:.6f} USDT</b>\n"
                f"💸 Потрачено: <b>{purchase_amount_usdt:.6f} USDT</b> ({trade_percentage}% от баланса)\n"
                f"🎯 Цель продажи: <b>{target_price_usdt:.6f} USDT</b> (+{profit_percentage}% / x{multiplier_for_target:.2f})",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при покупке токена {token_address} для {user_id}/{wallet_name}: {e}")
        # Отправка уведомления об ошибке
        try:
            await bot.send_message(
                user_id,
                f"❌ Ошибка покупки токена {token_address}:\n{str(e)}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об ошибке пользователю {user_id}: {e}")
        return False

async def check_and_sell_tokens(user_id: int, wallet_name: str, bot: Bot):
    """Проверяет купленные токены и продает, если достигнуты цели."""
    logger.info(f"Проверка целей продажи для {user_id}/{wallet_name}")
    purchased_tokens_file = os.path.join(PURCHASED_TOKENS_DIR, f"{user_id}_{wallet_name}.json")
    if not os.path.exists(purchased_tokens_file):
        logger.info(f"Нет купленных токенов для {user_id}/{wallet_name}")
        return
    try:
        with open(purchased_tokens_file, 'r') as f:
            purchased_tokens = json.load(f)
        updated_tokens = {}
        sold_any = False
        for token_address, token_data in purchased_tokens.items():
            current_price_usdt = await get_token_current_price(token_address)
            if current_price_usdt <= 0:
                logger.warning(f"Не удалось получить цену для купленного токена {token_address}. Пропуск.")
                updated_tokens[token_address] = token_data  # Сохраняем старые данные
                continue
            # --- ИЗМЕНЕНО: Получаем цену покупки и цель продажи ---
            purchase_price = token_data.get('purchase_price_usdt', 0)
            target_price = token_data.get('target_price_usdt', 0)

            # Обновляем текущую цену в файле
            token_data['current_price_usdt'] = current_price_usdt
            updated_tokens[token_address] = token_data

            # --- ИЗМЕНЕНО: Проверка цели продажи ---
            if current_price_usdt >= target_price and target_price > 0:
                logger.info(f"Цель продажи достигнута для {token_address} ({current_price_usdt} >= {target_price}). Продажа...")
                sold = await sell_token(user_id, wallet_name, token_address, bot)
                if sold:
                    sold_any = True
                    # Токен удаляется из updated_tokens, так как он продан
                else:
                    # Если продажа не удалась, оставляем токен в списке
                    updated_tokens[token_address] = token_data
            # else:
                # logger.debug(f"Цель продажи не достигнута для {token_address}. Текущая: {current_price_usdt}, Цель: {target_price}")

        # Сохраняем обновленный список (без проданных токенов)
        if sold_any:
            with open(purchased_tokens_file, 'w') as f:
                json.dump(updated_tokens, f, indent=2)
            logger.info(f"Список купленных токенов для {user_id}/{wallet_name} обновлен.")
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON из файла {purchased_tokens_file}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при проверке целей продажи для {user_id}/{wallet_name}: {e}")

async def sell_token(user_id: int, wallet_name: str, token_address: str, bot: Bot) -> bool:
    """Продает токен через Jupiter API"""
    logger.info(f"Попытка продажи токена {token_address} для {user_id}/{wallet_name}")
    wm = WalletManager()
    # Получение данных кошелька
    private_key_b58 = wm.get_wallet_private_key(user_id, wallet_name)
    wallet_address = wm.get_wallet_address(user_id, wallet_name)
    if not private_key_b58 or not wallet_address:
        logger.error(f"Данные кошелька {wallet_name} для пользователя {user_id} не найдены.")
        return False
    try:
        keypair = Keypair.from_bytes(base58.b58decode(private_key_b58))
    except Exception as e:
        logger.error(f"Ошибка декодирования приватного ключа для {wallet_name}: {e}")
        return False
    try:
        # Получаем текущую цену токена
        current_price = await get_token_current_price(token_address)
        if current_price <= 0:
             logger.error(f"Не удалось получить текущую цену токена {token_address} для продажи.")
             return False
        # --- TODO: Здесь нужно правильно рассчитать количество токенов для продажи. ---
        # Пока что для теста используем 1 (в smallest units). Нужно получить баланс токена.
        # Для этого можно использовать RPC вызов getTokenAccountsByOwner или аналог из библиотеки.
        # Пример (упрощенный, без обработки ошибок):
        # from solana.rpc.types import TokenAccountOpts
        # token_accounts = client.get_token_accounts_by_owner(Pubkey.from_string(wallet_address), TokenAccountOpts(mint=Pubkey.from_string(token_address)))
        # if token_accounts.value:
        #     # Получить баланс из token_accounts.value[0].account.data.parsed['info']['tokenAmount']['amount']
        #     token_balance_smallest_unit = int(token_accounts.value[0].account.data.parsed['info']['tokenAmount']['amount'])
        # else:
        #     token_balance_smallest_unit = 0
        # amount_to_sell = token_balance_smallest_unit

        # Для начала используем фиксированное значение или 100% баланса токена (если известен)
        # Пока что используем 1 lamport для тестирования, чтобы избежать ошибок.
        # amount_to_sell_smallest_unit = 1 # Это не будет работать для реальной продажи

        # Отправляем уведомление, что продажа пока не реализована полностью
        token_name = "Unknown"
        token_symbol = "UNKNOWN"
        purchase_price = 0.0
        purchase_amount = 0.0
        profit_percent_target = 0.0
        actual_profit_percent = 0.0

        purchased_tokens_file = os.path.join(PURCHASED_TOKENS_DIR, f"{user_id}_{wallet_name}.json")
        if os.path.exists(purchased_tokens_file):
            try:
                with open(purchased_tokens_file, 'r') as f:
                    purchased_tokens = json.load(f)
                token_data = purchased_tokens.get(token_address, {})
                token_name = token_data.get('name', 'Unknown')
                token_symbol = token_data.get('symbol', 'UNKNOWN')
                purchase_price = token_data.get('purchase_price_usdt', 0)
                purchase_amount = token_data.get('purchase_amount_usdt', 0)
                profit_percent_target = token_data.get('profit_percentage_target', 100.0)
                # Рассчитываем фактическую прибыль
                if purchase_price > 0:
                    actual_profit_percent = ((current_price - purchase_price) / purchase_price) * 100
                else:
                    actual_profit_percent = 0.0
            except Exception as e:
                logger.error(f"Ошибка получения данных токена из файла {purchased_tokens_file} для уведомления: {e}")

        # Отправка уведомления в Telegram о том, что продажа не реализована
        try:
            await bot.send_message(
                user_id,
                f"⚠️ Продажа токена <b>{token_name} ({token_symbol})</b> была инициирована, "
                f"но автоматическая продажа пока не реализована в коде.\n"
                f"💰 Цена покупки: <b>{purchase_price:.6f} USDT</b>\n"
                f"💰 Текущая цена: <b>{current_price:.6f} USDT</b>\n"
                f"💸 Потрачено: <b>{purchase_amount:.6f} USDT</b>\n"
                f"🎯 Цель была: +{profit_percent_target}%\n"
                f"📈 Фактическая прибыль: <b>{actual_profit_percent:.2f}%</b>\n"
                f"🛠️ Необходимо реализовать логику получения баланса токена и формирования транзакции продажи.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id} о необходимости реализации продажи: {e}")
        # Возвращаем False, так как реальная продажа не произошла
        return False

        # --- Ниже идет закомментированный код для реальной продажи ---
        # swap_transaction = get_jupiter_swap_transaction(
        #     input_mint=token_address,
        #     output_mint="So11111111111111111111111111111111111111112",  # SOL
        #     amount=amount_to_sell_smallest_unit,  # Это должно быть реальное количество токенов
        #     slippage=100,  # 1%
        #     user_public_key=wallet_address
        # )
        # if not swap_transaction:
        #     logger.error(f"Не удалось получить транзакцию swap для продажи {token_address}")
        #     return False
        # # Декодируем сырую транзакцию из base64
        # raw_txn = base64.b64decode(swap_transaction['tx'])
        # # Создаем объект транзакции из байтов
        # transaction = Transaction.from_bytes(raw_txn)
        # # Подписываем транзакцию своим ключом
        # transaction.sign([keypair])
        # # Преобразуем подписанную транзакцию обратно в сырые байты для отправки
        # raw_signed_txn = bytes(transaction)
        # # Отправляем подписанную транзакцию
        # client = Client(SOLANA_RPC_URL)
        # tx_sig = client.send_raw_transaction(raw_signed_txn)
        # logger.info(f"Транзакция продажи отправлена: {tx_sig}")
        # # Проверка подтверждения транзакции
        # client.confirm_transaction(tx_sig, commitment=Confirmed)
        # logger.info(f"Продажа токена {token_address} для {user_id}/{wallet_name} завершена успешно.")
        # # ... (остальная логика уведомлений) ...
        # return True
    except Exception as e:
        logger.error(f"Ошибка при продаже токена {token_address} для {user_id}/{wallet_name}: {e}")
        # Отправка уведомления об ошибке
        try:
            await bot.send_message(
                user_id,
                f"❌ Ошибка инициации продажи токена {token_address}:\n{str(e)}",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об ошибке пользователю {user_id}: {e}")
        return False
