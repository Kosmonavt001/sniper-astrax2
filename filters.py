# solana_monitor/filters.py

import json
import requests
import logging
from datetime import datetime, timezone
import time
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logger = logging.getLogger(__name__)

# --- КОНФИГУРАЦИЯ ГРУППЫ ---
GROUP_CHAT_ID = "-1003071618300"
TOPIC_MESSAGE_THREAD_ID = 61  # ID темы/топика внутри группы

def check_liquidity_and_sellability(token_address: str, sol_mint: str) -> dict:
    """
    Проверяет ликвидность токена и возможность его продажи.
    Возвращает словарь с информацией о ликвидности и цене.
    """
    try:
        url = f"https://quote-api.jup.ag/v6/quote?inputMint={token_address}&outputMint={sol_mint}&amount=1"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if "routePlan" in data and len(data["routePlan"]) > 0:
                out_amount = data.get('outAmount', '0')
                if out_amount.isdigit():
                    price_per_token_sol = int(out_amount) / 1_000_000_000
                    return {
                        "has_liquidity": True,
                        "price_per_token_sol": price_per_token_sol,
                        "message": "Токен имеет ликвидность"
                    }
        
        dexscreener_url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        dex_response = requests.get(dexscreener_url, timeout=10)
        
        if dex_response.status_code == 200:
            dex_data = dex_response.json()
            pairs = dex_data.get("pairs", [])
            for pair in pairs:
                quote_token = pair.get("quoteToken", {})
                if quote_token.get("symbol", "").upper() == "SOL":
                    return {
                        "has_liquidity": True,
                        "price_per_token_sol": 0,
                        "message": "Ликвидность подтверждена через DexScreener"
                    }
        
        return {
            "has_liquidity": False,
            "price_per_token_sol": 0,
            "message": "Нет ликвидности или пула"
        }
        
    except Exception as e:
        logger.error(f"Ошибка проверки ликвидности для {token_address}: {e}")
        return {
            "has_liquidity": False,
            "price_per_token_sol": 0,
            "message": "Ошибка сети"
        }


def check_token_via_rugcheck(token_address: str) -> dict:
    """
    Проверяет токен через RugCheck.xyz API.
    Возвращает полный отчет.
    """
    try:
        rugcheck_api_url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        response = requests.get(rugcheck_api_url, headers=headers, timeout=15)

        if response.status_code != 200:
            logger.warning(f"RugCheck API вернул статус {response.status_code} для {token_address}")
            return {"error": f"Status {response.status_code}", "has_report": False}

        data = response.json()
        return {
            "has_report": True,
            "data": data
        }

    except Exception as e:
        logger.error(f"Ошибка при обращении к RugCheck API для {token_address}: {e}")
        return {"error": str(e), "has_report": False}


def check_token_scam_risk(token_address: str) -> dict:
    """
    Проверяет токен на риск скама через DexScreener и RugCheck.
    Добавляет новые фильтры: крупный держатель и минимальный рейтинг.
    """
    try:
        url = f"https://api.dexscreener.com/tokens/v1/solana/{token_address}"
        response = requests.get(url, timeout=30)
        
        if response.status_code != 200 or not response.text.strip():
            return {
                "risk_level": "HIGH",
                "risk_reason": "Не удалось получить данные от DexScreener",
                "message": "Ошибка получения информации о токене",
                "has_pairs": False
            }
            
        try:
            data = response.json()
        except json.JSONDecodeError:
            return {
                "risk_level": "HIGH",
                "risk_reason": "Некорректный JSON от DexScreener",
                "message": "Ошибка парсинга данных",
                "has_pairs": False
            }

        if not data:
            return {
                "risk_level": "HIGH",
                "risk_reason": "Нет пулов на DEX",
                "message": "Токен не найден на основных DEX",
                "has_pairs": False
            }
            
        first_pair = data[0]
        base_token = first_pair.get("baseToken", {})
        quote_token = first_pair.get("quoteToken", {})

        liquidity_usd = first_pair.get("liquidity", {}).get("usd", 0)
        volume_24h = first_pair.get("volume", {}).get("h24", 0)

        created_at = first_pair.get("pairCreatedAt", 0)
        if created_at:
            created_time = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
            created_str = created_time.strftime("%Y-%m-%d %H:%M UTC")
        else: 
            created_str = "Неизвестно"

        risk_score = 0
        risk_reasons = []

        # --- ОСНОВНЫЕ ФИЛЬТРЫ ИЗ DEXSCREENER ---
        if liquidity_usd < 10000:
            risk_score += 3
            risk_reasons.append(f"Низкая ликвидность (${liquidity_usd:,.0f})")

        if volume_24h < 50000:
            risk_score += 2
            risk_reasons.append(f"Низкий объем торговли (${volume_24h:,.0f})")

        if not base_token.get("name") or not base_token.get("symbol"):
            risk_score += 4
            risk_reasons.append("Нет метаданных токена")

        txns = first_pair.get("txns", {})
        h1_buys = txns.get("h1", {}).get("buys", 0)
        h1_sells = txns.get("h1", {}).get("sells", 0)
        if h1_sells > 0 and h1_buys > 0:
            buy_sell_ratio = h1_buys / h1_sells
            if buy_sell_ratio < 1.2:
                risk_score += 2
                risk_reasons.append("Высокая продажная активность")

        socials = first_pair.get("info", {}).get("socials", [])
        if not socials:
            risk_score += 1
            risk_reasons.append("Нет социальных ссылок")

        price_change_h1 = first_pair.get("priceChange", {}).get("h1", 0)
        if price_change_h1 > 300:
            risk_score += 2
            risk_reasons.append(f"Высокая волатильность (+{price_change_h1}% за час)")

        # --- НОВЫЕ ФИЛЬТРЫ ЧЕРЕЗ RUGCHECK ---
        rugcheck_result = check_token_via_rugcheck(token_address)
        
        if rugcheck_result.get("has_report"):
            report = rugcheck_result["data"]
            
            # 1. Фильтр: Один держатель владеет более чем 20%
            top_holder_pct = 0
            if "topHolders" in report and report["topHolders"]:
                top_holder_pct = report["topHolders"][0].get("pct", 0)
                
            if top_holder_pct > 20:
                risk_score += 5
                risk_reasons.append(f"⚠️ Один держатель владеет {top_holder_pct:.2f}% всех токенов (>20%)")

            # 2. Фильтр: RugCheck Score ниже 90
            score_normalised = report.get("score_normalised", 100)
            if score_normalised < 90:
                risk_score += 3
                risk_reasons.append(f"❌ Низкий рейтинг RugCheck ({score_normalised}/100)")
            elif score_normalised < 100:
                risk_reasons.append(f"🟡 Рейтинг RugCheck ({score_normalised}/100)")

            # 3. Дополнительные риски из отчета
            for risk in report.get("risks", []):
                if risk.get("level") == "danger":
                    risk_score += 3
                    risk_reasons.append(f"🚨 {risk['name']}: {risk.get('value', '')} - {risk['description']}")
                elif risk.get("level") == "warn":
                    risk_score += 1
                    risk_reasons.append(f"⚠️ {risk['name']}: {risk['description']}")

        elif "error" in rugcheck_result:
            risk_reasons.append(f"⚠️ RugCheck: Не удалось проверить ({rugcheck_result['error']})")

        # --- ОПРЕДЕЛЕНИЕ УРОВНЯ РИСКА ---
        if risk_score >= 8:
            risk_level = "HIGH"
            risk_message = "Высокий риск скама"
        elif risk_score >= 4:
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
            "created_at": created_str,
            "token_name": base_token.get("name", "Unknown"),
            "token_symbol": base_token.get("symbol", "UNKNOWN"),
            "quote_token": quote_token.get("symbol", "SOL"),
            "dex_id": first_pair.get("dexId", "Unknown"),
            "price_change_h1": price_change_h1,
            "price_usd": first_pair.get("priceUsd", 0),
            "has_socials": len(socials) > 0,
            "rugcheck_result": rugcheck_result,
            "top_holder_pct": top_holder_pct if 'top_holder_pct' in locals() else 0
        }
            
    except Exception as e:
        logger.error(f"Ошибка проверки токена {token_address} на скам: {e}")
        return {
            "risk_level": "UNKNOWN",
            "risk_reason": "Ошибка сети",
            "message": "Не удалось проверить токен",
            "has_pairs": False
        }


def is_potential_scam(token_address: str, token_name: str, token_symbol: str) -> bool:
    """
    Проверяет, является ли токен потенциально скамом.
    Возвращает True, если токен считается скамом.
    """
    logging.info(f'началась проверка токена {token_address}')
    
    scam_info = check_token_scam_risk(token_address)
    
    # Если нет пулов, высокий риск, ИЛИ один держатель >20%, ИЛИ рейтинг <90
    if not scam_info["has_pairs"] or scam_info["risk_level"] == "HIGH":
        return True

    # Дополнительные простые проверки
    scam_indicators = [
        "test" in token_name.lower(),
        "test" in token_symbol.lower(),
        "fake" in token_name.lower(),
        "fake" in token_symbol.lower(),
        "scam" in token_name.lower(),
        "scam" in token_symbol.lower(),
        "honeypot" in token_name.lower(),
        "honeypot" in token_symbol.lower(),
        len(token_name) > 50,
        len(token_symbol) > 10,
    ]

    return any(scam_indicators)


# --- НОВАЯ ФУНКЦИЯ: ОТПРАВКА АНАЛИЗА В ГРУППУ ---
async def send_token_analysis_to_group(bot: Bot, token_address: str):
    """
    Отправляет анализ токена в указанную Telegram-группу.
    """
    scam_info = check_token_scam_risk(token_address)

    risk_emoji = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢", "UNKNOWN": "🟠"}.get(scam_info["risk_level"], "❓")

    message_text = (
        f"🔍 <b>Проверка нового токена</b>\n"
        f"📛 Название: <b>{scam_info['token_name']}</b> ({scam_info['token_symbol']})\n"
        f"🔗 Адрес: <code>{token_address}</code>\n"
        f"🕒 <b>Дата создания пула:</b> {scam_info['created_at']}\n"
        f"{risk_emoji} <b>Риск скама:</b> {scam_info['message']}\n"
        f"📊 <b>Ликвидность:</b> ${scam_info['liquidity_usd']:,.0f}\n"
        f"📈 <b>Объем (24ч):</b> ${scam_info['volume_24h']:,.0f}\n"
        f"💰 <b>Цена:</b> ${float(scam_info['price_usd']):.8f} USD"
    )

    rugcheck = scam_info.get("rugcheck_result", {})
    if rugcheck.get("has_report"):
        report = rugcheck["data"]
        score = report.get("score_normalised", 0)
        message_text += f"\n⭐ <b>RugCheck Score:</b> {score}/100"
        
        top_holder = scam_info.get("top_holder_pct", 0)
        if top_holder > 0:
            color = "🔴" if top_holder > 20 else "🟡"
            message_text += f"\n👑 <b>Топ держатель:</b> {color} {top_holder:.2f}%"
    else:
        message_text += "\n⭐ <b>RugCheck:</b> Не удалось получить отчет"

    message_text += f"\n🚫 <b>Фильтры пройдены:</b> {'❌ Нет' if scam_info['risk_level'] == 'HIGH' else '✅ Да'}"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Посмотреть на DexScreener", url=f"https://dexscreener.com/solana/{token_address}")],
        [InlineKeyboardButton(text="🔍 Solscan", url=f"https://solscan.io/token/{token_address}")],
        [InlineKeyboardButton(text="🛡 Проверить на RugCheck", url=f"https://rugcheck.xyz/tokens/{token_address}")],
        [InlineKeyboardButton(text="🔄 Обновить цену", callback_data=f"refresh_{token_address}")]
    ])

    try:
        await bot.send_message(
            chat_id=GROUP_CHAT_ID,
            message_thread_id=TOPIC_MESSAGE_THREAD_ID,
            text=message_text,
            reply_markup=keyboard,
            parse_mode="HTML"
        )
        logger.info(f"Отправлен анализ токена {token_address} в группу.")
    except Exception as e:
        logger.error(f"Ошибка отправки анализа токена {token_address} в группу: {e}")
