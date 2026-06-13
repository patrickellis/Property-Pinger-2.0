from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
import math

class Window(BaseModel):
    orientation: str = Field(description="The compass direction the window faces: 'N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'. Infer from the compass rose if present. If no compass rose exists, assume the top of the plan is 'N'.")

class RoomNode(BaseModel):
    name: str = Field(description="Name or label of the room, e.g. 'Living Room', 'Kitchen', 'Hallway'.")
    length_m: float = Field(description="Longest dimension of the room in meters. 0.0 if unknown.")
    width_m: float = Field(description="Shortest dimension of the room in meters. 0.0 if unknown.")
    room_type: str = Field(description="Type of the room: 'reception', 'bedroom', 'kitchen', 'bathroom', 'hallway', 'entrance', 'other'.")
    windows: List[Window] = Field(default_factory=list, description="Windows present in the room.")
    sunlight_hours: float = Field(default=0.0, description="Average daily hours of direct sunlight.")

class DoorEdge(BaseModel):
    from_room: str = Field(description="Name of the room this door/opening connects from. Must match a room name.")
    to_room: str = Field(description="Name of the room this door/opening connects to. Must match a room name.")
    width_m: float = Field(description="Width of the door/opening in meters. Use standard 0.76m if unstated but visible.")

class FloorplanGraph(BaseModel):
    rooms: List[RoomNode] = Field(default_factory=list, description="All distinct rooms and hallways found in the floorplan.")
    doors: List[DoorEdge] = Field(default_factory=list, description="All connections (doors/archways) between rooms.")
    entrance_room: Optional[str] = Field(None, description="The name of the room/hallway that acts as the main entrance to the property from the outside.")

class PriceHistoryEntry(BaseModel):
    date: str = Field(description="Date the price was changed, e.g., YYYY-MM-DD")
    price_pcm: int = Field(description="The old price before the change")

class PropertyListing(BaseModel):
    id: str
    url: str
    status: Dict[str, Any] = Field(default_factory=dict)
    price_pcm: int
    price_history: List[PriceHistoryEntry] = Field(default_factory=list)
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
    user_note: Optional[str] = None

    # Fields populated during evaluation
    sqft: Optional[int] = None
    reception_length_m: Optional[float] = None
    reception_width_m: Optional[float] = None
    reception_on_ground_floor: Optional[bool] = None
    max_ceiling_height_m: Optional[float] = None
    floor_level: Optional[int] = None
    has_lift: Optional[bool] = None
    master_bedroom_length_m: Optional[float] = None
    master_bedroom_width_m: Optional[float] = None

    commute_mins: Optional[int] = None
    has_ac: Optional[bool] = None
    has_underfloor_heating: Optional[bool] = None
    
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
    
    # Graph based floorplan
    floorplan_graph: Optional[Dict[str, Any]] = None

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
