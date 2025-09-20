import asyncio
import datetime
import json
import logging
import os
from typing import Dict, List
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, FSInputFile, InputMediaPhoto, InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from mnemonic import Mnemonic
from solders.pubkey import Pubkey
from solders.keypair import Keypair
import base58
import requests

from filters import check_token_scam_risk
from keyboards import create_main_menu, create_wallet_menu
from wallet_manager import WalletManager
from trader import get_user_config, get_purchased_tokens_info
from bot.password_manager import PasswordManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_DIR = 'config'
BOT_CONFIG_FILE = os.path.join(CONFIG_DIR, 'bot_config.json')
USER_PASSWORDS_FILE = 'data/user_passwords.json'
PHOTO_DIR = 'photo'
PURCHASED_TOKENS_DIR = 'data/purchased_tokens'
NEWLY_FOUND_TOKENS_FILE = 'data/newly_found_tokens.json'

class BotConfig:
    def __init__(self):
        self.config = self.load_or_create_config()
        self.password = self.config.get('password', 'default_password')
        
    def load_or_create_config(self) -> dict:
        """Загружает или создает конфигурацию бота"""
        default_config = {
            "password": "admin123",
            "bot_token": "YOUR_BOT_TOKEN_HERE"
        }
        
        if os.path.exists(BOT_CONFIG_FILE):
            try:
                with open(BOT_CONFIG_FILE, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Ошибка загрузки конфига: {e}")
                return default_config
        else:
            with open(BOT_CONFIG_FILE, 'w') as f:
                json.dump(default_config, f, indent=2)
            return default_config

class WalletStates(StatesGroup):
    waiting_for_password = State()
    waiting_for_wallet_name = State()
    waiting_for_wallet_address = State()
    waiting_for_mnemonic = State()
    waiting_for_purchase_price = State()
    waiting_for_targets = State()
    waiting_for_trade_percentage = State()
    waiting_for_profit_percentage = State()
    editing_setting = State() 

config = BotConfig()
password_manager = PasswordManager()
wallet_manager = WalletManager()

bot = Bot(token=config.config.get('bot_token', ''))
dp = Dispatcher()

def get_photo_path(filename: str) -> str:
    return os.path.join(PHOTO_DIR, filename)

@dp.callback_query(F.data == "trade_settings")
async def trade_settings_menu(callback: CallbackQuery):
    """Меню настроек торговли"""
    user_id = callback.from_user.id
    # Предполагаем, что пользователь хочет настроить параметры для всех своих кошельков
    # или для какого-то конкретного. Пока покажем общие настройки.
    # В будущем можно сделать выбор кошелька.
    # Для упрощения, сделаем настройки на уровне пользователя, а не кошелька.
    # Или можно сделать настройки на уровне кошелька. Выберем второй вариант.
    # Но тогда нужно знать, какой кошелек выбран. Предположим, что пользователь
    # находится в контексте какого-то кошелька. Это не всегда так.
    # Лучше сделать отдельное меню для настроек, где пользователь сначала выберет кошелек.
    # Но для начала сделаем настройки на уровне пользователя.

    # Пока что покажем сообщение с просьбой выбрать кошелек для настройки
    wallets = wallet_manager.get_user_wallets(user_id)
    if not wallets:
        await callback.answer("У вас нет кошельков для настройки.", show_alert=True)
        return

    keyboard = InlineKeyboardBuilder()
    for name in wallets.keys():
        keyboard.button(text=f"👛 {name}", callback_data=f"settings_wallet_{name}")
    keyboard.button(text="⬅️ Назад", callback_data="main_menu")
    keyboard.adjust(1)

    # --- ИСПРАВЛЕНИЕ: Используем edit_text вместо edit_caption ---
    try:
        await callback.message.edit_text( # <--- Изменено здесь
            text="⚙️ Выберите кошелек для настройки параметров торговли:", # <--- И здесь
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        if "message is not modified" in e.message.lower():
            # Сообщение не изменилось, это не критично
            pass
        elif "message can't be edited" in e.message.lower():
            # Сообщение не может быть отредактировано (например, слишком старое)
            # Можно отправить новое сообщение
            await callback.message.answer(
                text="⚙️ Выберите кошелек для настройки параметров торговли:",
                reply_markup=keyboard.as_markup(),
                parse_mode="HTML"
            )
            # Или удалить старое и отправить новое, если это уместно
            # await callback.message.delete()
            # await callback.message.answer(...)
        else:
            # Другая ошибка редактирования
            logger.error(f"Ошибка редактирования сообщения в trade_settings_menu: {e}")
            # Попробуем отправить новое сообщение
            await callback.message.answer(
                text="⚙️ Выберите кошелек для настройки параметров торговли:",
                reply_markup=keyboard.as_markup(),
                parse_mode="HTML"
            )
    await callback.answer()

@dp.callback_query(F.data.startswith("settings_wallet_"))
async def choose_wallet_for_settings(callback: CallbackQuery, state: FSMContext):
    """Выбор кошелька для настройки параметров"""
    # Извлекаем имя кошелька из callback_data, например, "settings_wallet_MyWallet"
    wallet_name = callback.data.split("_", 2)[2]
    user_id = callback.from_user.id

    # Получаем текущие настройки кошелька
    # Предполагается, что wallet_manager доступен как глобальная переменная или импортирован
    wallet_config = wallet_manager.get_wallet_config(user_id, wallet_name)
    # Получаем значения с разумными умолчаниями
    trade_percentage = wallet_config.get('trade_percentage', 1.0) # По умолчанию 1%
    profit_percentage = wallet_config.get('profit_percentage', 100.0) # По умолчанию 100% (x2)

    # Формируем текст сообщения
    text = (
        f"⚙️ <b>Настройки торговли для кошелька '{wallet_name}'</b>\n\n"
        f"📊 Процент от баланса на сделку: <b>{trade_percentage}%</b>\n"
        f"📈 Процент прибыли для выхода: <b>{profit_percentage}%</b> (x{(1 + profit_percentage/100):.2f})\n"
    )

    # Создаем клавиатуру
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="💱 Изменить % от баланса", callback_data=f"change_trade_percent_{wallet_name}")
    keyboard.button(text="🎯 Изменить % прибыли", callback_data=f"change_profit_percent_{wallet_name}")
    keyboard.button(text="⬅️ Назад", callback_data="trade_settings") # Предполагается, что "trade_settings" ведет в меню выбора кошелька
    keyboard.adjust(1) # Кнопки в один столбец

    # Попытка отредактировать сообщение
    try:
        # Сначала пытаемся отредактировать подпись (если сообщение с медиа)
        await callback.message.edit_caption(
            caption=text,
            reply_markup=keyboard.as_markup(),
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        # Если редактирование подписи не удалось, пытаемся отредактировать текст
        if "no caption" in e.message.lower() or "message is not modified" in e.message.lower():
            try:
                await callback.message.edit_text(
                    text=text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML"
                )
            except TelegramBadRequest as e2:
                # Если и редактирование текста не удалось (например, сообщение слишком старое),
                # можно просто ответить или отправить новое сообщение.
                # Здесь мы просто логируем.
                logger.warning(f"Не удалось отредактировать сообщение в choose_wallet_for_settings: {e2}")
                # Альтернатива: await callback.message.answer(text, reply_markup=keyboard.as_markup(), parse_mode="HTML")
        else:
            # Если ошибка другая, пробрасываем её
            logger.error(f"TelegramBadRequest в choose_wallet_for_settings: {e}")
            raise
    except Exception as e:
        # Ловим любые другие исключения
        logger.error(f"Ошибка в choose_wallet_for_settings: {e}")
        # Можно отправить сообщение пользователю об ошибке
        # await callback.answer("Произошла ошибка, попробуйте позже.", show_alert=True)
        # return

    # Сохраняем имя кошелька в состоянии FSM для последующих шагов
    await state.update_data(current_wallet_for_settings=wallet_name)
    # Отправляем ответ на callback, чтобы убрать "крутилку"
    await callback.answer()

@dp.callback_query(F.data.startswith("change_trade_percent_"))
async def start_change_trade_percent(callback: CallbackQuery, state: FSMContext):
    """Начало изменения процента от баланса"""
    wallet_name = callback.data.split("_", 3)[3] # "change_trade_percent_NAME"
    user_id = callback.from_user.id

    # Получаем текущее значение
    wallet_config = wallet_manager.get_wallet_config(user_id, wallet_name)
    current_percent = wallet_config.get('trade_percentage', 1.0)

    await state.set_state(WalletStates.waiting_for_trade_percentage)
    await state.update_data(current_wallet_for_settings=wallet_name) # На всякий случай обновим

    await callback.message.edit_caption(
        caption=f"💱 Введите новый процент от баланса для сделки (текущий: {current_percent}%):",
        reply_markup=InlineKeyboardBuilder().button(text="⬅️ Назад", callback_data=f"settings_wallet_{wallet_name}").as_markup()
    )
    await callback.answer()

@dp.message(WalletStates.waiting_for_trade_percentage)
async def get_new_trade_percent(message: Message, state: FSMContext):
    """Получение нового процента от баланса"""
    user_id = message.from_user.id
    try:
        new_percent = float(message.text.strip().replace('%', '')) # Убираем % если ввели
        if new_percent <= 0 or new_percent > 100:
            raise ValueError("Процент должен быть больше 0 и не больше 100")
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число от 0 до 100 (например, 1.5):")
        return

    user_data = await state.get_data()
    wallet_name = user_data.get('current_wallet_for_settings')

    if not wallet_name:
        await message.answer("❌ Ошибка состояния. Попробуйте снова.")
        await state.clear()
        return

    # Обновляем настройки кошелька
    wallet_manager.update_wallet_config(user_id, wallet_name, {'trade_percentage': new_percent})

    await state.clear()
    await message.answer(
        f"✅ Процент от баланса для кошелька '{wallet_name}' установлен на {new_percent}%.",
        reply_markup=create_main_menu()
    )

    # Возвращаемся в меню настроек кошелька
    # await choose_wallet_for_settings(...) - сложно вызвать напрямую, лучше переслать сообщение
    # или показать меню снова. Проще отправить новое сообщение с меню.
    # Но для простоты просто покажем главное меню.

@dp.callback_query(F.data.startswith("change_profit_percent_"))
async def start_change_profit_percent(callback: CallbackQuery, state: FSMContext):
    """Начало изменения процента прибыли"""
    wallet_name = callback.data.split("_", 3)[3] # "change_profit_percent_NAME"
    user_id = callback.from_user.id

    # Получаем текущее значение
    wallet_config = wallet_manager.get_wallet_config(user_id, wallet_name)
    current_percent = wallet_config.get('profit_percentage', 100.0) # x2

    await state.set_state(WalletStates.waiting_for_profit_percentage)
    await state.update_data(current_wallet_for_settings=wallet_name) # На всякий случай обновим

    await callback.message.edit_caption(
        caption=f"🎯 Введите новый процент прибыли для выхода (текущий: {current_percent}%, множитель x{(1 + current_percent/100):.2f}):",
        reply_markup=InlineKeyboardBuilder().button(text="⬅️ Назад", callback_data=f"settings_wallet_{wallet_name}").as_markup()
    )
    await callback.answer()

@dp.message(WalletStates.waiting_for_profit_percentage)
async def get_new_profit_percent(message: Message, state: FSMContext):
    """Получение нового процента прибыли"""
    user_id = message.from_user.id
    try:
        new_percent = float(message.text.strip().replace('%', '')) # Убираем % если ввели
        if new_percent < 0: # Можно и убыток задать, но 0 - минимум
            raise ValueError("Процент прибыли не может быть отрицательным")
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число (например, 100 для x2, 200 для x3):")
        return

    user_data = await state.get_data()
    wallet_name = user_data.get('current_wallet_for_settings')

    if not wallet_name:
        await message.answer("❌ Ошибка состояния. Попробуйте снова.")
        await state.clear()
        return

    # Обновляем настройки кошелька
    wallet_manager.update_wallet_config(user_id, wallet_name, {'profit_percentage': new_percent})

    await state.clear()
    await message.answer(
        f"✅ Процент прибыли для кошелька '{wallet_name}' установлен на {new_percent}% (множитель x{(1 + new_percent/100):.2f}).",
        reply_markup=create_main_menu()
    )

def save_found_token_info(token_address: str, token_name: str, token_symbol: str, price_usd: float):
    """Сохраняет информацию о найденном токене в файл."""
    try:
        # Создаем директорию data, если её нет
        os.makedirs(os.path.dirname(NEWLY_FOUND_TOKENS_FILE), exist_ok=True)

        # Получаем текущую дату и время
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        token_info = {
            "address": token_address,
            "name": token_name,
            "symbol": token_symbol,
            "price_usd": price_usd,
            "discovered_at": timestamp_str
        }

        # Загружаем существующие данные (если есть)
        existing_tokens = []
        if os.path.exists(NEWLY_FOUND_TOKENS_FILE):
            try:
                with open(NEWLY_FOUND_TOKENS_FILE, 'r') as f:
                    existing_tokens = json.load(f)
            except json.JSONDecodeError:
                logging.warning(f"Ошибка чтения {NEWLY_FOUND_TOKENS_FILE}, создается новый файл.")

        # Добавляем новую запись в начало списка
        existing_tokens.insert(0, token_info)

        # Ограничиваем количество записей, например, до 10 последних
        if len(existing_tokens) > 10:
            existing_tokens = existing_tokens[:10]

        # Сохраняем обновленный список
        with open(NEWLY_FOUND_TOKENS_FILE, 'w') as f:
            json.dump(existing_tokens, f, indent=2)

        logging.info(f"Информация о токене {token_symbol} ({token_address}) сохранена в {NEWLY_FOUND_TOKENS_FILE}")

    except Exception as e:
        logging.error(f"Ошибка сохранения информации о найденном токене {token_address}: {e}")

def get_last_found_token_info() -> dict:
    """Получает информацию о последнем найденном токене из файла."""
    try:
        if os.path.exists(NEWLY_FOUND_TOKENS_FILE):
            with open(NEWLY_FOUND_TOKENS_FILE, 'r') as f:
                tokens_data = json.load(f)
                if tokens_data and isinstance(tokens_data, list) and len(tokens_data) > 0:
                    return tokens_data[0] # Возвращаем первый (последний добавленный) элемент
    except Exception as e:
        logger.error(f"Ошибка при получении информации о последнем найденном токене: {e}")
    return {} # Возвращаем пустой словарь, если файл не существует или ошибка
@dp.callback_query(lambda c: c.data.startswith("refresh_"))
async def refresh_price(callback_query: types.CallbackQuery):
    token_address = callback_query.data.replace("refresh_", "")
    scam_info = await check_token_scam_risk(token_address)
    
    if scam_info["has_pairs"]:
        new_text = (
            f"💰 <b>Текущая цена</b>: ${float(scam_info['price_usd']):.8f} USD\n"
            f"🔄 Обновлено: {datetime.now(datetime.timezone.utc).strftime('%H:%M:%S UTC')}"
        )
    else:
        new_text = "❌ Не удалось получить актуальную цену."

    await callback_query.answer()
    await callback_query.message.edit_caption(caption=new_text, parse_mode="HTML")
@dp.message(CommandStart())
async def start_command(message: Message, state: FSMContext):
    """Обработка команды /start"""
    user_id = message.from_user.id
    
    # Проверяем, авторизован ли пользователь
    if password_manager.is_user_authenticated(user_id):
        menu_photo_path = get_photo_path("menu.png")
        photo = FSInputFile(menu_photo_path)
        await message.answer_photo(
            photo=photo,
            caption="👋 Добро пожаловать в Solana Wallet Bot!",
            reply_markup=create_main_menu()
        )
    else:
        await state.set_state(WalletStates.waiting_for_password)
        await message.answer("🔐 Введите пароль для доступа к боту:")

@dp.message(WalletStates.waiting_for_password)
async def check_password(message: Message, state: FSMContext):
    """Проверка пароля"""
    user_id = message.from_user.id
    
    if message.text == config.password:
        # Сохраняем пароль пользователя
        password_manager.save_user_password(user_id)
        await state.clear()
        
        # Создаем директорию для фото если её нет
        if not os.path.exists(PHOTO_DIR):
            os.makedirs(PHOTO_DIR)
        
        # Отправляем приветственное сообщение с фото
        menu_photo_path = get_photo_path("menu.png")
        photo = FSInputFile(menu_photo_path)
        await message.answer_photo(
            photo=photo,
            caption="👋 Добро пожаловать в Solana Wallet Bot!",
            reply_markup=create_main_menu()
        )
    else:
        await message.answer("❌ Неверный пароль. Попробуйте еще раз:")

@dp.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    """Главное меню"""
    menu_photo_path = get_photo_path("menu.png")
    photo = FSInputFile(menu_photo_path)
    input_media = InputMediaPhoto(media=photo)
    try:
        await callback.message.edit_media(
            media=input_media,
            reply_markup=create_main_menu()
        )
    except TelegramBadRequest:
        await callback.message.edit_text(
            text="🏠 Главное меню",
            reply_markup=create_main_menu()
        )
    await callback.answer()

@dp.callback_query(F.data == "my_wallet")
async def my_wallet_menu(callback: CallbackQuery):
    """Меню кошельков"""
    user_id = callback.from_user.id
    wallets = wallet_manager.get_user_wallets(user_id)
    
    if wallets:
        text = "👛 Ваши кошельки:\n"
        for name, wallet_data in wallets.items():
            address = wallet_data.get('address', 'N/A')
            
            # Получаем реальный баланс через RPC вызов
            balance = wallet_manager.get_wallet_balance_solana(address)
            logger.info(f"Получен баланс для кошелька {name} ({address}): {balance} SOL")
            # Показываем только первые и последние 4 символа адреса
            short_address = f"{address[:4]}...{address[-4:]}" if len(address) > 8 else address
            text += f"\n🔹 <b>{name}</b>\n📬 Адрес: <code>{short_address}</code>\n💰 Баланс: <b>{balance} SOL</b>\n"
    else:
        text = "📭 У вас пока нет кошельков.\nНажмите 'Добавить кошелек' чтобы начать."
    
    # Отправляем фото кошелька если есть
    wallet_photo_path = get_photo_path("wallet.png")
    if os.path.exists(wallet_photo_path):
        photo = FSInputFile(wallet_photo_path)
        input_media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
        try:
            await callback.message.edit_media(
                media=input_media,
                reply_markup=create_wallet_menu(user_id)
            )
        except TelegramBadRequest:
            await callback.message.edit_caption(
                caption=text,
                reply_markup=create_wallet_menu(user_id),
                parse_mode="HTML"
            )
    else:
        await callback.message.edit_caption(
            caption=text,
            reply_markup=create_wallet_menu(user_id),
            parse_mode="HTML"
        )
    await callback.answer()

@dp.callback_query(F.data == "add_wallet")
async def start_add_wallet(callback: CallbackQuery, state: FSMContext):
    """Начало добавления кошелька"""
    await state.set_state(WalletStates.waiting_for_wallet_name)
    await callback.message.edit_caption(
        caption="📝 Введите название для вашего кошелька:",
        reply_markup=InlineKeyboardBuilder().button(text="⬅️ Назад", callback_data="my_wallet").as_markup()
    )
    await callback.answer()

@dp.message(WalletStates.waiting_for_wallet_name)
async def get_wallet_name(message: Message, state: FSMContext):
    """Получение названия кошелька"""
    await state.update_data(wallet_name=message.text)
    await state.set_state(WalletStates.waiting_for_wallet_address)
    await message.answer(
        "📬 Введите адрес вашего Solana кошелька:",
        reply_markup=InlineKeyboardBuilder().button(text="⬅️ Назад", callback_data="my_wallet").as_markup()
    )

@dp.message(WalletStates.waiting_for_wallet_address)
async def get_wallet_address(message: Message, state: FSMContext):
    """Получение адреса кошелька"""
    wallet_address = message.text.strip()
    
    # Проверяем валидность адреса
    if not wallet_manager.verify_solana_address(wallet_address):
        await message.answer("❌ Неверный адрес Solana кошелька. Проверьте правильность и попробуйте еще раз:")
        return
    
    # Сохраняем адрес и переходим к запросу seed фразы
    await state.update_data(wallet_address=wallet_address)
    await state.set_state(WalletStates.waiting_for_mnemonic)
    await message.answer(
        "🔑 Введите ваш приватный ключ (Base58, ~88 символов):"
        "<b>⚠️ ВНИМАНИЕ:</b> Это даёт полный доступ к кошельку! "
        "Бот хранит его локально. Убедитесь, что доверяете этому сервису."
        "Пример: 5HvGqjXoKZ7BdYJU2eFQvDpV1hR7tWzgkE3rN9xTmSsP...",
        parse_mode="HTML",
        reply_markup=InlineKeyboardBuilder().button(text="⬅️ Назад", callback_data="my_wallet").as_markup()
    )

@dp.message(WalletStates.waiting_for_mnemonic)
async def get_private_key(message: Message, state: FSMContext):
    """Получение приватного ключа и добавление кошелька"""
    private_key_b58 = message.text.strip()

    # Проверка формата Base58 (простая проверка)
    if len(private_key_b58) < 80 or len(private_key_b58) > 96:
        await message.answer("❌ Приватный ключ должен быть строкой Base58 длиной ~88 символов. Попробуйте снова:")
        return

    # Получаем данные из состояния
    user_data = await state.get_data()
    wallet_name = user_data.get('wallet_name')
    wallet_address = user_data.get('wallet_address')
    user_id = message.from_user.id

    # Добавляем кошелек с приватным ключом
    success = wallet_manager.add_wallet(user_id, wallet_name, wallet_address, private_key_b58)

    if success:
        await state.clear()
        await message.answer(
            f"✅ Кошелек <b>{wallet_name}</b> успешно добавлен!"
            f"📬 Адрес: <code>{wallet_address[:6]}...{wallet_address[-4:]}</code>"
            f"🔐 Приватный ключ сохранён для использования в API",
            parse_mode="HTML",
            reply_markup=create_main_menu()
        )
    else:
        await message.answer(
            "❌ Не удалось добавить кошелек. Убедитесь, что приватный ключ корректен и соответствует адресу.",
            reply_markup=create_main_menu()
        )

@dp.callback_query(F.data.startswith("wallet_"))
async def show_wallet_info(callback: CallbackQuery):
    """Показ информации о кошельке"""
    wallet_name = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id

    wallets = wallet_manager.get_user_wallets(user_id)
    if wallet_name in wallets:
        wallet_data = wallets[wallet_name]
        address = wallet_data.get('address', 'N/A')
        balance = wallet_manager.get_wallet_balance_solana(address)
        logger.info(f"Получен баланс для кошелька {wallet_name} ({address}): {balance} SOL")
        trade_percentage = wallet_data.get('trade_percentage', 1.0)
        profit_percentage = wallet_data.get('profit_percentage', 100.0)
        tokens_info = await get_purchased_tokens_info(user_id, wallet_name)
        purchased_tokens_info = ""
        if tokens_info:
            purchased_tokens_info = "\n\n📊 Купленные токены:\n"
            for token in tokens_info:
                purchased_tokens_info += (
                    f"{token['status']} <b>{token['name']} ({token['symbol']})</b> (<code>{token['address'][:6]}...{token['address'][-4:]}</code>)\n"
                    f"   💵 Цена покупки: ${token['purchase_price']:.6f}\n"
                    f"   💲 Текущая цена: ${token['current_price']:.6f}\n"
                    f"   📈 Множитель: x{token['multiplier']:.2f}\n"
                    f"   📊 Профит: {token['profit_percent']:.2f}%\n"
                    f"   🎯 Цели: x2(${token['target_x2']:.6f}), x3(${token['target_x3']:.6f}), x4(${token['target_x4']:.6f})\n\n"
                )
        else:
            purchased_tokens_info = "\n\n📭 Нет купленных токенов."
        text = (
            f"👛 <b>{wallet_name}</b>\n\n"
            f"📬 Адрес: <code>{address}</code>\n"
            f"💰 Баланс: <b>{balance} SOL</b>\n"
            f"📊 % от баланса на сделку: <b>{trade_percentage}%</b>\n"
            f"📈 % прибыли для выхода: <b>{profit_percentage}%</b> (x{(1 + profit_percentage/100):.2f})\n"
            f"{purchased_tokens_info}"
            f"\n📊 Действия:"
        )
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔄 Обновить баланс", callback_data=f"refresh_{wallet_name}")
        keyboard.button(text="📝 Подписать транзакцию", callback_data=f"sign_{wallet_name}")
        keyboard.button(text="⚙️ Настройки торговли", callback_data=f"settings_wallet_{wallet_name}")
        keyboard.button(text="⬅️ Назад", callback_data="my_wallet")
        keyboard.adjust(1)
        wallet_photo_path = get_photo_path("wallet.png")
        if os.path.exists(wallet_photo_path):
            photo = FSInputFile(wallet_photo_path)
            input_media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
            try:
                await callback.message.edit_media(
                    media=input_media,
                    reply_markup=keyboard.as_markup()
                )
            except TelegramBadRequest:
                await callback.message.edit_caption(
                    caption=text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML"
                )
        else:
            await callback.message.edit_caption(
                caption=text,
                reply_markup=keyboard.as_markup(),
                parse_mode="HTML"
            )
    await callback.answer()

@dp.callback_query(F.data.startswith("refresh_"))
async def refresh_balance(callback: CallbackQuery):
    """Обновление баланса"""
    wallet_name = callback.data.split("_", 1)[1]
    user_id = callback.from_user.id

    wallets = wallet_manager.get_user_wallets(user_id)
    if wallet_name in wallets:
        wallet_data = wallets[wallet_name]
        address = wallet_data.get('address', 'N/A')

        # Получаем баланс через RPC вызов
        balance = wallet_manager.get_wallet_balance_solana(address)

        logger.info(f"Обновлен баланс для кошелька {wallet_name} ({address}): {balance} SOL")

        # --- НОВОЕ: Получение настроек торговли ---
        trade_percentage = wallet_data.get('trade_percentage', 1.0) # По умолчанию 1%
        profit_percentage = wallet_data.get('profit_percentage', 100.0) # По умолчанию 100% (x2)
        # Рассчитываем множитель для отображения
        profit_multiplier = 1 + (profit_percentage / 100)

        # Обновляем сообщение с новым балансом
        # Получаем информацию о купленных токенах
        tokens_info = await get_purchased_tokens_info(user_id, wallet_name)
        purchased_tokens_info = ""

        if tokens_info:
            purchased_tokens_info = "\n\n📊 Купленные токены:\n"
            for token in tokens_info:
                purchased_tokens_info += (
                    f"{token['status']} <b>{token['name']} ({token['symbol']})</b> (<code>{token['address'][:6]}...{token['address'][-4:]}</code>)\n"
                    f"   💵 Цена покупки: ${token['purchase_price']:.6f}\n"
                    f"   💲 Текущая цена: ${token['current_price']:.6f}\n"
                    f"   📈 Множитель: x{token['multiplier']:.2f}\n"
                    f"   📊 Профит: {token['profit_percent']:.2f}%\n"
                    # Убираем старые цели x2, x3, x4, так как теперь используется одна цель
                    # f"   🎯 Цели: x2(${token['target_x2']:.6f}), x3(${token['target_x3']:.6f}), x4(${token['target_x4']:.6f})\n\n"
                    f"\n"
                )
        else:
            purchased_tokens_info = "\n\n📭 Нет купленных токенов."

        text = (
            f"👛 <b>{wallet_name}</b>\n\n"
            f"📬 Адрес: <code>{address}</code>\n"
            f"💰 Баланс: <b>{balance} SOL</b>\n"
            f"📊 % от баланса на сделку: <b>{trade_percentage}%</b>\n"
            f"📈 % прибыли для выхода: <b>{profit_percentage}%</b> (множитель x{profit_multiplier:.2f})\n"
            f"{purchased_tokens_info}"
            f"\n📊 Действия:"
        )

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🔄 Обновить баланс", callback_data=f"refresh_{wallet_name}")
        keyboard.button(text="📝 Подписать транзакцию", callback_data=f"sign_{wallet_name}")
        # --- НОВОЕ: Кнопка настроек торговли ---
        keyboard.button(text="⚙️ Настройки торговли", callback_data=f"settings_wallet_{wallet_name}")
        keyboard.button(text="⬅️ Назад", callback_data="my_wallet")
        keyboard.adjust(1) # Все кнопки в один столбец

        # --- ОБНОВЛЕНИЕ: Обработка как edit_caption, так и edit_text ---
        try:
             # Отправляем фото кошелька если есть
            wallet_photo_path = get_photo_path("wallet.png")
            if os.path.exists(wallet_photo_path):
                photo = FSInputFile(wallet_photo_path)
                input_media = InputMediaPhoto(media=photo, caption=text, parse_mode="HTML")
                await callback.message.edit_media(
                    media=input_media,
                    reply_markup=keyboard.as_markup()
                )
            else:
                 # Если фото нет, пытаемся отредактировать подпись
                await callback.message.edit_caption(
                    caption=text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode="HTML"
                )
        except TelegramBadRequest as e:
            # Если не удалось отредактировать подпись (например, сообщение без медиа),
            # пытаемся отредактировать текст
            if "no caption" in e.message.lower() or "message is not modified" in e.message.lower():
                try:
                    await callback.message.edit_text(
                        text=text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode="HTML"
                    )
                except TelegramBadRequest as e2:
                    logger.error(f"Не удалось отредактировать сообщение в refresh_balance (edit_text): {e2}")
                    # Можно отправить новое сообщение или показать уведомление
                    # await callback.answer("Не удалось обновить сообщение, попробуйте снова.", show_alert=True)
            else:
                # Если ошибка другая, логируем и пробрасываем
                logger.error(f"TelegramBadRequest в refresh_balance: {e}")
                raise
        except Exception as e:
            logger.error(f"Ошибка обновления сообщения в refresh_balance: {e}")
            await callback.answer("Ошибка обновления", show_alert=True)
            # Не возвращаемся, чтобы callback.answer() в конце всё равно выполнился

    await callback.answer("🔄 Баланс обновлен!", show_alert=True)


@dp.callback_query(F.data.startswith("sign_"))
async def sign_transaction(callback: CallbackQuery):
    """Подписание транзакции"""
    wallet_name = callback.data.split("_", 1)[1]
    await callback.answer(f"Подписание транзакции для кошелька {wallet_name}", show_alert=True)
    # Здесь будет логика подписания транзакций

@dp.callback_query(F.data == "stats")
async def show_stats(callback: CallbackQuery):
    """Показ статистики"""
    user_id = callback.from_user.id
    wallets = wallet_manager.get_user_wallets(user_id)
    
    total_wallets = len(wallets)
    total_balance = 0.0
    
    # Суммируем балансы всех кошельков
    for wallet_data in wallets.values():
        address = wallet_data.get('address', '')
        if address:
            balance = wallet_manager.get_wallet_balance_solana(address)
            total_balance += balance
    
    # Получаем информацию о купленных токенах
    total_tokens = 0
    total_profit = 0.0
    
    for wallet_name in wallets.keys():
        tokens_info = await get_purchased_tokens_info(user_id, wallet_name)
        total_tokens += len(tokens_info)
        
        for token in tokens_info:
            total_profit += token['profit_percent']
    
    avg_profit = total_profit / total_tokens if total_tokens > 0 else 0
    
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👛 Всего кошельков: <b>{total_wallets}</b>\n"
        f"💰 Общий баланс: <b>{total_balance:.6f} SOL</b>\n"
        f"🪙 Всего токенов: <b>{total_tokens}</b>\n"
        f"📈 Средний профит: <b>{avg_profit:.2f}%</b>\n"
        f"👤 Пользователь ID: <code>{user_id}</code>"
    )
    
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="⬅️ Назад", callback_data="main_menu")
    
    await callback.message.edit_caption(
        caption=text,
        reply_markup=keyboard.as_markup(),
        parse_mode="HTML"
    )
    await callback.answer()

@dp.callback_query()
async def handle_other_callbacks(callback: CallbackQuery):
    """Обработка неизвестных callback"""
    await callback.answer("❌ Неизвестная команда", show_alert=True)

@dp.inline_query()
async def inline_query_handler(inline_query: InlineQuery):
    """Обработка inline-запросов"""
    user_id = inline_query.from_user.id
    # --- ИЗМЕНЕНО: Проверка авторизации больше не обязательна для базовой информации ---
    # Получаем информацию о последнем найденном токене
    last_token_info = get_last_found_token_info()

    # Формируем результаты
    results = []

    # Результат с информацией о последнем токене (доступен всем)
    if last_token_info:
        try:
        # Предполагаем, что 'price_usd' хранится как строка или число в файле
            price_usd_raw = last_token_info['price_usd']
            price_usd_float = float(price_usd_raw)
            price_usd_formatted = f"{price_usd_float:.6f}"
        except (ValueError, TypeError, KeyError) as e:
            logging.warning(f"Невозможно преобразовать цену {last_token_info.get('price_usd')} в число для токена {last_token_info.get('address')}. Причина: {e}. Используется 'N/A'.")
            price_usd_formatted = "N/A"
        token_text = (
            f"🚀 <b>Последний найденный токен:</b>\n"
            f"🪙 <b>{last_token_info['name']} ({last_token_info['symbol']})</b>\n"
            f"📬 Адрес: <code>{last_token_info['address']}</code>\n"
            f"💰 Цена: ${price_usd_formatted}\n"  # <-- Используйте отформатированную цену
            f"🕒 Обнаружен: {last_token_info['discovered_at']}\n"
        )
        results.append(
            InlineQueryResultArticle(
                id="1",
                title=f"Новый токен: {last_token_info['symbol']}",
                input_message_content=InputTextMessageContent(
                    message_text=token_text,
                    parse_mode="HTML"
                ),
                description=f"{last_token_info['name']} | ${price_usd_formatted}", # <-- И здесь используем отформатированную цену
                thumb_url="https://public.bnbstatic.com/image/pgc/202309/346b46c784cd2703880e824e24acd0ef.png"
            )
        )
    else:
        results.append(
            InlineQueryResultArticle(
                id="1",
                title="Нет данных",
                input_message_content=InputTextMessageContent(
                    message_text="❌ Информация о последних токенах отсутствует."
                ),
                description="Нет данных о новых токенах",
                thumb_url="https://public.bnbstatic.com/image/pgc/202309/346b46c784cd2703880e824e24acd0ef.png"
            )
        )

    # Если пользователь авторизован, показываем его статистику
    if password_manager.is_user_authenticated(user_id):
        # Получаем баланс всех кошельков
        wallets = wallet_manager.get_user_wallets(user_id)
        total_balance = 0.0
        for wallet_data in wallets.values():
            address = wallet_data.get('address', '')
            if address:
                balance = wallet_manager.get_wallet_balance_solana(address)
                total_balance += balance
        # Получаем информацию о купленных токенах
        total_tokens = 0
        total_profit = 0.0
        for wallet_name in wallets.keys():
            tokens_info = await get_purchased_tokens_info(user_id, wallet_name)
            total_tokens += len(tokens_info)
            for token in tokens_info:
                total_profit += token['profit_percent']
        avg_profit = total_profit / total_tokens if total_tokens > 0 else 0

        results.append(
            InlineQueryResultArticle(
                id="2",
                title="Мой баланс",
                input_message_content=InputTextMessageContent(
                    message_text=f"💰 Ваш общий баланс: {total_balance:.6f} SOL\n"
                                 f"🪙 Куплено токенов: {total_tokens}\n"
                                 f"📈 Средний профит: {avg_profit:.2f}%"
                ),
                description=f"Баланс: {total_balance:.6f} SOL | Токены: {total_tokens}",
                thumb_url="https://coinspot.io/wp-content/uploads/2025/06/phantom-wallet-546056.png"
            )
        )
        results.append(
            InlineQueryResultArticle(
                id="3",
                title="Последние транзакции",
                input_message_content=InputTextMessageContent(
                    message_text="🔄 Последние транзакции:\n"
                                 "✅ Покупка токена WIF за 0.0001 SOL (2 мин. назад)\n"
                                 "✅ Продажа токена BONK за 0.0002 SOL (5 мин. назад)\n"
                                 "✅ Покупка токена Jito за 0.00015 SOL (10 мин. назад)"
                ),
                description="Просмотр последних транзакций",
                thumb_url="https://public.bnbstatic.com/image/pgc/202309/346b46c784cd2703880e824e24acd0ef.png"
            )
        )
    else:
         # Если не авторизован, предлагаем авторизоваться
        results.append(
            InlineQueryResultArticle(
                id="4",
                title="Вы не авторизованы",
                input_message_content=InputTextMessageContent(
                    message_text="❌ Вы не авторизованы. Используйте /start для входа в бота и получения доступа к вашей статистике."
                ),
                description="Для доступа к статистике необходима авторизация",
                thumb_url="https://fb.ru/misc/i/gallery/20380/2318041.jpg"
            )
        )

    await inline_query.answer(results, cache_time=10, is_personal=True) # is_personal=True для персонализированных результатов

async def run_bot(bot_instance: Bot):
    """Основная функция запуска бота"""
    logger.info("Запуск Solana Wallet Bot...")
    
    # Создаем директорию для фото если её нет
    if not os.path.exists(PHOTO_DIR):
        os.makedirs(PHOTO_DIR)
        logger.info(f"Создана директория {PHOTO_DIR} для фото")
    
    # Проверяем наличие токена
    if not config.config.get('bot_token') or config.config['bot_token'] == 'YOUR_BOT_TOKEN_HERE':
        logger.error("❌ Не найден токен бота в config.json")
        logger.info("Добавьте ваш токен в файл bot_config.json")
        return
    
    try:
        await dp.start_polling(bot_instance)
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}")
