import requests
import logging

from core.models import PropertyListing

def send_telegram_alert(bot_token: str, chat_id: str, property_data: PropertyListing, score: float, breakdown: dict):
    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    
    pros_text = "\n".join([f"✅ {p}" for p in breakdown.get("pros", [])])
    cons_text = "\n".join([f"⚠️ {c}" for c in breakdown.get("cons", [])])
    
    caption = (
        f"🚨 **High Match Property ({score}/100)** 🚨\n\n"
        f"📍 {property_data.display_address}\n"
        f"💷 £{property_data.price_pcm} pcm\n"
        f"🛏️ {property_data.bedrooms} Bed | 📏 {property_data.sqft or 'Unknown'} sqft\n"
        f"🏡 Type: {property_data.property_type}\n"
        f"🚆 Commute: {property_data.commute_mins or 'Unknown'} mins\n"
        f"📅 {property_data.listing_update or 'Date Unknown'}\n\n"
    )
    
    if pros_text:
        caption += f"**Pros:**\n{pros_text}\n\n"
    if cons_text:
        caption += f"**Cons:**\n{cons_text}\n\n"
        
    caption += f"🔗 [View on Rightmove]({property_data.url})"
    
    payload = {
        "chat_id": chat_id,
        "photo": property_data.images[0] if property_data.images else "",
        "caption": caption,
        "parse_mode": "Markdown"
    }
    
    try:
        requests.post(url, json=payload)
        logging.info(f"Alert sent for {property_data.id}")
    except Exception as e:
        logging.error(f"Failed to send Telegram alert: {e}")
