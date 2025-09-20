import json
import logging
import os
from mnemonic import Mnemonic
from solders.keypair import Keypair
import base58
import requests
import time
from solders.pubkey import Pubkey

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_DIR = 'config'
USER_CONFIGS_DIR = os.path.join(CONFIG_DIR, 'wallets')
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

class WalletManager:
    def __init__(self):
        # Убедимся, что директория существует
        os.makedirs(USER_CONFIGS_DIR, exist_ok=True)

    def _get_user_config_file(self, user_id: int) -> str:
        """Получает путь к файлу конфигурации пользователя"""
        return os.path.join(USER_CONFIGS_DIR, f"{user_id}.json")

    def load_user_config(self, user_id: int) -> dict:
        """Загружает конфигурацию пользователя"""
        config_file = self._get_user_config_file(user_id)
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r') as f:
                    return json.load(f)
            else:
                # Создаем пустой файл
                with open(config_file, 'w') as f:
                    json.dump({}, f)
                return {}
        except Exception as e:
            logger.error(f"Ошибка загрузки конфига пользователя {user_id}: {e}")
            return {}

    def save_user_config(self, user_id: int, config: dict):
        """Сохраняет конфигурацию пользователя"""
        config_file = self._get_user_config_file(user_id)
        try:
            with open(config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения конфига пользователя {user_id}: {e}")

    def add_wallet(self, user_id: int, name: str, address: str, private_key_b58: str,
               trade_percentage: float = 1.0,
               profit_percentage: float = 100.0) -> bool:
        """Добавляет кошелёк по имени, адресу и приватному ключу (Base58)"""
        try:
            # Декодируем приватный ключ из Base58
            private_key_bytes = base58.b58decode(private_key_b58)

            # Пытаемся создать Keypair из байтов
            keypair = Keypair.from_bytes(private_key_bytes)
            derived_pubkey = str(keypair.pubkey())

            # Опционально: проверяем соответствие адресов
            if derived_pubkey != address:
                raise ValueError(f"Приватный ключ соответствует адресу {derived_pubkey}, "
                                f"но указан {address}. Несоответствие!")

            # Готовим конфиг
            user_config = self.load_user_config(user_id)
            if "wallets" not in user_config:
                user_config["wallets"] = {}

            # Сохраняем только приватный ключ (без мнемоники)
            user_config["wallets"][name] = {
                "address": address,
                "private_key": private_key_b58,  # сохраняем как строку
                "created_at": str(time.time()),
                "trade_percentage": trade_percentage,
                "profit_percentage": profit_percentage
            }

            self.save_user_config(user_id, user_config)
            return True

        except Exception as e:
            logger.error(f"Ошибка добавления кошелька через приватный ключ: {e}")
            return False
        
    def get_user_wallets(self, user_id: int) -> dict:
        """Получает кошельки пользователя"""
        user_config = self.load_user_config(user_id)
        return user_config.get("wallets", {})

    def verify_solana_address(self, address: str) -> bool:
        """Проверяет валидность адреса Solana"""
        try:
            Pubkey.from_string(address)
            return True
        except:
            return False

    def get_wallet_balance_solana(self, wallet_address: str) -> float:
        """Получает баланс Solana кошелька через RPC вызов"""
        try:
            logger.info(f"Запрос баланса для адреса: {wallet_address}")

            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getBalance",
                "params": [wallet_address]
            }

            headers = {
                "Content-Type": "application/json"
            }

            response = requests.post(SOLANA_RPC_URL, json=payload, headers=headers, timeout=10)

            if response.status_code == 200:
                result = response.json()

                if 'result' in result and 'value' in result['result']:
                    balance_lamports = result['result']['value']
                    balance_sol = balance_lamports / 1_000_000_000
                    logger.info(f"Баланс кошелька {wallet_address}: {balance_sol} SOL")
                    return round(balance_sol, 6)
                else:
                    logger.error(f"Ошибка в ответе RPC для {wallet_address}: {result}")
                    return 0.0
            else:
                logger.error(f"HTTP ошибка {response.status_code} для адреса {wallet_address}")
                return 0.0

        except Exception as e:
            logger.error(f"Ошибка получения баланса для {wallet_address}: {e}")
            return 0.0

    # --- НОВЫЕ МЕТОДЫ ДЛЯ ТОРГОВЛИ ---
    def get_wallet_private_key(self, user_id: int, wallet_name: str) -> str:
        """Получает приватный ключ кошелька пользователя"""
        wallets = self.get_user_wallets(user_id)
        if wallet_name in wallets:
            return wallets[wallet_name].get('private_key', '')
        return ''

    def get_wallet_address(self, user_id: int, wallet_name: str) -> str:
        """Получает адрес кошелька пользователя"""
        wallets = self.get_user_wallets(user_id)
        if wallet_name in wallets:
            return wallets[wallet_name].get('address', '')
        return ''

    def get_wallet_config(self, user_id: int, wallet_name: str) -> dict:
        """Получает конфигурацию конкретного кошелька пользователя (цены, цели)"""
        user_config = self.load_user_config(user_id)
        wallets = user_config.get("wallets", {})
        if wallet_name in wallets:
            return wallets[wallet_name]
        return {}

    def update_wallet_config(self, user_id: int, wallet_name: str, updates: dict):
        """Обновляет конфигурацию конкретного кошелька пользователя"""
        user_config = self.load_user_config(user_id)
        if "wallets" not in user_config:
            user_config["wallets"] = {}
        if wallet_name not in user_config["wallets"]:
            user_config["wallets"][wallet_name] = {}

        user_config["wallets"][wallet_name].update(updates)
        self.save_user_config(user_id, user_config)
