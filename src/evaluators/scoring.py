import logging


def passes_dealbreakers(property_data: dict, config: dict) -> bool:
    """
    Evaluates zero-cost, hard constraints before we spend money on Gemini or Maps APIs.
    """
    db = config["dealbreakers"]
    prop_id = property_data.get("id", "Unknown")

    if property_data["price_pcm"] > db["max_price_pcm"]:
        logging.info(
            f"[{prop_id}] Rejected: Price £{property_data['price_pcm']} pcm exceeds max £{db['max_price_pcm']} pcm."
        )
        return False

    if property_data["bedrooms"] < db["min_bedrooms"]:
        logging.info(
            f"[{prop_id}] Rejected: Bedrooms ({property_data['bedrooms']}) below min required ({db['min_bedrooms']})."
        )
        return False

    # Only check sqft if it was successfully extracted by Gemini
    if (
        property_data.get("sqft", 0) > 0
        and property_data["sqft"] < db["min_total_sqft"]
    ):
        logging.info(
            f"[{prop_id}] Rejected: Size {property_data['sqft']} sq ft below min required {db['min_total_sqft']} sq ft."
        )
        return False

    return True


def calculate_match_score(
    property_data: dict, visual_data, commute_metrics: dict, config: dict
) -> float:
    """
    Calculates the final match score out of 100 based on the weighted criteria.
    """
    weights = config["weights"]
    score = 0

    # --- 1. Commute Scoring & Hard Dealbreaker ---
    avg_commute = commute_metrics.get("average_mins", 999)
    max_commute = config["dealbreakers"]["max_commute_mins"]

    # If it's too far or unreachable by transit, kill the score entirely
    if avg_commute > max_commute:
        logging.info(
            f"Property {property_data['id']} failed commute dealbreaker ({avg_commute} mins)."
        )
        return 0.0

    # Reward properties that are significantly under the max commute time
    # e.g., A 15-min commute on a 45-min max gets ~66% of the allocated points.
    commute_score = weights["commute"] * (1 - (avg_commute / max_commute))
    score += max(0, commute_score)

    # --- 2. Price Scoring ---
    # Rewards properties that leave headroom in the budget
    budget_headroom = (
        config["dealbreakers"]["max_price_pcm"] - property_data["price_pcm"]
    ) / config["dealbreakers"]["max_price_pcm"]
    price_score = min(weights["price"], weights["price"] * (budget_headroom / 0.3))
    score += max(0, price_score)

    # --- 3. Aesthetics Scoring (from Gemini) ---
    light_percentage = visual_data.natural_light_score / 10.0
    score += weights["natural_light"] * light_percentage

    if visual_data.is_period_property:
        score += weights["period_features"]
    if visual_data.has_sash_windows:
        score += weights["sash_windows"]
    if property_data.get("has_garden"):
        score += weights["garden"]

    if visual_data.exterior_material.lower() in ["pebble dash", "cladding"]:
        score += config["penalties"]["ugly_exterior"]

    # --- 4. Size Scoring (Bonus) ---
    # If the floorplan extraction worked, grant proportion points
    if property_data.get("sqft", 0) > 0:
        size_ratio = min(
            1.0, property_data["sqft"] / 1500
        )  # Assumes 1500 sqft is "perfect"
        score += weights["total_size"] * size_ratio

    return round(score, 2)
