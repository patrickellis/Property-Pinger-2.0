import yaml
import json
import time
import requests

def geocode(address):
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        'q': address,
        'format': 'json',
        'limit': 1
    }
    headers = {
        'User-Agent': 'PropertyPingerBot/1.0'
    }
    try:
        response = requests.get(url, params=params, headers=headers)
        data = response.json()
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception as e:
        print(f"Error geocoding {address}: {e}")
    return None, None

def main():
    with open('config/london.yaml', 'r') as f:
        config = yaml.safe_load(f)
        
    pois = []
    
    hubs = config.get('locations', {}).get('hubs', [])
    venues = config.get('locations', {}).get('venues', [])
    
    for address in hubs:
        lat, lng = geocode(address)
        if lat and lng:
            pois.append({
                'name': address,
                'type': 'hub',
                'lat': lat,
                'lng': lng
            })
        time.sleep(1) # Nominatim rate limit
        
    for address in venues:
        lat, lng = geocode(address)
        if lat and lng:
            pois.append({
                'name': address,
                'type': 'venue',
                'lat': lat,
                'lng': lng
            })
        time.sleep(1)

    # Some might fail, let's provide fallbacks for known ones if needed
    fallbacks = {
        "Climbing Gym Sen, London": (51.545, -0.015), # Approximate for Yonder / similar
    }
    for p in pois:
        if p['lat'] is None and p['name'] in fallbacks:
            p['lat'], p['lng'] = fallbacks[p['name']]

    with open('dashboard/src/pois.json', 'w') as f:
        json.dump(pois, f, indent=2)
        
    print(f"Geocoded {len(pois)} POIs")

if __name__ == '__main__':
    main()
