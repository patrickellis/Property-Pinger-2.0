import math
import datetime

def calculate_solar_position(date: datetime.datetime, lat: float, lon: float) -> tuple[float, float]:
    """
    Approximates solar position for a given datetime and location.
    Returns (altitude_degrees, azimuth_degrees).
    """
    n = date.timetuple().tm_yday
    
    # Declination of the sun (in degrees)
    declination = 23.45 * math.sin(math.radians(360.0 / 365.0 * (n - 81)))
    
    # Solar time (hours)
    # Using local time approximation
    h = date.hour + date.minute / 60.0
    
    # Hour angle (in degrees)
    hour_angle = 15.0 * (h - 12.0)
    
    lat_rad = math.radians(lat)
    dec_rad = math.radians(declination)
    ha_rad = math.radians(hour_angle)
    
    # Solar Altitude
    sin_alt = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(ha_rad)
    # Clamp for floating point errors
    sin_alt = max(-1.0, min(1.0, sin_alt))
    alt_rad = math.asin(sin_alt)
    altitude = math.degrees(alt_rad)
    
    # Solar Azimuth
    cos_az = (math.sin(dec_rad) - math.sin(alt_rad) * math.sin(lat_rad)) / (math.cos(alt_rad) * math.cos(lat_rad))
    cos_az = max(-1.0, min(1.0, cos_az))
    az_rad = math.acos(cos_az)
    azimuth = math.degrees(az_rad)
    
    if hour_angle > 0:
        azimuth = 360.0 - azimuth
        
    return altitude, azimuth

def get_window_azimuth(orientation: str) -> float:
    mapping = {
        'N': 0.0,
        'NE': 45.0,
        'E': 90.0,
        'SE': 135.0,
        'S': 180.0,
        'SW': 225.0,
        'W': 270.0,
        'NW': 315.0,
    }
    return mapping.get(orientation.upper(), 0.0)

def calculate_average_sunlight_hours(lat: float, lon: float, windows: list) -> float:
    """
    Calculates the average daily hours of direct sunlight a room receives 
    over the course of a year based on its windows.
    """
    if not windows:
        return 0.0
        
    # Sample 12 days across the year
    days_to_sample = [datetime.datetime(2024, m, 21) for m in range(1, 13)]
    total_hours = 0
    
    for day in days_to_sample:
        daily_hours = 0
        # Sample every hour from 4 AM to 10 PM
        for hour in range(4, 22):
            dt = datetime.datetime(day.year, day.month, day.day, hour, 30)
            alt, az = calculate_solar_position(dt, lat, lon)
            
            if alt > 0: # Sun is up
                # Check if any window gets direct light
                for window in windows:
                    win_az = get_window_azimuth(window.orientation)
                    
                    diff = abs(win_az - az)
                    if diff > 180:
                        diff = 360 - diff
                        
                    if diff < 90:
                        daily_hours += 1
                        break
                        
        total_hours += daily_hours
        
    return round(total_hours / len(days_to_sample), 1)
