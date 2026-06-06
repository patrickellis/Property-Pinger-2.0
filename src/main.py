import logging
import os

import yaml

import re
from datetime import datetime, timezone
from core.db import get_property_cache, save_scraped_data, mark_evaluated, get_config_mtime
from evaluators.scoring import calculate_match_score, passes_dealbreakers
from evaluators.vision import evaluate_property_images, extract_floorplan_details
from scrapers.listing import extract_rightmove_data_via_api
from scrapers.search import fetch_search_results
from services.maps import get_commute_times, check_noise_pollution
from services.telegram import send_telegram_alert
from core.models import PropertyListing

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logging.getLogger("google").setLevel(logging.WARNING)
logging.getLogger("google.generativeai").setLevel(logging.WARNING)
logging.getLogger("absl").setLevel(logging.WARNING)


def deep_merge(d1, d2):
    """Recursively merge dictionary d2 into d1."""
    for k, v in d2.items():
        if isinstance(v, dict) and k in d1 and isinstance(d1[k], dict):
            deep_merge(d1[k], v)
        else:
            d1[k] = v
    return d1

def load_config():
    # Load defaults
    with open("config/defaults.yaml", "r") as f:
        config = yaml.safe_load(f) or {}

    # Load specific config, defaulting to london.yaml
    config_path = os.environ.get("CONFIG_PATH", "config/london.yaml")
    with open(config_path, "r") as f:
        specific_config = yaml.safe_load(f) or {}

    return deep_merge(config, specific_config)


def evaluate_single_property(url, config, scraper_key, telegram_token, telegram_chat_id, config_mtime, ignore_threshold):
    match = re.search(r'/properties/(\d+)', url)
    if not match:
        return
    property_id = match.group(1)

    # Check cache
    doc_data = get_property_cache(property_id)
    if doc_data.get("ignored"):
        return

    scraped_at = doc_data.get("scraped_at")
    evaluated_at = doc_data.get("evaluated_at")
    
    needs_scrape = True
    needs_eval = True

    now = datetime.now(timezone.utc)
    if scraped_at and (now - scraped_at).days < 14:
        if doc_data.get("raw_data"):
            needs_scrape = False
            property_data = doc_data["raw_data"]

    if evaluated_at and evaluated_at >= config_mtime and not needs_scrape:
        needs_eval = False

    if not needs_eval:
        return

    if needs_scrape:
        property_data = extract_rightmove_data_via_api(url, scraper_key)
        if not property_data:
            return
        save_scraped_data(property_id, property_data)

    # 2. Hard Filters (Zero/Low Cost)
    if not passes_dealbreakers(property_data, config):
        mark_evaluated(property_id, ignored=True, property_data=property_data)
        return

    # 4. Extract Floorplan Details via Gemini (Fast Path)
    floorplan_details = extract_floorplan_details(
        property_data.floorplans,
        property_data.description
    )
    property_data.sqft = floorplan_details.total_sqft
    property_data.reception_length_m = floorplan_details.reception_length_m
    property_data.reception_on_ground_floor = floorplan_details.reception_on_ground_floor
    property_data.max_ceiling_height_m = floorplan_details.max_ceiling_height_m
    property_data.floor_level = floorplan_details.floor_level
    property_data.has_lift = floorplan_details.has_lift
    property_data.master_bedroom_length_m = floorplan_details.master_bedroom_length_m

    # 5. Heavy Evaluation
    visual_metrics = evaluate_property_images(
        property_data.images, property_data.description
    )

    # Fetch commute times (Assuming you added Google Maps API to GCP Secret Manager)
    commute_metrics = get_commute_times(
        origin_lat=property_data.latitude,
        origin_lng=property_data.longitude,
        destinations=config["locations"]["hubs"]
        + config["locations"]["venues"],
        mode=config["locations"]["transit_mode"],
        api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
    )

    # 6. Final Scoring (Pass the commute metrics in)
    property_data.is_noisy_location = check_noise_pollution(
        property_data.latitude, property_data.longitude
    )

    final_score, breakdown = calculate_match_score(
        property_data, visual_metrics, commute_metrics, config
    )

    # Prepare Rich Logging Data
    cons = " | ".join(breakdown.get("cons", [])) or "None"
    pros = " | ".join(breakdown.get("pros", [])) or "None"
    scorecard = breakdown.get("scorecard", {})
    score_str = " | ".join(f"{k}: {v}" for k, v in scorecard.items())

    is_ignored = final_score < ignore_threshold
    mark_evaluated(
        property_id, 
        ignored=is_ignored, 
        score=final_score, 
        breakdown=breakdown, 
        property_data=property_data
    )
    
    if final_score >= config.get("alert_threshold", 70):
        status_msg = "Dispatching Telegram alert"
        send_telegram_alert(telegram_token, telegram_chat_id, property_data, final_score, breakdown)
    elif is_ignored:
        status_msg = f"Ignored (score < {ignore_threshold})"
    else:
        status_msg = "Below alert threshold"

    logging.info(
        f"[{property_id}] Scored {final_score:.1f} - {status_msg}.\n"
        f"  Scorecard: {score_str}\n"
        f"  Pros: [{pros}] | Cons: [{cons}]"
    )

    if is_ignored:
        return

def run_pipeline():
    import concurrent.futures

    config = load_config()

    # Load secrets from environment (injected by GCP Secret Manager)
    required_vars = ["SCRAPER_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
    for var in required_vars:
        if var not in os.environ:
            raise ValueError(f"Missing required environment variable: {var}")

    scraper_key = os.environ["SCRAPER_API_KEY"]
    telegram_token = os.environ["TELEGRAM_BOT_TOKEN"]
    telegram_chat_id = os.environ["TELEGRAM_CHAT_ID"]

    config_mtime = get_config_mtime()
    ignore_threshold = config.get("ignore_threshold", 50)

    all_urls = []
    for search_url in config["search_urls"]:
        logging.info(f"Processing search batch: {search_url}")
        listing_urls = fetch_search_results(search_url, scraper_key, max_pages=config.get("max_search_pages", 20))
        all_urls.extend(listing_urls)
        
    all_urls = list(set(all_urls))
    logging.info(f"Found {len(all_urls)} unique properties to process across all searches.")

    # Process concurrently using ThreadPoolExecutor
    # max_workers=10 runs APIs in parallel without immediate extreme ratelimiting.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = []
        for url in all_urls:
            futures.append(
                executor.submit(
                    evaluate_single_property,
                    url, config, scraper_key, telegram_token, telegram_chat_id, config_mtime, ignore_threshold
                )
            )
            
        for future in concurrent.futures.as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logging.exception("Property evaluation thread failed")


if __name__ == "__main__":
    run_pipeline()
