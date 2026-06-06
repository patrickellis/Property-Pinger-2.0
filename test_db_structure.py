from google.cloud import firestore
import json
from datetime import datetime

try:
    db = firestore.Client()
    collection_ref = db.collection('properties')
    # Get the most recently evaluated property
    docs = list(collection_ref.order_by('evaluated_at', direction=firestore.Query.DESCENDING).limit(1).stream())
    
    if docs:
        doc = docs[0].to_dict()
        print(f"ID: {docs[0].id}")
        
        # Safely convert datetimes for printing
        for k, v in doc.items():
            if isinstance(v, datetime):
                doc[k] = v.isoformat()
                
        # Only print keys and some top level values to check
        print(f"Top-level keys: {list(doc.keys())}")
        print(f"Score: {doc.get('score')}")
        print(f"Ignored: {doc.get('ignored')}")
        if 'breakdown' in doc:
            print(f"Breakdown present: Yes")
        
        # Check raw_data
        if 'raw_data' in doc:
            print(f"raw_data keys: {list(doc['raw_data'].keys())}")
    else:
        print("No documents found.")
except Exception as e:
    print(f"Error: {e}")
