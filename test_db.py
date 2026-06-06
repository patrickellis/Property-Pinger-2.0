from google.cloud import firestore

try:
    db = firestore.Client()
    collection_ref = db.collection('properties')
    docs = list(collection_ref.stream())
    
    print(f"Successfully connected to Firestore!")
    print(f"Total properties in collection 'properties': {len(docs)}")
    
    if docs:
        first_doc = docs[0].to_dict()
        print(f"\nSample data from first document (keys):")
        for k in first_doc.keys():
            print(f"- {k}")
            
        print(f"\nIs 'raw_data' present? {'Yes' if 'raw_data' in first_doc else 'No'}")
        if 'raw_data' in first_doc:
            raw = first_doc['raw_data']
            print(f"Does 'raw_data' have latitude? {'latitude' in raw}")
            
except Exception as e:
    print(f"Error: {e}")
