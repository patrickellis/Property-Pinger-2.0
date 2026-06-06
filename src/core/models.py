from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any

class PropertyListing(BaseModel):
    id: str
    url: str
    status: Dict[str, Any] = Field(default_factory=dict)
    price_pcm: int
    bedrooms: int
    bathrooms: int
    property_type: str
    display_address: str
    postcode: str
    uk_country: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    nearest_stations: List[Dict[str, Any]] = Field(default_factory=list)
    has_garden: bool
    description: str
    furnishing: str
    listing_update: str
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
