import logging
from io import BytesIO

import requests
from google import genai
from google.genai import errors
from PIL import Image
from pydantic import BaseModel, Field
from core.models import FloorplanGraph
from tenacity import retry, wait_exponential, stop_after_attempt
from typing import Optional

# Initialize the client. GCP automatically injects GEMINI_API_KEY from Secret Manager.
client = genai.Client()


# --- STRUCTURED SCHEMA ---
class PropertyVisuals(BaseModel):
    is_period_property: bool = Field(
        description="True if the property shows Victorian, Georgian, or Edwardian features (e.g., fireplaces, cornicing)."
    )
    has_sash_windows: bool = Field(
        description="True if traditional sliding sash windows are visible."
    )
    has_large_windows: bool = Field(
        description="True if the property features prominently large windows like bay windows or floor-to-ceiling glass."
    )
    natural_light_score: int = Field(
        description="Score from 1 to 10 estimating the abundance of natural light and window sizes.",
        ge=1,
        le=10,
    )
    exterior_material: str = Field(
        description="Primary exterior material, e.g., 'brick', 'stucco', 'pebble dash', 'cladding', or 'unknown'."
    )
    has_garden: bool = Field(description="True if a private garden is visible.")
    aesthetic_verdict: str = Field(
        description="A brief 1-sentence summary of the property's style and condition."
    )
    has_virtual_staging: bool = Field(
        description="True if any images appear to use virtual staging (computer-generated furniture)."
    )
    has_wide_angle_distortion: bool = Field(
        description="True if images exhibit severe wide-angle lens distortion to make rooms look larger."
    )
    epc_rating: str = Field(
        description="The Energy Performance Certificate (EPC) rating letter (A-G), if an EPC graph is found. Return 'Unknown' if not found."
    )


class FloorplanDetails(BaseModel):
    total_sqft: int = Field(
        description="Total Gross Internal Area in square feet. 0 if not found."
    )
    reception_length_m: float = Field(
        description="The longest dimension of the largest reception room/living room in meters. Convert from feet/inches if necessary. 0.0 if not found."
    )
    reception_width_m: float = Field(
        description="The shortest dimension of the largest reception room/living room in meters. Convert from feet/inches if necessary. 0.0 if not found."
    )
    reception_on_ground_floor: bool = Field(
        description="True if the main reception room is on the ground floor."
    )
    max_ceiling_height_m: float = Field(
        description="The maximum ceiling height indicated anywhere on the plan in meters. 0.0 if not found."
    )
    floor_level: int = Field(
        description="The floor the main entrance of the flat is on. Ground floor = 0, First = 1, etc. If it's a house or unknown, return 0."
    )
    has_lift: bool = Field(
        description="True if the description mentions a lift or elevator."
    )
    master_bedroom_length_m: float = Field(
        description="The longest dimension of the largest bedroom in meters. 0.0 if not found."
    )
    master_bedroom_width_m: float = Field(
        description="The shortest dimension of the largest bedroom in meters. 0.0 if not found."
    )
    floorplan_graph: Optional[FloorplanGraph] = Field(
        default=None,
        description="A full graph representation of the floorplan layout, detailing rooms and doors."
    )


# --- FUNCTION 1: AESTHETIC EVALUATION ---
@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
def evaluate_property_images(
    image_urls: list[str], description: str = ""
) -> PropertyVisuals:
    images = []
    # Limit to the first 10 images to save API costs and processing time
    for url in image_urls[:10]:
        try:
            response = requests.get(url, timeout=5)
            images.append(Image.open(BytesIO(response.content)))
        except Exception as e:
            logging.warning(f"Skipping image {url}: {e}")

    if not images:
        logging.error("No valid images found to evaluate.")
        # Return a neutral, empty baseline if images fail to load
        return PropertyVisuals(
            is_period_property=False,
            has_sash_windows=False,
            has_large_windows=False,
            natural_light_score=5,
            exterior_material="unknown",
            has_garden=False,
            aesthetic_verdict="Could not evaluate images.",
            has_virtual_staging=False,
            has_wide_angle_distortion=False,
            epc_rating="Unknown",
        )

    prompt = f"""
    You are an expert real estate evaluator. Review these property photos.
    The user is looking for a period property with plenty of natural light and large windows.
    Agent Description for context: {description}
    Extract the visual characteristics exactly according to the provided schema.
    Also, watch out for misleading photos: flag any virtual staging or severe wide-angle distortion.
    If you see an Energy Performance Certificate (EPC) graph among the images, extract the current rating letter (A-G).
    """

    config = {
        "response_mime_type": "application/json",
        "response_schema": PropertyVisuals,
        "temperature": 0.1,
    }

    # Primary call to 3.1 Flash Lite, fallback to 2.5 Flash
    try:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite", contents=[prompt] + images, config=config
        )
    except errors.APIError as e:
        logging.warning(f"3.1 Flash Lite overloaded ({e}). Falling back to 2.5 Flash.")
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=[prompt] + images, config=config
        )
    except Exception as e:
        logging.warning(f"Unexpected vision error: {e}. Falling back to 2.5.")
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=[prompt] + images, config=config
        )

    return PropertyVisuals.model_validate_json(response.text)


# --- FUNCTION 2: FLOORPLAN SIZING ---
@retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(5))
def extract_floorplan_details(floorplan_urls: list[str], description: str) -> FloorplanDetails:
    empty_baseline = FloorplanDetails(
        total_sqft=0,
        reception_length_m=0.0,
        reception_width_m=0.0,
        reception_on_ground_floor=False,
        max_ceiling_height_m=0.0,
        floor_level=0,
        has_lift=False,
        master_bedroom_length_m=0.0,
        master_bedroom_width_m=0.0,
        floorplan_graph=None,
    )

    if not floorplan_urls:
        return empty_baseline

    try:
        response = requests.get(floorplan_urls[0], timeout=10)
        img = Image.open(BytesIO(response.content))
    except Exception as e:
        logging.error(f"Failed to load floorplan image: {e}")
        return empty_baseline

    prompt = f"""
    Examine this floorplan and the property description below. 
    Extract the details according to the provided schema.
    If you find dimensions in feet/inches, please convert them to meters.

    CRITICAL: You must also extract the full floorplan graph (rooms and doors).
    1. Identify every distinct room, hallway, or space. Estimate its length and width in meters. If the dimensions are not explicitly stated but you can estimate them visually compared to other rooms, do so.
    2. Identify all doors, openings, or connections between these spaces. Create DoorEdge items connecting the 'from_room' to the 'to_room'. Make sure the names match exactly.
    3. Identify the main entrance room/hallway from the outside.
    4. For each room, identify the orientation of any external windows based on the compass rose (if present). If there is no compass rose, assume North is at the top of the image. Add a Window object for each window found.

    Agent Description for context: {description}
    """

    config = {
        "response_mime_type": "application/json",
        "response_schema": FloorplanDetails,
        "temperature": 0.0,
    }

    try:
        result = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, img],
            config=config,
        )
        return FloorplanDetails.model_validate_json(result.text)
    except Exception as e:
        logging.error(f"Gemini failed to extract floorplan details: {e}")
        return empty_baseline
