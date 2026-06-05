import json
import logging
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup


def extract_rightmove_data_via_api(url: str, api_key: str) -> dict:
    encoded_target_url = quote(url)
    proxy_url = f"http://api.scraperapi.com?api_key={api_key}&url={encoded_target_url}&premium=true"

    try:
        response = requests.get(proxy_url, timeout=45)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Scraping API failed to fetch {url}: {e}")
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
            "images": [img.get("url", "") for img in (prop_info.get("images") or [])],
            "floorplans": [
                fp.get("url", "") for fp in (prop_info.get("floorplans") or [])
            ],
        }

        if cleaned_data["price_pcm"].isdigit():
            cleaned_data["price_pcm"] = int(cleaned_data["price_pcm"])

        return cleaned_data

    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode JSON for {url}: {e}")
        return None
    except KeyError as e:
        logging.error(
            f"Schema mismatch after decompression for {url}. Missing key: {e}"
        )
        return None
