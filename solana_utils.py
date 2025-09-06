# solana_utils.py
import json
import requests
import logging
import time
from solana.rpc.api import Client
from solana.rpc.commitment import Confirmed
from solders.pubkey import Pubkey

# --- Настройка логирования ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Константы ---
# Используем официальный эндпоинт Solana Mainnet Beta
# Исправлены URL без лишних пробелов
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"
JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP_URL = "https://quote-api.jup.ag/v6/swap"
# DexScreener API для получения информации о токенах
DEXSCREENER_API_URL = "https://api.dexscreener.com/latest/dex/tokens/"
JUPITER_MAX_RETRIES = 3
JUPITER_BACKOFF_FACTOR = 1
def get_token_metadata(token_address: str) -> dict:
    """
    Получает метаданные токена (имя, символ, URI) через DexScreener API.

    Args:
        token_address (str): Адрес токена Solana

    Returns:
        dict: Словарь с информацией о токене
    """
    try:
        # Используем DexScreener API для получения метаданных
        url = f"{DEXSCREENER_API_URL}{token_address}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            pairs = data.get("pairs", [])

            # Берем первую пару для получения метаданных
            if pairs:
                pair = pairs[0]
                base_token = pair.get("baseToken", {})
                return {
                    "name": base_token.get('name', 'Unknown'),
                    "symbol": base_token.get('symbol', 'UNKNOWN'),
                    "decimals": 0,  # DexScreener не предоставляет информацию о decimals
                    "logoURI": base_token.get('logoURI', '')
                }
        logger.warning(f"Не удалось получить метаданные для токена {token_address}. Status: {response.status_code}")
        return {"name": "Unknown", "symbol": "UNKNOWN", "decimals": 0, "logoURI": ""}
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении метаданных для {token_address}: {e}")
        return {"name": "Unknown", "symbol": "UNKNOWN", "decimals": 0, "logoURI": ""}
    except Exception as e:
        logger.error(f"Ошибка получения метаданных для {token_address}: {e}")
        return {"name": "Unknown", "symbol": "UNKNOWN", "decimals": 0, "logoURI": ""}

def get_sol_usdt_price() -> float:
    """
    Получает текущую цену SOL в USDT через CoinGecko API.

    Returns:
        float: Цена SOL в USDT
    """
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            price = data.get('solana', {}).get('usd', 0.0)
            if price > 0:
                return float(price)
            else:
                logger.warning("CoinGecko вернул нулевую цену для SOL.")
                return 0.0

        logger.warning(f"Не удалось получить цену SOL через CoinGecko. Status: {response.status_code}")
        return 0.0
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении цены SOL в USDT: {e}")
        return 0.0
    except Exception as e:
        logger.error(f"Ошибка получения цены SOL в USDT: {e}")
        return 0.0

def get_jupiter_swap_transaction(
    input_mint: str, 
    output_mint: str, 
    amount: int,  # Теперь это должно быть в lamports
    slippage: int,
    user_public_key: str
) -> dict:
    """
    Получает транзакцию swap от Jupiter API.
    """
    try:
        logger.info(f"DEBUG: get_jupiter_swap_transaction called with:")
        logger.info(f"  input_mint: {input_mint}")
        logger.info(f"  output_mint: {output_mint}")
        logger.info(f"  amount: {amount} (type: {type(amount)})")
        logger.info(f"  slippage: {slippage}")
        logger.info(f"  user_public_key: {user_public_key}")
        
        # Преобразуем количество в smallest units (lamports для SOL)
        amount_in_smallest_units = int(amount)
        logger.info(f"  amount_in_smallest_units: {amount_in_smallest_units}")
        
        # 1. Получаем котировку
        quote_params = {
            'inputMint': input_mint,
            'outputMint': output_mint,
            'amount': amount_in_smallest_units,
            'slippageBps': slippage
        }
        
        logger.info(f"DEBUG: Sending quote request to {JUPITER_QUOTE_URL} with params: {quote_params}")
        
        quote_response = requests.get(JUPITER_QUOTE_URL, params=quote_params, timeout=20)
        logger.info(f"DEBUG: Quote response status: {quote_response.status_code}")
        logger.info(f"DEBUG: Quote response headers: {dict(quote_response.headers)}")
        
        if quote_response.status_code != 200:
            logger.error(f"Ошибка получения котировки: {quote_response.status_code} {quote_response.text}")
            return None
            
        quote_data = quote_response.json()
        logger.info(f"DEBUG: Quote data received: {quote_data}")
        
        # 2. Получаем транзакцию
        swap_params = {
            'quoteResponse': quote_data,
            'userPublicKey': user_public_key,
            'wrapAndUnwrapSol': True,
            'useSharedAccounts': False
        }
        
        logger.info(f"DEBUG: Sending swap request with params: {swap_params}")
        
        swap_response = requests.post(JUPITER_SWAP_URL, json=swap_params, timeout=30)
        logger.info(f"DEBUG: Swap response status: {swap_response.status_code}")
        logger.info(f"DEBUG: Swap response headers: {dict(swap_response.headers)}")
        
        if swap_response.status_code != 200:
            logger.error(f"Ошибка получения транзакции swap: {swap_response.status_code} {swap_response.text}")
            try:
                error_details = swap_response.json()
                logger.error(f"DEBUG: Swap error details: {error_details}")
            except:
                logger.error(f"DEBUG: Swap error text: {swap_response.text}")
            return None
            
        swap_data = swap_response.json()
        logger.info(f"DEBUG: Swap data received: {swap_data}")
        
        # Jupiter возвращает 'swapTransaction'
        if 'swapTransaction' not in swap_data:
            logger.error(f"Ключ 'swapTransaction' не найден в ответе Jupiter: {swap_data}")
            return None
            
        # Возвращаем в формате с ключом 'tx' для совместимости с trader.py
        return {"tx": swap_data['swapTransaction']}
    except Exception as e:
        logger.error(f"Ошибка при получении транзакции swap: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None
    
def get_token_dex_listings(token_address: str) -> list:
    """
    Получает список DEX, на которых торгуется токен через DexScreener API.

    Args:
        token_address (str): Адрес токена

    Returns:
        list: Список названий DEX
    """
    try:
        # Используем DexScreener API для получения информации о пулах
        url = f"{DEXSCREENER_API_URL}{token_address}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            pairs = data.get("pairs", [])
            dex_names = [pair.get('dexId', 'Unknown') for pair in pairs]
            return dex_names

        logger.warning(f"Не удалось получить информацию о DEX для токена {token_address}. Status: {response.status_code}")
        return []
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении списка DEX для {token_address}: {e}")
        return []
    except Exception as e:
        logger.error(f"Ошибка получения списка DEX для {token_address}: {e}")
        return []

def get_token_price_usdt(token_address: str) -> float:
    """
    Получает текущую цену токена в USDT через DexScreener API.

    Args:
        token_address (str): Адрес токена

    Returns:
        float: Цена токена в USDT
    """
    try:
        # Используем DexScreener API для получения текущей цены
        url = f"{DEXSCREENER_API_URL}{token_address}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            pairs = data.get("pairs", [])

            # Ищем пару с SOL в качестве квотируемого токена
            for pair in pairs:
                quote_token = pair.get("quoteToken", {})
                if quote_token.get("symbol", "").upper() == "SOL":
                    price_usd = pair.get("priceUsd", "0")
                    try:
                        price_float = float(price_usd)
                        if price_float > 0:
                            return price_float
                    except (TypeError, ValueError):
                        continue

        logger.warning(f"Не удалось получить текущую цену для токена {token_address} через DexScreener. Status: {response.status_code}")
        return 0.0
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении цены токена {token_address} в USDT: {e}")
        return 0.0
    except Exception as e:
        logger.error(f"Ошибка при получении цены токена {token_address} в USDT: {e}")
        return 0.0

def get_token_creation_time(token_address: str) -> float:
    """
    Получает время создания токена через DexScreener API.

    Args:
        token_address (str): Адрес токена

    Returns:
        float: Время создания в Unix timestamp или 0 при ошибке
    """
    try:
        # Используем DexScreener API для получения времени создания
        url = f"{DEXSCREENER_API_URL}{token_address}"
        response = requests.get(url, timeout=10)

        if response.status_code == 200:
            data = response.json()
            pairs = data.get("pairs", [])

            # Берем первую пару для получения времени создания
            if pairs:
                created_at = pairs[0].get("pairCreatedAt", 0)
                # DexScreener возвращает время в миллисекундах
                if created_at > 0:
                    return created_at / 1000  # Конвертируем в секунды

        logger.warning(f"Не удалось получить время создания для токена {token_address}. Status: {response.status_code}")
        return 0.0
    except requests.exceptions.RequestException as e:
        logger.error(f"Ошибка сети при получении времени создания для {token_address}: {e}")
        return 0.0
    except Exception as e:
        logger.error(f"Ошибка при получении времени создания для {token_address}: {e}")
        return 0.0
