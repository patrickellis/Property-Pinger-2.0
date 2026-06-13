import json
import logging
import re
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
            "epc_rating": re.search(r'EPC\s*Rating[^A-G]*([A-G])\b', response.text, re.IGNORECASE).group(1).upper() if re.search(r'EPC\s*Rating[^A-G]*([A-G])\b', response.text, re.IGNORECASE) else None,
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


def _extract_zoopla_app_router_fallback(soup: BeautifulSoup, html: str, url: str) -> Optional[PropertyListing]:
    try:
        # Extract ID from URL
        url_match = re.search(r'/details/(\d+)', url)
        listing_id = url_match.group(1) if url_match else str(hash(url))

        # Price
        price_pcm = "0"
        price_elem = soup.find(string=re.compile(r"£[0-9,]+ pcm"))
        if price_elem:
            p_match = re.search(r"£([0-9,]+) pcm", price_elem)
            if p_match:
                price_pcm = p_match.group(1).replace(",", "")

        # Beds and Baths
        bedrooms = 0
        beds_elem = soup.find(string=re.compile(r"([0-9]+) beds?"))
        if beds_elem:
            b_match = re.search(r"([0-9]+) beds?", beds_elem)
            if b_match:
                bedrooms = int(b_match.group(1))
                
        bathrooms = 0
        baths_elem = soup.find(string=re.compile(r"([0-9]+) baths?"))
        if baths_elem:
            b_match = re.search(r"([0-9]+) baths?", baths_elem)
            if b_match:
                bathrooms = int(b_match.group(1))

        # Coordinates
        lat, lng = None, None
        coord_match = re.search(r'"latitude"[^0-9.-]+([0-9.-]+).*?"longitude"[^0-9.-]+([0-9.-]+)', html, re.IGNORECASE | re.DOTALL)
        if coord_match:
            lat = float(coord_match.group(1))
            lng = float(coord_match.group(2))
        else:
            coord_match2 = re.search(r'latitude[^0-9.-]+([0-9.-]+).*?longitude[^0-9.-]+([0-9.-]+)', html, re.IGNORECASE | re.DOTALL)
            if coord_match2:
                lat = float(coord_match2.group(1))
                lng = float(coord_match2.group(2))

        # Title / Display Address
        display_address = soup.title.string.split(',')[0].strip() if soup.title else ""
        address_elem = soup.find("address")
        if address_elem:
            display_address = address_elem.text.strip()
            
        postcode = ""
        
        # Images
        images = []
        img_matches = list(set(re.findall(r"(https://lid\.zoocdn\.com/u/[^/]+/[^/]+/[^\"]+\.jpg)", html)))
        if not img_matches:
            img_matches = list(set(re.findall(r"(https://lid\.zoocdn\.com/[^\"]+\.jpg)", html)))
            
        if img_matches:
            # Prefer 1024/768 if possible, but keep whatever regex found
            images = [img.replace("480/360", "1024/768") for img in img_matches]

        # Description
        detailed_desc = ""
        desc_elem = soup.find(id="detailed-desc")
        if desc_elem:
            detailed_desc = desc_elem.text.strip()

        property_type = "Room" if "room" in (soup.title.string.lower() if soup.title else "") else "Unknown"

        cleaned_data = {
            "id": listing_id,
            "url": url,
            "status": {},
            "price_pcm": price_pcm,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "property_type": property_type,
            "display_address": display_address,
            "postcode": postcode,
            "uk_country": "GB",
            "latitude": lat,
            "longitude": lng,
            "nearest_stations": [],
            "has_garden": "garden" in detailed_desc.lower(),
            "description": detailed_desc,
            "furnishing": "furnished" if "furnished" in detailed_desc.lower() else "unknown",
            "epc_rating": re.search(r'EPC\s*Rating[^A-G]*([A-G])\b', BeautifulSoup(html, "html.parser").get_text(separator=' '), re.IGNORECASE).group(1).upper() if re.search(r'EPC\s*Rating[^A-G]*([A-G])\b', BeautifulSoup(html, "html.parser").get_text(separator=' '), re.IGNORECASE) else None,
            "listing_update": "Date Unknown",
            "images": images,
            "floorplans": []
        }

        # Validate with Pydantic
        # If title has "Captcha" in it, it might actually be a captcha
        if soup.title and "captcha" in soup.title.string.lower():
            logging.error(f"Zoopla hit a Captcha for {url}")
            return None

        return PropertyListing(**cleaned_data)
        
    except Exception as e:
        import traceback
        logging.error(f"Error parsing Zoopla App Router fallback for {url}: {e}\n{traceback.format_exc()}")
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
    
    # 1. Extract __NEXT_DATA__ script
    next_data_script = soup.find("script", id="__NEXT_DATA__")
    if not next_data_script or not next_data_script.string:
        logging.info(f"__NEXT_DATA__ not found for {url}. Attempting Next.js App Router fallback parsing...")
        return _extract_zoopla_app_router_fallback(soup, response.text, url)

    try:
        raw_data = json.loads(next_data_script.string)
        
        page_props = raw_data.get("props", {}).get("pageProps", {})
        listing_details = page_props.get("listingDetails", {}) or page_props.get("regularListing", {}) or {}
        
        if not listing_details:
            logging.error(f"Could not find listingDetails in __NEXT_DATA__ for {url}")
            return None

        # Extract ID from URL
        url_match = re.search(r'/details/(\d+)', url)
        listing_id = str(listing_details.get("listingId") or (url_match.group(1) if url_match else str(hash(url))))

        pricing = listing_details.get("pricing", {})
        price_pcm = str(pricing.get("price") or listing_details.get("analyticsTaxonomy", {}).get("priceActual") or "0")

        counts = listing_details.get("counts", {})
        bedrooms = counts.get("numBedrooms", 0)
        bathrooms = counts.get("numBathrooms", 0)
        
        taxonomy = listing_details.get("analyticsTaxonomy", {})
        property_type = taxonomy.get("propertyType", "Unknown")
        postcode = f"{taxonomy.get('outcode', '')} {taxonomy.get('incode', '')}".strip()

        # Location could be in analyticsTaxonomy or a location object
        lat, lng = None, None
        coord_match = re.search(r'"coordinates":\{"latitude":([0-9.-]+),"longitude":([0-9.-]+)\}', response.text)
        if coord_match:
            lat = float(coord_match.group(1))
            lng = float(coord_match.group(2))

        # Build Image URLs
        images = []
        for img in listing_details.get("propertyImage", []):
            filename = img.get("filename")
            if filename:
                # Zoopla uses lid.zoocdn.com for high-res images
                images.append(f"https://lid.zoocdn.com/1024/768/{filename}")
                
        # If array is empty, fallback to regex
        if not images:
            img_matches = re.findall(r'"(https://lid\.zoocdn\.com/[^"]+\.jpg)"', response.text)
            if img_matches:
                seen = set()
                for img in img_matches:
                    if img not in seen:
                        images.append(img)
                        seen.add(img)

        # Build Floorplan URLs
        floorplans = []
        for fp in listing_details.get("floorPlan", []):
            filename = fp.get("filename")
            if filename:
                floorplans.append(f"https://lid.zoocdn.com/1024/768/{filename}")

        # Description and Features
        detailed_desc = listing_details.get("detailedDescription", "")
        features = " ".join(listing_details.get("bullets", []))
        full_description = f"{detailed_desc}\n{features}"
        
        # Garden and Furnishing heuristics
        has_garden = bool(re.search(r'\bgarden\b', full_description, re.IGNORECASE))
        furnishing = taxonomy.get("furnishedState", "unknown").lower()
        if furnishing == "unfurnished":
            furnishing = "unfurnished"
        elif "furnished" in furnishing:
            furnishing = "furnished"
        else:
            furnishing = "unknown"

        cleaned_data = {
            "id": listing_id,
            "url": url,
            "status": listing_details.get("statusSummary", {}),
            "price_pcm": price_pcm,
            "bedrooms": bedrooms,
            "bathrooms": bathrooms,
            "property_type": property_type,
            "display_address": listing_details.get("displayAddress", ""),
            "postcode": postcode,
            "uk_country": taxonomy.get("countryCode", "gb").upper(),
            "latitude": lat,
            "longitude": lng,
            "nearest_stations": [],
            "has_garden": has_garden,
            "description": full_description,
            "furnishing": furnishing,
            "epc_rating": re.search(r'EPC\s*Rating[^A-G]*([A-G])\b', BeautifulSoup(response.text, "html.parser").get_text(separator=' '), re.IGNORECASE).group(1).upper() if re.search(r'EPC\s*Rating[^A-G]*([A-G])\b', BeautifulSoup(response.text, "html.parser").get_text(separator=' '), re.IGNORECASE) else None,
            "listing_update": listing_details.get("statusSummary", {}).get("label") or "Date Unknown",
            "images": images,
            "floorplans": floorplans
        }

        # Validate with Pydantic
        return PropertyListing(**cleaned_data)

    except json.JSONDecodeError as e:
        logging.error(f"Failed to decode __NEXT_DATA__ JSON for Zoopla {url}: {e}")
        return None
    except Exception as e:
        import traceback
        logging.error(f"Error parsing Zoopla property data for {url}: {e}\n{traceback.format_exc()}")
        return None
