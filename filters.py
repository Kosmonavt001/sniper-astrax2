# solana_monitor/filters.py

import requests
import logging
from datetime import datetime, timezone
import time

logger = logging.getLogger(__name__)

def check_liquidity_and_sellability(token_address: str, sol_mint: str) -> dict:
    """
    Проверяет ликвидность токена и возможность его продажи.
    Возвращает словарь с информацией о ликвидности и цене.
    """
    try:
        # Используем Jupiter API для проверки пулов
        url = f"https://quote-api.jup.ag/v6/quote?inputMint={token_address}&outputMint={sol_mint}&amount=1"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Проверяем, есть ли маршрут
            if "routePlan" in data and len(data["routePlan"]) > 0:
                # Попробуем получить цену из первого шага маршрута
                out_amount = data.get('outAmount', '0')
                if out_amount.isdigit():
                    price_per_token_sol = int(out_amount) / 1_000_000_000
                    return {
                        "has_liquidity": True,
                        "price_per_token_sol": price_per_token_sol,
                        "message": "Токен имеет ликвидность"
                    }
        
        # Если Jupiter API не работает, используем DexScreener как резерв
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
                        "price_per_token_sol": 0,  # Не можем точно определить цену
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

def check_token_scam_risk(token_address: str) -> dict:
    """
    Проверяет токен на риск скама через DexScreener API
    Возвращает информацию о токене и его риске
    
    """
    try:
        # Используем DexScreener API для получения информации о парах токена
        url = f"https://api.dexscreener.com/tokens/v1/solana/{token_address}"
        response = requests.get(url, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            
            # Проверяем, есть ли пары для токена
            if not data:
                return {
                    "risk_level": "HIGH",
                    "risk_reason": "Нет пулов на DEX",
                    "message": "Токен не найден на основных DEX",
                    "has_pairs": False
                }
            
            # Берем первую пару для анализа
            first_pair = data[0]
            
            # Получаем информацию о токене
            base_token = first_pair.get("baseToken", {})
            quote_token = first_pair.get("quoteToken", {})
            
            # Проверяем ликвидность
            liquidity_usd = first_pair.get("liquidity", {}).get("usd", 0)
            volume_24h = first_pair.get("volume", {}).get("h24", 0)
            
            # Проверяем время создания
            created_at = first_pair.get("pairCreatedAt", 0)
            if created_at:
                created_time = datetime.fromtimestamp(created_at / 1000, tz=timezone.utc)
                time_diff = datetime.now(timezone.utc) - created_time
                age_hours = time_diff.total_seconds() / 3600
            else:
                age_hours = 0
            
            # Оценка риска
            risk_score = 0
            risk_reasons = []
            
            # Риск 1: Низкая ликвидность
            if liquidity_usd < 10000:  # Меньше $10,000 ликвидности
                risk_score += 3
                risk_reasons.append(f"Низкая ликвидность (${liquidity_usd:,.0f})")
            
            # Риск 2: Низкий объем торговли
            if volume_24h < 50000:  # Меньше $50,000 объема за 24 часа
                risk_score += 2
                risk_reasons.append(f"Низкий объем торговли (${volume_24h:,.0f})")
            
            # УДАЛЕНО: Риск "слишком новый токен"
            # Мы ищем новые токены, поэтому это не является риском
            
            # Риск 3: Нет информации о токене
            if not base_token.get("name") or not base_token.get("symbol"):
                risk_score += 4
                risk_reasons.append("Нет метаданных токена")
            
            # Риск 4: Плохое соотношение покупок/продаж
            txns = first_pair.get("txns", {})
            h1_buys = txns.get("h1", {}).get("buys", 0)
            h1_sells = txns.get("h1", {}).get("sells", 0)
            if h1_sells > 0 and h1_buys > 0:
                buy_sell_ratio = h1_buys / h1_sells
                if buy_sell_ratio < 1.2:  # Покупок меньше чем продаж
                    risk_score += 2
                    risk_reasons.append("Высокая продажная активность")
            
            # Риск 5: Отсутствие социальных ссылок
            socials = first_pair.get("info", {}).get("socials", [])
            if not socials:
                risk_score += 1
                risk_reasons.append("Нет социальных ссылок")
            
            # Риск 6: Высокая волатильность
            price_change_h1 = first_pair.get("priceChange", {}).get("h1", 0)
            if price_change_h1 > 300:  # Рост более чем на 300% за час
                risk_score += 2
                risk_reasons.append(f"Высокая волатильность (+{price_change_h1}% за час)")
            
            # Определение уровня риска
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
                "age_hours": age_hours,
                "token_name": base_token.get("name", "Unknown"),
                "token_symbol": base_token.get("symbol", "UNKNOWN"),
                "quote_token": quote_token.get("symbol", "SOL"),
                "dex_id": first_pair.get("dexId", "Unknown"),
                "price_change_h1": first_pair.get("priceChange", {}).get("h1", 0),
                "price_usd": first_pair.get("priceUsd", 0),
                "has_socials": len(socials) > 0
            }
        else:
            return {
                "risk_level": "HIGH",
                "risk_reason": "Не удалось получить данные от DexScreener",
                "message": "Ошибка получения информации о токене",
                "has_pairs": False
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
    Проверяет, является ли токен потенциальным скамом.
    Возвращает True, если токен считается скамом.
    """
    logging.info(f'началась проверка токена {token_address}')
    # Проверяем через DexScreener API
    scam_info = check_token_scam_risk(token_address)
    
    # Если не удалось получить данные или высокий риск
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
        len(token_name) > 50,  # Слишком длинное имя
        len(token_symbol) > 10,  # Слишком длинный символ
    ]
    
    # Если есть хотя бы один индикатор скама
    if any(scam_indicators):
        return True
    
    return False