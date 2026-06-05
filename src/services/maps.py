import requests
import logging

def get_commute_times(origin_lat: float, origin_lng: float, destinations: list[str], mode: str, api_key: str) -> dict:
    """
    Calculates the commute time from the property coordinates to a list of destinations.
    Returns the average commute in minutes, plus a breakdown per destination.
    """
    if not origin_lat or not origin_lng:
        return {'average_mins': 999, 'details': {}}
        
    origins = f"{origin_lat},{origin_lng}"
    # The Distance Matrix API accepts multiple destinations separated by a pipe '|'
    dests = "|".join(destinations)
    url = f"https://maps.googleapis.com/maps/api/distancematrix/json?origins={origins}&destinations={dests}&mode={mode}&key={api_key}"
    
    try:
        response = requests.get(url, timeout=10).json()
        if response.get('status') != 'OK':
            logging.error(f"Maps API Error: {response.get('status')}")
            return {'average_mins': 999, 'details': {}}
            
        times = []
        details = {}
        
        for i, dest in enumerate(destinations):
            element = response['rows'][0]['elements'][i]
            if element.get('status') == 'OK':
                mins = element['duration']['value'] // 60
                times.append(mins)
                details[dest] = mins
            else:
                details[dest] = 999 # Unreachable / No transit route
                
        avg_commute = sum(times) / len(times) if times else 999
        return {'average_mins': avg_commute, 'details': details}
        
    except Exception as e:
        logging.error(f"Google Maps API request failed: {e}")
        return {'average_mins': 999, 'details': {}}

def check_noise_pollution(lat: float, lng: float) -> bool:
    """
    Checks if the coordinates are within 50 meters of a major road (primary/trunk) or railway.
    Returns True if noisy infrastructure is found, False otherwise.
    """
    if not lat or not lng:
        return False
        
    # Query for primary/trunk roads and railways within 50m of the coordinate
    query = f"""
    [out:json][timeout:10];
    (
      way["highway"~"^(primary|trunk)$"](around:50,{lat},{lng});
      way["railway"="rail"](around:50,{lat},{lng});
    );
    out body;
    """
    url = "https://overpass-api.de/api/interpreter"
    
    try:
        headers = {"User-Agent": "PropertyPingerBot/1.0"}
        response = requests.post(url, data={'data': query}, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        return len(data.get("elements", [])) > 0
    except Exception as e:
        logging.error(f"Overpass API request failed: {e}")
        return False
