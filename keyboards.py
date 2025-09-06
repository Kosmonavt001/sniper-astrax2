from aiogram.utils.keyboard import InlineKeyboardBuilder

def create_main_menu():
    """Создает главное меню"""
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="💰 Мой кошелек", callback_data="my_wallet")
    keyboard.button(text="📊 Статистика", callback_data="stats")
    keyboard.button(text="⚙️ Настройки торговли", callback_data="trade_settings")
    keyboard.adjust(1)
    return keyboard.as_markup()

def create_wallet_menu(user_id: int):
    """Создает меню кошельков"""
    from wallet_manager import WalletManager
    wm = WalletManager()
    keyboard = InlineKeyboardBuilder()

    wallets = wm.get_user_wallets(user_id)
    if wallets:
        for name in wallets.keys():
            keyboard.button(text=f"👛 {name}", callback_data=f"wallet_{name}")
        keyboard.button(text="➕ Добавить кошелек", callback_data="add_wallet")
    else:
        keyboard.button(text="➕ Добавить кошелек", callback_data="add_wallet")

    keyboard.button(text="⬅️ Назад", callback_data="main_menu")
    keyboard.adjust(1)
    return keyboard.as_markup()