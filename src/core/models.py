from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
import math

class PropertyListing(BaseModel):
    id: str
    url: str
    status: Dict[str, Any] = Field(default_factory=dict)
    price_pcm: int
    bedrooms: int = 0
    bathrooms: int = 0
    property_type: Optional[str] = None
    display_address: Optional[str] = None
    postcode: Optional[str] = None
    uk_country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nearest_stations: List[Dict[str, Any]] = Field(default_factory=list)
    has_garden: Optional[bool] = None
    description: Optional[str] = None
    furnishing: Optional[str] = None
    listing_update: Optional[str] = None
    images: List[str] = Field(default_factory=list)
    floorplans: List[str] = Field(default_factory=list)

    # Fields populated during evaluation
    sqft: Optional[int] = None
    reception_length_m: Optional[float] = None
    reception_on_ground_floor: Optional[bool] = None
    max_ceiling_height_m: Optional[float] = None
    floor_level: Optional[int] = None
    has_lift: Optional[bool] = None
    master_bedroom_length_m: Optional[float] = None
    is_noisy_location: Optional[bool] = None
    commute_mins: Optional[int] = None
    
    # Gemini Vision Fields
    natural_light_score: Optional[int] = None
    is_period_property: Optional[bool] = None
    has_sash_windows: Optional[bool] = None
    has_large_windows: Optional[bool] = None
    exterior_material: Optional[str] = None
    aesthetic_verdict: Optional[str] = None
    has_virtual_staging: Optional[bool] = None
    has_wide_angle_distortion: Optional[bool] = None
    epc_rating: Optional[str] = None

    # Google Maps Commute Fields
    commute_metrics_raw: Optional[Dict[str, Any]] = None
    
    # Cache Invalidators
    image_count: Optional[int] = None
    floorplan_count: Optional[int] = None

    @field_validator("price_pcm", mode="before")
    @classmethod
    def parse_price(cls, value):
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            clean_val = value.replace("£", "").replace(",", "").replace(" pcm", "").strip()
            if clean_val.isdigit():
                return int(clean_val)
        # Fallback for "POA" or unknown prices to avoid crashing but ensure it gets filtered out
        return 999999

    @field_validator("commute_mins", mode="before")
    @classmethod
    def parse_commute_mins(cls, value):
        if value is None:
            return value
        if isinstance(value, (int, float)):
            return math.ceil(value)
        if isinstance(value, str):
            try:
                return math.ceil(float(value))
            except ValueError:
                return None
        return value

    @field_validator("bedrooms", "bathrooms", mode="before")
    @classmethod
    def parse_rooms(cls, value):
        if value is None:
            return 0
        return value
