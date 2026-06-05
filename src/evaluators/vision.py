import logging
from io import BytesIO

import requests
from google import genai
from google.genai import errors
from PIL import Image
from pydantic import BaseModel, Field

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


# --- FUNCTION 1: AESTHETIC EVALUATION ---
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
        )

    prompt = f"""
    You are an expert real estate evaluator. Review these property photos.
    The user is looking for a period property with plenty of natural light and large windows.
    Agent Description for context: {description}
    Extract the visual characteristics exactly according to the provided schema.
    """

    config = {
        "response_mime_type": "application/json",
        "response_schema": PropertyVisuals,
        "temperature": 0.1,
    }

    # Primary call to 3.5 Flash, fallback to 2.5 Flash
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash", contents=[prompt] + images, config=config
        )
    except errors.APIError as e:
        logging.warning(f"3.5 Flash overloaded ({e}). Falling back to 2.5 Flash.")
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
def extract_square_footage(floorplan_urls: list[str]) -> int:
    if not floorplan_urls:
        return 0

    try:
        response = requests.get(floorplan_urls[0], timeout=10)
        img = Image.open(BytesIO(response.content))
    except Exception as e:
        logging.error(f"Failed to load floorplan image: {e}")
        return 0

    prompt = """
    Examine this floorplan. Locate the 'Total Gross Internal Area' or similar metric.
    Return ONLY the numeric value in square feet (sq ft).
    Do not include text, commas, or symbols. If it is not present, return 0.
    """

    try:
        result = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[prompt, img],
            config={"temperature": 0.0},
        )
        # Convert to float first to absorb the decimal, then int to drop it
        return int(float(result.text.strip().replace(",", "")))
    except Exception as e:
        logging.error(f"Gemini failed to extract sqft: {e}")
        return 0
