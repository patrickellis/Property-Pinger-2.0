import requests
import logging
from tenacity import retry, wait_exponential, stop_after_attempt

@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
def get_commute_times(origin_lat: float, origin_lng: float, destinations: list[str], mode: str, api_key: str) -> dict:
    """
    Calculates the commute time from the property coordinates to a list of destinations.
    Returns the average commute in minutes, plus a breakdown per destination.
    """
    if not origin_lat or not origin_lng:
        return {'average_mins': 999, 'details': {}}
        
    from core.db import get_cached_commute, cache_commute
    
    # Check cache first
    cached = get_cached_commute(origin_lat, origin_lng, destinations, mode)
    if cached:
        logging.info(f"[{origin_lat},{origin_lng}] Commute grid cache hit!")
        return cached

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
        result = {'average_mins': avg_commute, 'details': details}
        
        # Save to cache
        cache_commute(origin_lat, origin_lng, destinations, mode, result)
        
        return result
        
    except Exception as e:
        logging.error(f"Google Maps API request failed: {e}")
        return {'average_mins': 999, 'details': {}}

