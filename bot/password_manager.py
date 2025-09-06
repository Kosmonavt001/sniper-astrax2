import json
import logging
import os

logger = logging.getLogger(__name__)

class PasswordManager:
    def __init__(self):
        self.user_passwords = self.load_user_passwords()
    
    def load_user_passwords(self) -> dict:
        """Загружает пароли пользователей"""
        try:
            USER_PASSWORDS_FILE = 'data/user_passwords.json'
            if os.path.exists(USER_PASSWORDS_FILE):
                with open(USER_PASSWORDS_FILE, 'r') as f:
                    return json.load(f)
            else:
                with open(USER_PASSWORDS_FILE, 'w') as f:
                    json.dump({}, f)
                return {}
        except Exception as e:
            logger.error(f"Ошибка загрузки паролей: {e}")
            return {}
    
    def save_user_password(self, user_id: int):
        """Сохраняет пароль пользователя"""
        self.user_passwords[str(user_id)] = True
        try:
            USER_PASSWORDS_FILE = 'data/user_passwords.json'
            with open(USER_PASSWORDS_FILE, 'w') as f:
                json.dump(self.user_passwords, f, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения пароля: {e}")
    
    def is_user_authenticated(self, user_id: int) -> bool:
        """Проверяет, авторизован ли пользователь"""
        return str(user_id) in self.user_passwords
