import logging

from google.api_core.exceptions import GoogleAPIError
from google.cloud import firestore

# Initialize Firestore Client
try:
    # Automatically detects your GCP project and auth when running in Cloud Run
    db = firestore.Client()
    collection_ref = db.collection("properties")
except Exception as e:
    logging.error(f"Failed to initialize Firestore client: {e}")
    db = None


def check_and_log_property(property_id: str) -> bool:
    """
    Checks if a property exists in the isolated GCP database.
    Returns True if it is new (process it).
    Returns False if we have seen it before (skip it).
    """
    if not db:
        logging.warning(
            "Firestore client unavailable. Proceeding without state checking."
        )
        return True  # Fail-open

    prop_id = str(property_id)
    doc_ref = collection_ref.document(prop_id)
    try:
        doc = doc_ref.get()
        if doc.exists:
            # Firestore returns a standard Python datetime object
            data = doc.to_dict() or {}
            ts = data.get("timestamp")
            date_str = ts.strftime("%Y-%m-%d %H:%M:%S UTC") if ts else "an unknown date"

            logging.info(
                f"[{prop_id}] Already processed on {date_str}. Skipping to avoid duplicate alerts."
            )
            return False

        # If it doesn't exist, we write the record to Firestore to ensure idempotency
        doc_ref.set({"id": prop_id, "timestamp": firestore.SERVER_TIMESTAMP})
        logging.info(f"[{prop_id}] Successfully logged to Firestore.")
        return True

    except GoogleAPIError as e:
        logging.error(f"Database connection error: {e}")
        # Fail-safe: if the DB goes down, we assume we HAVEN'T seen it
        # (better to get a duplicate alert than miss a property)
        return True
