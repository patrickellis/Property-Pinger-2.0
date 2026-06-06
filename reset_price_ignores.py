from google.cloud import firestore
import yaml

db = firestore.Client(database="property-pinger-db")

with open('config/defaults.yaml', 'r') as f:
    config = yaml.safe_load(f)

min_beds = config['dealbreakers']['min_bedrooms']
req_furn = config['dealbreakers']['required_furnishing']

docs = db.collection('properties').where('ignored', '==', True).stream()

count = 0
for doc in docs:
    data = doc.to_dict()
    if data.get('score') is not None:
        continue
    
    beds = data.get('bedrooms', 0)
    furn = data.get('raw_data', {}).get('furnishing', 'unknown')
    
    if beds >= min_beds and furn in req_furn:
        print(f"Resetting property {doc.id} (Price: {data.get('price_pcm')})")
        db.collection('properties').document(doc.id).update({
            'ignored': firestore.DELETE_FIELD,
            'evaluated_at': firestore.DELETE_FIELD
        })
        count += 1

print(f"Successfully reset {count} properties.")
