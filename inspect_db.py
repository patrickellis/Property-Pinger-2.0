from core.db import db
docs = db.collection("properties").limit(5).stream()

for doc in docs:
    data = doc.to_dict()
    print(f"ID: {doc.id}")
    print(f"commute_mins: {data.get('commute_mins')}")
    if 'raw_data' in data:
        raw = data['raw_data']
        if isinstance(raw, dict):
            print(f"raw_data.commute_mins: {raw.get('commute_mins')}")
            cmr = raw.get('commute_metrics_raw')
            if isinstance(cmr, dict):
                print(f"raw_data.commute_metrics_raw.average_mins: {cmr.get('average_mins')}")
    print("-" * 40)
