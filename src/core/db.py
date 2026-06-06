import os
import logging
from datetime import datetime, timezone
from google.cloud import firestore
from google.api_core.exceptions import GoogleAPIError

# Initialize Firestore Client
try:
    db = firestore.Client()
    collection_ref = db.collection('properties')
except Exception as e:
    logging.error(f"Failed to initialize Firestore client: {e}")
    db = None
    collection_ref = None

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
            return data
    except GoogleAPIError as e:
        logging.error(f"Database connection error: {e}")
    return {}

def save_scraped_data(property_id: str, raw_data: dict):
    if not db:
        return
    try:
        collection_ref.document(str(property_id)).set({
            'id': str(property_id),
            'raw_data': raw_data,
            'scraped_at': firestore.SERVER_TIMESTAMP
        }, merge=True)
        logging.info(f"[{property_id}] Saved raw scraped data to Firestore.")
    except GoogleAPIError as e:
        logging.error(f"Database save error: {e}")

def mark_evaluated(property_id: str, ignored: bool = False):
    if not db:
        return
    try:
        collection_ref.document(str(property_id)).set({
            'evaluated_at': firestore.SERVER_TIMESTAMP,
            'ignored': ignored
        }, merge=True)
        if ignored:
            logging.info(f"[{property_id}] Marked as ignored due to low score or dealbreakers.")
    except GoogleAPIError as e:
        logging.error(f"Database evaluation mark error: {e}")