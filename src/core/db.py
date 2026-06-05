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

def get_config_mtime() -> datetime:
    """
    Reads the modification time of config.yaml inside the container
    and returns a timezone-aware UTC datetime.
    """
    try:
        path = 'config.yaml'
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            return datetime.fromtimestamp(mtime, tz=timezone.utc)
    except Exception as e:
        logging.error(f"Failed to read config.yaml modification time: {e}")
    
    # Fallback to current time if the file read fails (forces a safe re-evaluation)
    return datetime.now(timezone.utc)

def check_and_log_property(property_id: str) -> bool:
    """
    Checks if a property exists in Firestore.
    Returns True if it is brand new OR if the configuration file has been 
    modified since this specific property was last evaluated.
    """
    if not db:
        logging.warning("Firestore client unavailable. Proceeding without state checking.")
        return True # Fail-open

    prop_id = str(property_id)
    doc_ref = collection_ref.document(prop_id)
    config_mtime = get_config_mtime()
    
    try:
        doc = doc_ref.get()
        if doc.exists:
            data = doc.to_dict() or {}
            ts = data.get('timestamp')
            
            if ts:
                # Ensure the database timestamp is timezone-aware for comparison
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                
                date_str = ts.strftime('%Y-%m-%d %H:%M:%S UTC')
                
                # --- CORE LOGIC: Has config changed since last evaluation? ---
                if config_mtime > ts:
                    config_str = config_mtime.strftime('%Y-%m-%d %H:%M:%S UTC')
                    logging.info(
                        f"[{prop_id}] Config update detected ({config_str} > {date_str}). "
                        f"Bypassing cache to re-evaluate property."
                    )
                    # Update the timestamp immediately to reflect this fresh run configuration
                    doc_ref.set({
                        'id': prop_id,
                        'timestamp': firestore.SERVER_TIMESTAMP
                    }, merge=True)
                    return True
                
                logging.info(f"[{prop_id}] Already processed on {date_str} under current config. Skipping.")
                return False
            
        # Brand new property execution path
        doc_ref.set({
            'id': prop_id,
            'timestamp': firestore.SERVER_TIMESTAMP
        })
        logging.info(f"[{prop_id}] Successfully logged new property to Firestore.")
        return True
        
    except GoogleAPIError as e:
        logging.error(f"Database connection error: {e}")
        return True