import requests
import logging

def send_telegram_alert(bot_token: str, chat_id: str, property_data: dict, score: float):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    
    caption = (
        f"🚨 **High Match Property ({score}/100)** 🚨\n\n"
        f"📍 {property_data['display_address']}\n"
        f"💷 £{property_data['price_pcm']} pcm\n"
        f"🛏️ {property_data['bedrooms']} Bed | 📏 {property_data.get('sqft', 'Unknown')} sqft\n"
        f"🏡 Type: {property_data['property_type']}\n\n"
        f"🔗 [View on Rightmove]({property_data['url']})"
    )
    
    payload = {
        "chat_id": chat_id,
        "photo": property_data['images'][0] if property_data['images'] else "",
        "caption": caption,
        "parse_mode": "Markdown"
    }
    
    try:
        requests.post(url, json=payload)
        logging.info(f"Alert sent for {property_data['id']}")
    except Exception as e:
        logging.error(f"Failed to send Telegram alert: {e}")
