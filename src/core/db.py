import os
import logging
from datetime import datetime, timezone
from google.cloud import firestore
from google.api_core.exceptions import GoogleAPIError
from typing import Optional
from core.models import PropertyListing

# Initialize Firestore Client
try:
    db = firestore.Client()
    collection_ref = db.collection('properties')
except Exception as e:
    logging.error(f"Failed to initialize Firestore client: {e}")
    db = None
    collection_ref = None

def get_all_known_property_ids() -> set[str]:
    """
    Fetches all known property IDs from Firestore using a keys-only query.
    Returns a set of string IDs.
    """
    if not db:
        return set()
    try:
        # A keys-only query is much faster and cheaper than fetching documents
        return {doc.id for doc in collection_ref.select([]).stream()}
    except GoogleAPIError as e:
        logging.error(f"Failed to fetch known property IDs: {e}")
        return set()


def get_config_mtime() -> datetime:
    """
    Reads the modification time of config files and returns the newest timezone-aware UTC datetime.
    """
    try:
        paths = ["config/defaults.yaml", os.environ.get("CONFIG_PATH", "config/london.yaml")]
        max_mtime = 0
        for path in paths:
            if os.path.exists(path):
                max_mtime = max(max_mtime, os.path.getmtime(path))
        if max_mtime > 0:
            return datetime.fromtimestamp(max_mtime, tz=timezone.utc)
    except Exception as e:
        logging.error(f"Failed to read config modification time: {e}")
    
    return datetime.now(timezone.utc)

def get_property_cache(property_id: str) -> dict:
    if not db:
        return {}
    try:
        doc = collection_ref.document(str(property_id)).get()
        if doc.exists:
            data = doc.to_dict() or {}
            # Convert timestamps to timezone aware
            for field in ['scraped_at', 'evaluated_at']:
                if data.get(field) and data[field].tzinfo is None:
                    data[field] = data[field].replace(tzinfo=timezone.utc)
            
            # Rehydrate Pydantic model if raw_data exists
            if 'raw_data' in data and data['raw_data']:
                try:
                    data['raw_data'] = PropertyListing(**data['raw_data'])
                except Exception as e:
                    logging.error(f"Failed to parse PropertyListing from cache: {e}")
            return data
    except GoogleAPIError as e:
        logging.error(f"Database connection error: {e}")
    return {}

def save_scraped_data(property_id: str, raw_data: PropertyListing):
    if not db:
        return
    try:
        collection_ref.document(str(property_id)).set({
            'id': str(property_id),
            'raw_data': raw_data.model_dump(),
            'scraped_at': firestore.SERVER_TIMESTAMP
        }, merge=True)
        logging.info(f"[{property_id}] Saved raw scraped data to Firestore.")
    except GoogleAPIError as e:
        logging.error(f"Database save error: {e}")

def mark_evaluated(property_id: str, ignored: bool = False, score: float = 0.0, breakdown: dict = None, property_data: PropertyListing = None):
    if not db:
        return
    try:
        update_data = {
            'evaluated_at': firestore.SERVER_TIMESTAMP,
            'ignored': ignored,
            'score': score,
            'breakdown': breakdown or {}
        }
        if property_data:
            update_data.update({
                'price_pcm': property_data.price_pcm,
                'bedrooms': property_data.bedrooms,
                'latitude': property_data.latitude,
                'longitude': property_data.longitude,
                'property_type': property_data.property_type,
                'commute_mins': property_data.commute_mins,
                'raw_data': property_data.model_dump()
            })
            if property_data.user_note:
                update_data['user_note'] = property_data.user_note
            
            
        collection_ref.document(str(property_id)).set(update_data, merge=True)
        if ignored:
            logging.info(f"[{property_id}] Marked as ignored due to low score or dealbreakers.")
    except GoogleAPIError as e:
        logging.error(f"Database evaluation mark error: {e}")

def find_duplicate_property(property_data: PropertyListing) -> tuple[Optional[str], Optional[dict]]:
    """
    Checks if a property with the same physical characteristics already exists.
    Returns a tuple of (duplicate_id, duplicate_data) if found, otherwise (None, None).
    """
    if not db:
        return None, None
    
    if property_data.latitude is None or property_data.longitude is None:
        return None, None
        
    try:
        # Query by price to narrow down. We don't use multiple where() clauses to avoid composite index requirements.
        query = collection_ref.where('price_pcm', '==', property_data.price_pcm).stream()
        
        for doc in query:
            if doc.id == str(property_data.id):
                continue
                
            data = doc.to_dict()
            if not data:
                continue
                
            # Check bedrooms match
            if str(data.get('bedrooms', '')) != str(property_data.bedrooms):
                continue
                
            # Check coordinates (fuzzing up to ~100m, ~0.001 degrees)
            doc_lat = data.get('latitude')
            doc_lng = data.get('longitude')
            
            if doc_lat is not None and doc_lng is not None:
                if abs(doc_lat - property_data.latitude) < 0.002 and abs(doc_lng - property_data.longitude) < 0.002:
                    return doc.id, data
                    
        return None, None
    except GoogleAPIError as e:
        logging.error(f"Database duplicate check error: {e}")
        return None

def get_cached_commute(origin_lat: float, origin_lng: float, destinations: list[str], mode: str) -> Optional[dict]:
    if not db:
        return None
    try:
        import hashlib
        dest_str = "|".join(destinations)
        dest_hash = hashlib.md5(dest_str.encode()).hexdigest()
        cache_id = f"{round(origin_lat, 3)}_{round(origin_lng, 3)}_{mode}_{dest_hash}"
        
        doc = db.collection('commute_cache').document(cache_id).get()
        if doc.exists:
            return doc.to_dict().get('commute_metrics')
    except Exception as e:
        logging.error(f"Failed to read commute cache: {e}")
    return None

def cache_commute(origin_lat: float, origin_lng: float, destinations: list[str], mode: str, commute_metrics: dict):
    if not db:
        return
    try:
        import hashlib
        dest_str = "|".join(destinations)
        dest_hash = hashlib.md5(dest_str.encode()).hexdigest()
        cache_id = f"{round(origin_lat, 3)}_{round(origin_lng, 3)}_{mode}_{dest_hash}"
        
        db.collection('commute_cache').document(cache_id).set({
            'commute_metrics': commute_metrics,
            'created_at': firestore.SERVER_TIMESTAMP
        }, merge=True)
    except Exception as e:
        logging.error(f"Failed to write commute cache: {e}")