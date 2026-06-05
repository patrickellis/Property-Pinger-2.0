import logging
import os

import yaml

from core.db import check_and_log_property  # (The Firestore logic we discussed earlier)
from evaluators.scoring import calculate_match_score, passes_dealbreakers
from evaluators.vision import evaluate_property_images, extract_square_footage
from scrapers.listing import extract_rightmove_data_via_api
from scrapers.search import fetch_search_results
from services.maps import get_commute_times
from services.telegram import send_telegram_alert

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def load_config():
    with open("config.yaml", "r") as f:
        return yaml.safe_load(f)


def run_pipeline():
    config = load_config()

    # Load secrets from environment (injected by GCP Secret Manager)
    scraper_key = os.environ["SCRAPER_API_KEY"]
    telegram_token = os.environ["TELEGRAM_BOT_TOKEN"]
    telegram_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    for search_url in config["search_urls"]:
        logging.info(f"Processing search batch: {search_url}")
        listing_urls = fetch_search_results(search_url, scraper_key, max_pages=config.get("max_search_pages", 10))

        for url in listing_urls:
            # 1. Extract raw data
            property_data = extract_rightmove_data_via_api(url, scraper_key)
            if not property_data:
                continue

            # 2. Hard Filters (Zero/Low Cost)
            if not passes_dealbreakers(property_data, config):
                logging.info(
                    f"Property {property_data['id']} failed dealbreakers. Skipping."
                )
                continue

            # 3. Check State (Firestore Idempotency)
            # If we've seen it and it passed dealbreakers before, skip it.
            if not check_and_log_property(property_data["id"]):
                continue

            # 4. Extract SqFt via Gemini (Fast Path)
            property_data["sqft"] = extract_square_footage(
                property_data.get("floorplans", [])
            )

            # 4.5 Check sqft specific dealbreaker
            if property_data.get("sqft", 0) > 0 and property_data["sqft"] < config["dealbreakers"].get("min_total_sqft", 0):
                logging.info(
                    f"Property {property_data['id']} failed sqft dealbreaker. Skipping."
                )
                continue

            # ...
            # 5. Heavy Evaluation
            visual_metrics = evaluate_property_images(
                property_data["images"], property_data.get("description", "")
            )

            # Fetch commute times (Assuming you added Google Maps API to GCP Secret Manager)
            commute_metrics = get_commute_times(
                origin_lat=property_data.get("latitude"),
                origin_lng=property_data.get("longitude"),
                destinations=config["locations"]["hubs"]
                + config["locations"]["venues"],
                mode=config["locations"]["transit_mode"],
                api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
            )

            # 6. Final Scoring (Pass the commute metrics in)
            final_score = calculate_match_score(
                property_data, visual_metrics, commute_metrics, config
            )


if __name__ == "__main__":
    run_pipeline()
