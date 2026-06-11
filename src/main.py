import logging
import os

import yaml

import re
from datetime import datetime, timezone
from core.db import get_property_cache, save_scraped_data, mark_evaluated, get_config_mtime, get_all_known_property_ids, find_duplicate_property
from evaluators.scoring import calculate_match_score, passes_dealbreakers
from evaluators.vision import evaluate_property_images, extract_floorplan_details, PropertyVisuals
from scrapers.listing import extract_rightmove_data_via_api, extract_zoopla_data_via_api
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
    match = re.search(r'/properties/(\d+)', url) or re.search(r'/(\d{6,})', url)
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
    if scraped_at and (now - scraped_at).days < 7:
        if doc_data.get("raw_data"):
            needs_scrape = False
            property_data = doc_data["raw_data"]

    if evaluated_at and evaluated_at >= config_mtime and not needs_scrape:
        needs_eval = False

    if not needs_eval:
        return

    if needs_scrape:
        if "zoopla.co.uk" in url:
            property_data = extract_zoopla_data_via_api(url, scraper_key)
        else:
            property_data = extract_rightmove_data_via_api(url, scraper_key)
            
        if not property_data:
            return
            
        duplicate_id, existing_data = find_duplicate_property(property_data)
        if duplicate_id and existing_data:
            logging.info(f"[{property_id}] Skipping - duplicate of {duplicate_id}")
            
            updated_existing = False
            existing_raw = existing_data.get('raw_data', {})
            
            # Enrich existing duplicate with missing floorplans
            if not existing_raw.get('floorplans') and property_data.floorplans:
                existing_raw['floorplans'] = property_data.floorplans
                updated_existing = True
                logging.info(f"[{duplicate_id}] Enriched with floorplan from duplicate {property_id}")
                
            # Enrich existing duplicate with better images
            if len(property_data.images) > len(existing_raw.get('images', [])):
                existing_raw['images'] = property_data.images
                updated_existing = True
                logging.info(f"[{duplicate_id}] Enriched with {len(property_data.images)} images from duplicate {property_id}")
                
            if updated_existing:
                from google.cloud import firestore
                from core.db import collection_ref
                try:
                    # Update raw_data and trigger re-evaluation
                    collection_ref.document(str(duplicate_id)).update({
                        'raw_data': existing_raw,
                        'evaluated_at': firestore.DELETE_FIELD
                    })
                except Exception as e:
                    logging.error(f"Failed to merge duplicate data: {e}")

            property_data.user_note = f"Duplicate of {duplicate_id}"
            mark_evaluated(property_id, ignored=True, property_data=property_data)
            return
            
        save_scraped_data(property_id, property_data)
        
    # Copy over user_note from cache if it exists so we don't wipe it out
    if doc_data.get("user_note"):
        property_data.user_note = doc_data.get("user_note")

    # 2. Hard Filters (Zero/Low Cost)
    passed, reason = passes_dealbreakers(property_data, config)
    if not passed:
        existing_note = doc_data.get("user_note", "")
        if not existing_note:
            property_data.user_note = f"Auto-Ignored: {reason}"
        else:
            property_data.user_note = existing_note
            
        mark_evaluated(property_id, ignored=True, property_data=property_data)
        return

    # --- CACHE BYPASS CHECKS ---
    old_raw = doc_data.get("raw_data")
    if old_raw and isinstance(old_raw, PropertyListing):
        images_changed = len(property_data.images) != old_raw.image_count
        floorplans_changed = len(property_data.floorplans) != old_raw.floorplan_count
        location_changed = (property_data.latitude != old_raw.latitude) or (property_data.longitude != old_raw.longitude)
    else:
        images_changed = True
        floorplans_changed = True
        location_changed = True

    property_data.image_count = len(property_data.images)
    property_data.floorplan_count = len(property_data.floorplans)

    # 4. Extract Floorplan Details via Gemini (Fast Path)
    if floorplans_changed or not old_raw or not getattr(old_raw, 'floorplan_graph', None):
        floorplan_details = extract_floorplan_details(
            property_data.floorplans,
            property_data.description
        )
        
        sqft_match = re.search(r'(\d[,.\d]*)\s*(sq\s*ft|square\s*feet|sqft)', property_data.description, re.IGNORECASE)
        sqft_from_desc = 0
        if sqft_match:
            try:
                sqft_from_desc = int(float(sqft_match.group(1).replace(',', '')))
            except:
                pass
                
        property_data.sqft = floorplan_details.total_sqft or sqft_from_desc
        property_data.reception_length_m = floorplan_details.reception_length_m
        property_data.reception_on_ground_floor = floorplan_details.reception_on_ground_floor
        property_data.max_ceiling_height_m = floorplan_details.max_ceiling_height_m
        property_data.floor_level = floorplan_details.floor_level
        
        # Fallback to regex since Gemini short-circuits when floorplans are missing
        has_lift_from_desc = bool(re.search(r'\b(lift|elevator)\b', property_data.description, re.IGNORECASE))
        property_data.has_lift = floorplan_details.has_lift or has_lift_from_desc
        
        property_data.master_bedroom_length_m = floorplan_details.master_bedroom_length_m
        if floorplan_details.floorplan_graph:
            property_data.floorplan_graph = floorplan_details.floorplan_graph.model_dump()
        else:
            property_data.floorplan_graph = None
        
        property_data.has_ac = bool(re.search(r'\b(air conditioning|air-conditioning|a/c|ac|climate control|air-con|aircon)\b', property_data.description, re.IGNORECASE))
        property_data.has_underfloor_heating = bool(re.search(r'\b(underfloor heating|under floor heating|under-floor heating|ufh|underfloor|radiant floor|heated floor)\b', property_data.description, re.IGNORECASE))
    else:
        logging.info(f"[{property_id}] Skipping Gemini Floorplan (cache hit)")
        property_data.sqft = old_raw.sqft
        property_data.reception_length_m = old_raw.reception_length_m
        property_data.reception_on_ground_floor = old_raw.reception_on_ground_floor
        property_data.max_ceiling_height_m = old_raw.max_ceiling_height_m
        property_data.floor_level = old_raw.floor_level
        property_data.has_lift = old_raw.has_lift
        property_data.master_bedroom_length_m = old_raw.master_bedroom_length_m
        property_data.floorplan_graph = getattr(old_raw, 'floorplan_graph', None)
        property_data.has_ac = getattr(old_raw, 'has_ac', None)
        property_data.has_underfloor_heating = getattr(old_raw, 'has_underfloor_heating', None)

    # 5. Heavy Evaluation (Visuals)
    if images_changed or not old_raw or old_raw.natural_light_score is None:
        visual_metrics = evaluate_property_images(
            property_data.images, property_data.description
        )
        property_data.natural_light_score = visual_metrics.natural_light_score
        property_data.is_period_property = visual_metrics.is_period_property
        property_data.has_sash_windows = visual_metrics.has_sash_windows
        property_data.has_large_windows = visual_metrics.has_large_windows
        property_data.exterior_material = visual_metrics.exterior_material
        property_data.aesthetic_verdict = visual_metrics.aesthetic_verdict
        property_data.has_virtual_staging = visual_metrics.has_virtual_staging
        property_data.has_wide_angle_distortion = visual_metrics.has_wide_angle_distortion
        property_data.epc_rating = visual_metrics.epc_rating
    else:
        logging.info(f"[{property_id}] Skipping Gemini Vision (cache hit)")
        visual_metrics = PropertyVisuals(
            natural_light_score=old_raw.natural_light_score or 5,
            is_period_property=old_raw.is_period_property or False,
            has_sash_windows=old_raw.has_sash_windows or False,
            has_large_windows=old_raw.has_large_windows or False,
            exterior_material=old_raw.exterior_material or "unknown",
            aesthetic_verdict=old_raw.aesthetic_verdict or "",
            has_virtual_staging=old_raw.has_virtual_staging or False,
            has_wide_angle_distortion=old_raw.has_wide_angle_distortion or False,
            epc_rating=old_raw.epc_rating or "Unknown",
            has_garden=property_data.has_garden
        )
        property_data.natural_light_score = old_raw.natural_light_score
        property_data.is_period_property = old_raw.is_period_property
        property_data.has_sash_windows = old_raw.has_sash_windows
        property_data.has_large_windows = old_raw.has_large_windows
        property_data.exterior_material = old_raw.exterior_material
        property_data.aesthetic_verdict = old_raw.aesthetic_verdict
        property_data.has_virtual_staging = old_raw.has_virtual_staging
        property_data.has_wide_angle_distortion = old_raw.has_wide_angle_distortion
        property_data.epc_rating = old_raw.epc_rating

    # 6. Heavy Evaluation (Google Maps & Overpass)
    if location_changed or not old_raw or old_raw.commute_metrics_raw is None:
        commute_metrics = get_commute_times(
            origin_lat=property_data.latitude,
            origin_lng=property_data.longitude,
            destinations=config["locations"]["hubs"]
            + config["locations"]["venues"],
            mode=config["locations"]["transit_mode"],
            api_key=os.environ.get("GOOGLE_MAPS_API_KEY", ""),
        )
        property_data.is_noisy_location = check_noise_pollution(
            property_data.latitude, property_data.longitude
        )
        property_data.commute_metrics_raw = commute_metrics
        property_data.commute_mins = commute_metrics.get("average_mins")
    else:
        logging.info(f"[{property_id}] Skipping Maps API (cache hit)")
        commute_metrics = old_raw.commute_metrics_raw
        property_data.commute_metrics_raw = old_raw.commute_metrics_raw
        property_data.is_noisy_location = old_raw.is_noisy_location
        property_data.commute_mins = old_raw.commute_mins

    final_score, breakdown = calculate_match_score(
        property_data, visual_metrics, commute_metrics, config
    )

    # Prepare Rich Logging Data
    cons = " | ".join(breakdown.get("cons", [])) or "None"
    pros = " | ".join(breakdown.get("pros", [])) or "None"
    scorecard = breakdown.get("scorecard", {})
    score_str = " | ".join(f"{k}: {v}" for k, v in scorecard.items())

    mark_evaluated(
        property_id, 
        ignored=False, 
        score=final_score, 
        breakdown=breakdown, 
        property_data=property_data
    )
    
    if final_score >= config.get("alert_threshold", 70):
        status_msg = "Dispatching Telegram alert"
        send_telegram_alert(telegram_token, telegram_chat_id, property_data, final_score, breakdown)
    else:
        status_msg = "Below alert threshold"

    logging.info(
        f"[{property_id}] Scored {final_score:.1f} - {status_msg}.\n"
        f"  Scorecard: {score_str}\n"
        f"  Pros: [{pros}] | Cons: [{cons}]"
    )

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
    max_unseen_properties = config.get("max_unseen_properties", 50)

    logging.info("Fetching known property IDs from database...")
    known_property_ids = get_all_known_property_ids()
    logging.info(f"Loaded {len(known_property_ids)} known properties.")

    all_urls = []
    for search_url in config["search_urls"]:
        logging.info(f"Processing search batch: {search_url}")
        listing_urls = fetch_search_results(
            search_url, 
            scraper_key, 
            known_property_ids=known_property_ids,
            max_unseen_properties=max_unseen_properties
        )
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
