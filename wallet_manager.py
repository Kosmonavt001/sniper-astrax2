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

    def add_wallet(self, user_id: int, name: str, address: str, mnemonic: str,
                  # --- ИЗМЕНЕНО: Убраны фиксированные значения по умолчанию ---
                  # purchase_price_usdt: float = 100.0,
                  # target_x2: float = 200.0,
                  # target_x3: float = 300.0,
                  # target_x4: float = 400.0
                  # --- НОВОЕ: Добавлены новые параметры по умолчанию ---
                  trade_percentage: float = 1.0, # 1% от баланса
                  profit_percentage: float = 100.0 # 100% прибыль (x2)
                  ) -> bool:
        """Добавляет новый кошелек с генерацией приватного ключа из seed фразы"""
        try:
            # Проверяем валидность mnemonic фразы
            mnemo = Mnemonic("english")
            if not mnemo.check(mnemonic):
                raise ValueError("Неверная seed фраза")
            # Генерируем приватный ключ из seed фразы
            seed = mnemo.to_seed(mnemonic)
            private_key_bytes = seed[:32]
            keypair = Keypair.from_seed(private_key_bytes)
            private_key_full = bytes(keypair)
            private_key_b58 = base58.b58encode(private_key_full).decode('utf-8')
            # Загружаем текущую конфигурацию пользователя
            user_config = self.load_user_config(user_id)
            # Инициализируем кошельки если нужно
            if "wallets" not in user_config:
                user_config["wallets"] = {}
            # Добавляем кошелек с seed фразой и приватным ключом
            user_config["wallets"][name] = {
                "address": address,
                "mnemonic": mnemonic,
                "private_key": private_key_b58,
                "created_at": str(time.time()),
                # --- УДАЛЕНО: Старые фиксированные параметры ---
                # "purchase_price_usdt": purchase_price_usdt,
                # "target_x2": target_x2,
                # "target_x3": target_x3,
                # "target_x4": target_x4
                # --- НОВОЕ: Новые параметры ---
                "trade_percentage": trade_percentage,
                "profit_percentage": profit_percentage
            }
            # Сохраняем обновленную конфигурацию
            self.save_user_config(user_id, user_config)
            return True
        except Exception as e:
            logger.error(f"Ошибка добавления кошелька для пользователя {user_id}: {e}")
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