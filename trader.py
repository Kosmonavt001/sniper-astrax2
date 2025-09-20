import base64
import logging
import json
import os
import time
import re
import asyncio
import base58
import requests
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solana.rpc.types import TokenAccountOpts
from solders.pubkey import Pubkey
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from wallet_manager import WalletManager
from filters import check_liquidity_and_sellability, is_potential_scam, send_token_analysis_to_group
from solana_utils import get_sol_usdt_price, get_token_price_usdt, get_token_metadata
from aiogram import Bot

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
GROUP_CHAT_ID = "-1003071618300"
TOPIC_MESSAGE_THREAD_ID = 61
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
PURCHASED_TOKENS_DIR = 'data/purchased_tokens'
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://lite-api.jup.ag/swap/v1/swap"
url_pay = "https://t.me/KosmoNavt001"

def get_user_config(user_id: int, wallet_name: str) -> dict:
    wm = WalletManager()
    return wm.get_wallet_config(user_id, wallet_name)

async def get_token_current_price(token_address: str) -> float:
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                pairs = data
            elif isinstance(data, dict) and "pairs" in data:
                pairs = data["pairs"]
            else:
                logger.warning(f"Неожиданный формат данных от DexScreener для {token_address}: {type(data)}")
                pairs = []

            for pair in pairs:
                quote_token = pair.get("quoteToken", {})
                if quote_token.get("symbol", "").upper() == "SOL":
                    price_usd = pair.get("priceUsd", "0")
                    try:
                        return float(price_usd)
                    except (TypeError, ValueError):
                        continue

            logger.warning(f"Не найдена пара SOL для токена {token_address} через DexScreener.")
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
                continue

            purchase_price = token_data.get('purchase_price_usdt', 0)

            if purchase_price <= 0:
                logger.warning(f"Цена покупки для токена {token_address} некорректна: {purchase_price}")
                continue

            profit_percent = ((current_price - purchase_price) / purchase_price) * 100 if purchase_price > 0 else 0
            multiplier = current_price / purchase_price if purchase_price > 0 else 0

            status = "🔴"
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
            else:
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
                "target_price": token_data.get('target_price_usdt', 0),
                "tx_signature": token_data.get('tx_signature', '')
            })

    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON из файла {purchased_tokens_file}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при получении информации о купленных токенах из {purchased_tokens_file}: {e}")

    return tokens_info

def decode_jupiter_transaction(tx_data: str) -> bytes:
    try:
        cleaned_data = tx_data.strip().replace('\n', '').replace(' ', '').replace('=', '')
        
        padding = len(cleaned_data) % 4
        if padding:
            cleaned_data += '=' * (4 - padding)
        
        decoded = base64.b64decode(cleaned_data)
        logger.info(f"Успешно декодировано {len(decoded)} байт транзакции")
        return decoded
        
    except Exception as e:
        logger.error(f"Ошибка декодирования транзакции: {e}")
        try:
            base64_pattern = r'[A-Za-z0-9+/]+={0,2}'
            matches = re.findall(base64_pattern, tx_data)
            if matches:
                cleaned = ''.join(matches)
                padding = len(cleaned) % 4
                if padding:
                    cleaned += '=' * (4 - padding)
                return base64.b64decode(cleaned)
            raise ValueError("No valid base64 found in transaction data")
        except Exception as e2:
            logger.error(f"Альтернативный метод декодирования также не удался: {e2}")
            raise

def get_jupiter_swap_transaction_improved(input_mint: str, output_mint: str, amount: int,
                                        slippage: int, user_public_key: str) -> dict:
    """
    Получает base64-кодированную транзакцию свопа через Jupiter Swap API.
    """
    try:
        # 1. Получаем quote
        quote_url = f"{JUPITER_QUOTE_URL}?inputMint={input_mint}&outputMint={output_mint}&amount={amount}&slippageBps={slippage}"
        quote_response = requests.get(quote_url, timeout=10)

        if quote_response.status_code != 200:
            logger.error(f"Ошибка получения quote: {quote_response.status_code}")
            return None

        quote_data = quote_response.json()

        # 2. Формируем payload для получения транзакции свопа
        swap_payload = {
            "userPublicKey": user_public_key,
            "quoteResponse": quote_data,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
            "useSharedAccounts": False,
        }

        # 3. Отправляем запрос на получение транзакции
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        swap_response = requests.post(JUPITER_SWAP_URL, json=swap_payload, headers=headers, timeout=10)

        if swap_response.status_code != 200:
            # Пробуем с useSharedAccounts=True как fallback
            swap_payload["useSharedAccounts"] = True
            swap_response = requests.post(JUPITER_SWAP_URL, json=swap_payload, headers=headers, timeout=10)

        if swap_response.status_code != 200:
            logger.error(f"Ошибка получения swap транзакции: {swap_response.status_code}")
            logger.error(f"Response: {swap_response.text}")
            return None

        return swap_response.json()  # Возвращает { "swapTransaction": "...", ... }

    except Exception as e:
        logger.error(f"Ошибка в функции получения транзакции свопа: {e}")
        return None
       
def get_jupiter_swap_transaction(input_mint: str, output_mint: str, amount: int, 
                               slippage: int, user_public_key: str) -> dict:
    return get_jupiter_swap_transaction_improved(input_mint, output_mint, amount, 
                                               slippage, user_public_key)

async def buy_token(user_id: int, wallet_name: str, token_address: str, bot: Bot):
    logger.info(f"Попытка покупки токена {token_address} для {user_id}/{wallet_name}")
    wm = WalletManager()

    token_metadata = get_token_metadata(token_address)
    token_name = token_metadata.get('name', 'Unknown')
    token_symbol = token_metadata.get('symbol', 'UNKNOWN')

    # Отправляем анализ в группу
    try:
        await send_token_analysis_to_group(bot, token_address)
    except Exception as e:
        logger.error(f"Ошибка отправки анализа токена {token_address} в группу: {e}")

    if is_potential_scam(token_address, token_name, token_symbol):
        logger.warning(f"Токен {token_address} ({token_name}) заблокирован фильтрами.")
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

    wallet_config = wm.get_wallet_config(user_id, wallet_name)
    if not wallet_config:
        logger.error(f"Конфигурация кошелька {wallet_name} для пользователя {user_id} не найдена.")
        return False

    trade_percentage = wallet_config.get('trade_percentage', 1.0)
    if trade_percentage <= 0 or trade_percentage > 100:
        logger.error(f"Неверный процент от баланса для кошелька {wallet_name}: {trade_percentage}%")
        return False

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
        # --- ЕДИНЫЙ БЛОК РАСЧЕТОВ ---
        
        # 1. Получаем цену SOL в USDT
        sol_price_usdt = get_sol_usdt_price()
        if sol_price_usdt <= 0:
            raise Exception("Failed to get SOL price")

        # 2. Получаем баланс кошелька
        wallet_balance_sol = wm.get_wallet_balance_solana(wallet_address)
        if wallet_balance_sol <= 0:
            raise Exception("Failed to get wallet balance")

        # 3. Получаем текущую цену токена
        current_price_usdt = await get_token_current_price(token_address)
        if current_price_usdt <= 0:
            raise Exception("Failed to get token price")

        # 4. Рассчитываем сумму покупки
        wallet_balance_usdt = wallet_balance_sol * sol_price_usdt
        purchase_amount_usdt = wallet_balance_usdt * (trade_percentage / 100.0)
        purchase_amount_sol = purchase_amount_usdt / sol_price_usdt

        # 5. Логируем после всех расчетов
        logger.info(f"Кошелек {wallet_name}: Баланс {wallet_balance_sol:.6f} SOL ({wallet_balance_usdt:.6f} USDT). "
                    f"Покупка на {trade_percentage}% = {purchase_amount_sol:.6f} SOL ({purchase_amount_usdt:.6f} USDT).")

        # 6. Проверяем достаточность средств (включая создание ATA)
        ATA_CREATION_COST_SOL = 0.00203928
        MINIMUM_GAS_FOR_FUTURE_TX = 0.00001
        TOTAL_REQUIRED_SOL = purchase_amount_sol + ATA_CREATION_COST_SOL + MINIMUM_GAS_FOR_FUTURE_TX

        if wallet_balance_sol < TOTAL_REQUIRED_SOL:
            logger.error(f"Недостаточно средств для покупки. Требуется {TOTAL_REQUIRED_SOL:.6f} SOL, доступно {wallet_balance_sol:.6f} SOL")
            
            deposit_url = "https://jup.ag/swap/SOL-USDC"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Пополнить кошелек", url=deposit_url)]
            ])
            
            await bot.send_message(
                user_id,
                f"❌ Не удалось купить токен <b>{token_name} ({token_symbol})</b>.\n\n"
                f"📊 Баланс кошелька: <b>{wallet_balance_sol:.6f} SOL</b>\n"
                f"💸 Необходимо: <b>{TOTAL_REQUIRED_SOL:.6f} SOL</b>\n"
                f"📉 Не хватает: <b>{TOTAL_REQUIRED_SOL - wallet_balance_sol:.6f} SOL</b>\n\n"
                f"Для покупки требуется дополнительное место под новый токен (ата), "
                f"что стоит ~0.00204 SOL.",
                parse_mode="HTML",
                reply_markup=keyboard
            )
            return False

        # --- ПРОДОЛЖАЕМ С ПОКУПКОЙ ---
        sol_to_spend_lamports = int(purchase_amount_sol * 1_000_000_000)

        swap_transaction = get_jupiter_swap_transaction_improved(
            input_mint="So11111111111111111111111111111111111111112",
            output_mint=token_address,
            amount=sol_to_spend_lamports,
            slippage=100,
            user_public_key=wallet_address
        )

        if not swap_transaction:
            logger.warning(f"❌ Не удалось создать маршрут обмена для токена {token_address}.")
            try:
                await bot.send_message(
                    user_id,
                    f"❌ Покупка токена <b>{token_name} ({token_symbol})</b> не удалась.\n"
                    f"Причина: Не удалось получить маршрут обмена.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
            return False

        # Декодирование и подпись транзакции
        transaction_base64 = swap_transaction['swapTransaction']
        raw_txn = base64.b64decode(transaction_base64)
        logger.info(f"Успешно декодирована транзакция из base64, длина: {len(raw_txn)} байт")

        transaction = VersionedTransaction.from_bytes(raw_txn)
        logger.info("Успешно создана VersionedTransaction из байтов")

        signed_tx = VersionedTransaction(transaction.message, [keypair])
        raw_signed_txn = bytes(signed_tx)

        # Проверка размера транзакции
        if len(raw_signed_txn) > 1232:
            logger.error(f"Транзакция слишком большая: {len(raw_signed_txn)} байт")
            try:
                await bot.send_message(
                    user_id,
                    f"❌ Транзакция покупки токена <b>{token_name} ({token_symbol})</b> слишком большая.\n"
                    f"Размер: {len(raw_signed_txn)} байт (максимум 1232 байта).",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
            return False

        # Отправка транзакции
        client = Client(SOLANA_RPC_URL)
        tx_sig = client.send_raw_transaction(raw_signed_txn)
        logger.info(f"Транзакция покупки отправлена: {tx_sig}")

        # Ожидание подтверждения
        start_time = time.time()
        timeout = 30
        while time.time() - start_time < timeout:
            try:
                confirmation = client.confirm_transaction(tx_sig, commitment=Confirmed, sleep_seconds=1)
                if confirmation.value[0] is not None:
                    break
            except Exception as e:
                logger.warning(f"Ожидание подтверждения транзакции: {e}")
                await asyncio.sleep(2)
        else:
            logger.error(f"Таймаут подтверждения транзакции: {tx_sig}")
            try:
                await bot.send_message(
                    user_id,
                    f"❌ Транзакция покупки токена <b>{token_name} ({token_symbol})</b> не подтвердилась вовремя.\n"
                    f"Signature: {tx_sig}\n"
                    f"Проверьте позже в эксплорере.",
                    parse_mode="HTML"
                )
            except Exception as e:
                logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
            return False

        # --- ПОКУПКА УСПЕШНА ---
        logger.info(f"Покупка токена {token_address} для {user_id}/{wallet_name} завершена успешно.")

        profit_percentage = wallet_config.get('profit_percentage', 100.0)
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

        purchased_tokens[token_address] = {
            "name": token_name,
            "symbol": token_symbol,
            "purchase_price_usdt": current_price_usdt,
            "current_price_usdt": current_price_usdt,
            "target_price_usdt": target_price_usdt,
            "profit_percentage_target": profit_percentage,
            "purchase_amount_usdt": purchase_amount_usdt,
            "purchase_time": str(time.time()),
            "tx_signature": str(tx_sig)
        }

        os.makedirs(os.path.dirname(purchased_tokens_file), exist_ok=True)
        with open(purchased_tokens_file, 'w') as f:
            json.dump(purchased_tokens, f, indent=2)

        try:
            await bot.send_message(
                user_id,
                f"✅ Куплен токен <b>{token_name} ({token_symbol})</b>\n"
                f"💰 Цена покупки: <b>{current_price_usdt:.6f} USDT</b>\n"
                f"💸 Потрачено: <b>{purchase_amount_usdt:.6f} USDT</b> ({trade_percentage}% от баланса)\n"
                f"🎯 Цель продажи: <b>{target_price_usdt:.6f} USDT</b> (+{profit_percentage}% / x{multiplier_for_target:.2f})\n"
                f"📝 TX: <code>{tx_sig}</code>",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id}: {e}")
            return True
    except Exception as e:
        logger.error(f"Необработанная ошибка при покупке токена {token_address} для {user_id}/{wallet_name}: {e}")
        try:
            await bot.send_message(
                user_id,
                f"❌ Критическая ошибка при покупке токена {token_name} ({token_symbol}):\n{str(e)[:200]}...",
                parse_mode="HTML"
            )
        except Exception as ex:
            logger.error(f"Ошибка отправки уведомления об ошибке пользователю {user_id}: {ex}")
        return False

async def check_and_sell_tokens(user_id: int, wallet_name: str, bot: Bot):
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
                updated_tokens[token_address] = token_data
                continue
                
            purchase_price = token_data.get('purchase_price_usdt', 0)
            target_price = token_data.get('target_price_usdt', 0)

            token_data['current_price_usdt'] = current_price_usdt
            updated_tokens[token_address] = token_data

            if current_price_usdt >= target_price and target_price > 0:
                logger.info(f"Цель продажи достигнута для {token_address} ({current_price_usdt} >= {target_price}). Продажа...")
                sold = await sell_token(user_id, wallet_name, token_address, bot)
                if sold:
                    sold_any = True
                    if token_address in updated_tokens:
                        del updated_tokens[token_address]
                else:
                    updated_tokens[token_address] = token_data

        if sold_any:
            with open(purchased_tokens_file, 'w') as f:
                json.dump(updated_tokens, f, indent=2)
            logger.info(f"Список купленных токенов для {user_id}/{wallet_name} обновлен.")
            
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка декодирования JSON из файла {purchased_tokens_file}: {e}")
    except Exception as e:
        logger.error(f"Ошибка при проверке целей продажи для {user_id}/{wallet_name}: {e}")

async def get_token_balance(wallet_address: str, token_address: str) -> int:
    try:
        client = Client(SOLANA_RPC_URL)
        opts = TokenAccountOpts(mint=Pubkey.from_string(token_address))
        response = client.get_token_accounts_by_owner(
            Pubkey.from_string(wallet_address),
            opts
        )
        if hasattr(response, 'value') and response.value:
            accounts = response.value
            if accounts:
                balance_str = accounts[0].account.data.parsed['info']['tokenAmount']['amount']
                return int(balance_str)
        elif isinstance(response, dict) and 'result' in response:
            result = response['result']
            if result and 'value' in result and result['value']:
                accounts = result['value']
                if accounts:
                    balance_str = accounts[0]['account']['data']['parsed']['info']['tokenAmount']['amount']
                    return int(balance_str)
                    
        return 0
        
    except Exception as e:
        logger.error(f"Ошибка получения баланса токена {token_address} для кошелька {wallet_address}: {e}")
        return 0
async def sell_token(user_id: int, wallet_name: str, token_address: str, bot: Bot) -> bool:
    logger.info(f"Попытка продажи токена {token_address} для {user_id}/{wallet_name}")
    wm = WalletManager()
    
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
        token_balance = await get_token_balance(wallet_address, token_address)
        if token_balance <= 0:
            logger.error(f"Нулевой баланс токена {token_address} на кошельке {wallet_address}")
            return False

        token_metadata = get_token_metadata(token_address)
        token_name = token_metadata.get('name', 'Unknown')
        token_symbol = token_metadata.get('symbol', 'UNKNOWN')

        # Получаем транзакцию свопа для продажи
        swap_transaction = get_jupiter_swap_transaction_improved(
            input_mint=token_address,
            output_mint="So11111111111111111111111111111111111111112",
            amount=token_balance,
            slippage=100,
            user_public_key=wallet_address
        )
        
        if not swap_transaction:
            logger.error(f"Не удалось получить транзакцию swap для продажи {token_address}")
            return False

        # Декодируем и подписываем транзакцию
        try:
            transaction_base64 = swap_transaction['swapTransaction']
            raw_txn = base64.b64decode(transaction_base64)
            transaction = VersionedTransaction.from_bytes(raw_txn)
            message_bytes = bytes(transaction.message)
            signature = keypair.sign_message(message_bytes)
            signed_tx = VersionedTransaction(message_bytes, [signature])
            raw_signed_txn = bytes(signed_tx)
        except Exception as e:
            logger.error(f"Ошибка при обработке или подписи транзакции продажи: {e}")
            return False

        # Отправляем транзакцию
        client = Client(SOLANA_RPC_URL)
        tx_sig = client.send_raw_transaction(raw_signed_txn)
        logger.info(f"Транзакция продажи отправлена: {tx_sig}")
        
        # Ожидаем подтверждения
        start_time = time.time()
        timeout = 30
        while time.time() - start_time < timeout:
            try:
                confirmation = client.confirm_transaction(tx_sig, commitment=Confirmed, sleep_seconds=1)
                if confirmation.value[0] is not None:
                    break
            except Exception as e:
                logger.warning(f"Ожидание подтверждения транзакции продажи: {e}")
                await asyncio.sleep(2)
        else:
            logger.error(f"Таймаут подтверждения транзакции продажи: {tx_sig}")
            return False

        logger.info(f"Продажа токена {token_address} для {user_id}/{wallet_name} завершена успешно.")

        # Отправляем уведомление пользователю
        purchased_tokens_file = os.path.join(PURCHASED_TOKENS_DIR, f"{user_id}_{wallet_name}.json")
        purchase_price = 0.0
        purchase_amount = 0.0
        profit_percent_target = 0.0
        
        if os.path.exists(purchased_tokens_file):
            try:
                with open(purchased_tokens_file, 'r') as f:
                    purchased_tokens = json.load(f)
                token_data = purchased_tokens.get(token_address, {})
                purchase_price = token_data.get('purchase_price_usdt', 0)
                purchase_amount = token_data.get('purchase_amount_usdt', 0)
                profit_percent_target = token_data.get('profit_percentage_target', 100.0)
            except Exception as e:
                logger.error(f"Ошибка получения данных токена из файла {purchased_tokens_file} для уведомления: {e}")

        try:
            await bot.send_message(
                user_id,
                f"✅ Продан токен <b>{token_name} ({token_symbol})</b>\n"
                f"💰 Цена покупки: <b>{purchase_price:.6f} USDT</b>\n"
                f"💸 Потрачено: <b>{purchase_amount:.6f} USDT</b>\n"
                f"🎯 Цель была: +{profit_percent_target}%\n"
                f"📝 TX: <code>{tx_sig}</code>\n"
                f"✅ Токен успешно продан.",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления пользователю {user_id} об успешной продаже: {e}")

        return True

    except Exception as e:
        logger.error(f"Ошибка при продаже токена {token_address} для {user_id}/{wallet_name}: {e}")
        try:
            await bot.send_message(
                user_id,
                f"❌ Ошибка продажи токена {token_address}:\n{str(e)[:200]}...",
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления об ошибке пользователю {user_id}: {e}")
        return False

# Новый код для мониторинга новых токенов
NEW_TOKENS_FILE = os.path.join(PURCHASED_TOKENS_DIR, 'new_tokens.json')

def save_new_token(token_address: str, token_data: dict):
    """Сохраняет информацию о новом токене"""
    try:
        if not os.path.exists(NEW_TOKENS_FILE):
            new_tokens = {}
        else:
            with open(NEW_TOKENS_FILE, 'r') as f:
                new_tokens = json.load(f)
        
        new_tokens[token_address] = token_data
        
        os.makedirs(os.path.dirname(NEW_TOKENS_FILE), exist_ok=True)
        with open(NEW_TOKENS_FILE, 'w') as f:
            json.dump(new_tokens, f, indent=2)
            
    except Exception as e:
        logger.error(f"Ошибка сохранения нового токена {token_address}: {e}")

def get_new_tokens() -> dict:
    """Получает список всех новых токенов"""
    try:
        if not os.path.exists(NEW_TOKENS_FILE):
            return {}
        
        with open(NEW_TOKENS_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка получения новых токенов: {e}")
        return {}

async def monitor_new_tokens(bot: Bot):
    """Мониторит цены новых токенов каждые 30 секунд"""
    while True:
        try:
            new_tokens = get_new_tokens()
            
            for token_address, token_data in new_tokens.items():
                try:
                    current_price = await get_token_current_price(token_address)
                    if current_price > 0:
                        purchase_price = token_data.get('purchase_price_usdt', 0)
                        if purchase_price > 0:
                            profit_percent = ((current_price - purchase_price) / purchase_price) * 100
                            multiplier = current_price / purchase_price
                            
                            # Отправляем уведомление о достижении целей
                            user_id = token_data.get('user_id')
                            wallet_name = token_data.get('wallet_name')
                            
                            if multiplier >= 4:
                                await bot.send_message(
                                    user_id,
                                    f"🚀 Токен <b>{token_data.get('name', 'Unknown')} ({token_data.get('symbol', 'UNKNOWN')})</b> достиг x4!\n"
                                    f"📈 Текущая цена: <b>{current_price:.6f} USDT</b>\n"
                                    f"💰 Прибыль: <b>+{profit_percent:.2f}%</b> (x{multiplier:.2f})",
                                    parse_mode="HTML"
                                )
                            elif multiplier >= 3:
                                await bot.send_message(
                                    user_id,
                                    f"🟢 Токен <b>{token_data.get('name', 'Unknown')} ({token_data.get('symbol', 'UNKNOWN')})</b> достиг x3!\n"
                                    f"📈 Текущая цена: <b>{current_price:.6f} USDT</b>\n"
                                    f"💰 Прибыль: <b>+{profit_percent:.2f}%</b> (x{multiplier:.2f})",
                                    parse_mode="HTML"
                                )
                            elif multiplier >= 2:
                                await bot.send_message(
                                    user_id,
                                    f"🟡 Токен <b>{token_data.get('name', 'Unknown')} ({token_data.get('symbol', 'UNKNOWN')})</b> достиг x2!\n"
                                    f"📈 Текущая цена: <b>{current_price:.6f} USDT</b>\n"
                                    f"💰 Прибыль: <b>+{profit_percent:.2f}%</b> (x{multiplier:.2f})",
                                    parse_mode="HTML"
                                )
                except Exception as e:
                    logger.error(f"Ошибка мониторинга нового токена {token_address}: {e}")
            
            await asyncio.sleep(30)  # Проверяем каждые 30 секунд
            
        except Exception as e:
            logger.error(f"Ошибка в мониторинге новых токенов: {e}")
            await asyncio.sleep(30)

async def start_monitoring(bot: Bot):
    """Запускает мониторинг купленных токенов и новых токенов"""
    # Создаем задачи для обоих мониторингов
    task1 = asyncio.create_task(monitor_purchased_tokens(bot))
    task2 = asyncio.create_task(monitor_new_tokens(bot))
    
    # Ждем завершения обеих задач
    await asyncio.gather(task1, task2)

async def monitor_purchased_tokens(bot: Bot):
    """Оригинальный мониторинг купленных токенов каждые 60 секунд"""
    while True:
        try:
            wm = WalletManager()
            all_wallets = wm.get_all_wallets()
            
            for user_id, wallet_name in all_wallets:
                await check_and_sell_tokens(user_id, wallet_name, bot)
                
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Ошибка в мониторинге купленных токенов: {e}")
            await asyncio.sleep(60)

# Обновленная функция покупки токена с сохранением в новые токены
async def buy_token_with_monitoring(user_id: int, wallet_name: str, token_address: str, bot: Bot):
    """Покупает токен и добавляет его в список для мониторинга новых токенов"""
    success = await buy_token(user_id, wallet_name, token_address, bot)
    
    if success:
        # Получаем метаданные токена
        token_metadata = get_token_metadata(token_address)
        token_name = token_metadata.get('name', 'Unknown')
        token_symbol = token_metadata.get('symbol', 'UNKNOWN')
        
        # Получаем текущую цену токена
        current_price = await get_token_current_price(token_address)
        
        # Сохраняем токен в список новых токенов для мониторинга
        new_token_data = {
            "name": token_name,
            "symbol": token_symbol,
            "purchase_price_usdt": current_price,
            "user_id": user_id,
            "wallet_name": wallet_name,
            "purchase_time": str(time.time())
        }
        
        save_new_token(token_address, new_token_data)
        logger.info(f"Токен {token_address} добавлен в список новых токенов для мониторинга")
    
    return success
