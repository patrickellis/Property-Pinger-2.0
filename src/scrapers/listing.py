import json
import logging
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from tenacity import retry, wait_exponential, stop_after_attempt

from core.models import PropertyListing

from typing import Optional

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
def extract_rightmove_data_via_api(url: str, api_key: str) -> Optional[PropertyListing]:
    encoded_target_url = quote(url)
    proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={encoded_target_url}&premium=true"

    try:
        response = requests.get(proxy_url, timeout=45)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Scraping API failed to fetch Rightmove {url}: {e}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    script_tags = soup.find_all("script")
    page_model_str = None

    for script in script_tags:
        if script.string:
            if "window.__PAGE_MODEL = " in script.string:
                page_model_str = script.string.split("window.__PAGE_MODEL = ", 1)[
                    1
                ].strip()
                break
            elif "window.PAGE_MODEL = " in script.string:
                page_model_str = script.string.split("window.PAGE_MODEL = ", 1)[
                    1
                ].strip()
                break

    if not page_model_str:
        logging.warning(
            f"PAGE_MODEL not found for {url}. Captcha hit or listing removed."
        )
        return None

    try:
        raw_data, _ = json.JSONDecoder().raw_decode(page_model_str)

        # --- THE DECOMPRESSION ENGINE ---
        if "propertyData" not in raw_data and "data" in raw_data:
            data_payload = raw_data["data"]
            data_array = (
                json.loads(data_payload)
                if isinstance(data_payload, str)
                else data_payload
            )

            def rebuild_node(index):
                if type(index) is not int or index < 0 or index >= len(data_array):
                    return index

                node = data_array[index]
                if isinstance(node, dict):
                    return {k: rebuild_node(v) for k, v in node.items()}
                elif isinstance(node, list):
                    return [rebuild_node(v) for v in node]
                else:
                    return node

            raw_data = rebuild_node(0)

        # --- COMPREHENSIVE DATA EXTRACTION ---
        prop_info = raw_data["propertyData"]

        cleaned_data = {
            "id": str(prop_info.get("id")),
            "url": url,
            "status": prop_info.get("status", {}),
            "price_pcm": str(prop_info.get("prices", {}).get("primaryPrice", "0"))
            .replace("£", "")
            .replace(",", "")
            .replace(" pcm", ""),
            "bedrooms": prop_info.get("bedrooms", 0),
            "bathrooms": prop_info.get("bathrooms")
            or prop_info.get("numberOfBathrooms")
            or 0,
            "property_type": prop_info.get("propertySubType", "Unknown"),
            "display_address": prop_info.get("address", {}).get("displayAddress", ""),
            "postcode": prop_info.get("address", {}).get("outcode", "")
            + " "
            + prop_info.get("address", {}).get("incode", ""),
            "uk_country": prop_info.get("address", {}).get("ukCountry", ""),
            "latitude": prop_info.get("location", {}).get("latitude"),
            "longitude": prop_info.get("location", {}).get("longitude"),
            "nearest_stations": prop_info.get("nearestStations", []),
            "has_garden": bool(prop_info.get("features", {}).get("garden", [])),
            "description": prop_info.get("text", {}).get("description", ""),
            "furnishing": (
                (prop_info.get("lettings") or {}).get("furnishType") or "unknown"
            ).lower(),
            "listing_update": (prop_info.get("listingHistory") or {}).get("listingUpdateReason") or prop_info.get("addedOrReduced", "Date Unknown"),
            "images": [img.get("url", "") for img in (prop_info.get("images") or [])],
            "floorplans": [
                fp.get("url", "") for fp in (prop_info.get("floorplans") or [])
            ],
        }

        # Let Pydantic handle the price parsing and fallback
        return PropertyListing(**cleaned_data)

    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode JSON for {url}: {e}")
        return None
    except KeyError as e:
        logging.error(
            f"Schema mismatch after decompression for {url}. Missing key: {e}"
        )
        return None

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
def extract_zoopla_data_via_api(url: str, api_key: str) -> Optional[PropertyListing]:
    encoded_target_url = quote(url)
    proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={encoded_target_url}&ultra_premium=true"

    try:
        response = requests.get(proxy_url, timeout=60)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Scraping API failed to fetch Zoopla {url}: {e}")
        return None
    soup = BeautifulSoup(response.text, "html.parser")
    
    # 1. Parse Schema.org RealEstateListing JSON-LD
    schema_data = None
    for s in soup.find_all("script", type="application/ld+json"):
        if s.string:
            try:
                data = json.loads(s.string)
                if data.get("@type") == "RealEstateListing":
                    schema_data = data
                    break
            except Exception:
                continue

    if not schema_data:
        logging.warning(f"Schema.org RealEstateListing not found for {url}. Captcha hit or listing removed.")
        return None

    try:
        # Extract ID from URL
        url_match = re.search(r'/details/(\d+)', url)
        listing_id = url_match.group(1) if url_match else str(hash(url))

        # Offers
        offers = schema_data.get("offers", {})
        price = str(offers.get("price", "0"))

        # Beds / Baths from additionalProperty
        bedrooms = 0
        bathrooms = 0
        for prop in schema_data.get("additionalProperty", []):
            if prop.get("name") == "Bedrooms":
                bedrooms = int(prop.get("value", 0))
            elif prop.get("name") == "Bathrooms":
                bathrooms = int(prop.get("value", 0))

        # Lat/Lng from Next.js chunks via regex
        lat, lng = None, None
        coord_match = re.search(r'"coordinates":\{"latitude":([0-9.-]+),"longitude":([0-9.-]+)\}', response.text)
        if coord_match:
            lat = float(coord_match.group(1))
            lng = float(coord_match.group(2))

        # Images via regex from the page (Next.js chunks)
        images = []
        img_matches = re.findall(r'"(https://lid\.zoocdn\.com/[^"]+\.jpg)"', response.text)
        if img_matches:
            # maintain order but remove duplicates
            seen = set()
            for img in img_matches:
                # filter out very small thumbnails if possible, but for now just take all
                if img not in seen:
                    images.append(img)
                    seen.add(img)

        # Ensure we have at least the schema image
        schema_img = schema_data.get("image")
        if schema_img and schema_img not in images:
            images.insert(0, schema_img)

        cleaned_data = {
            "id": listing_id,
            "url": url,
            "status": {},
            "price_pcm": price,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "property_type": "Property", # Zoopla schema doesn't explicitly expose property type easily, fallback to generic
            "display_address": schema_data.get("name", ""),
            "postcode": "",
            "uk_country": "",
            "latitude": lat,
            "longitude": lng,
            "nearest_stations": [],
            "has_garden": "garden" in schema_data.get("description", "").lower(),
            "description": schema_data.get("description", ""),
            "furnishing": "unknown",
            "listing_update": schema_data.get("datePosted", "Date Unknown"),
            "images": images,
            "floorplans": []
        }

        # Validate with Pydantic
        return PropertyListing(**cleaned_data)

    except Exception as e:
        import traceback
        logging.error(f"Error parsing Zoopla property data for {url}: {e}\n{traceback.format_exc()}")
        return None
