import logging
import math
from core.models import PropertyListing

def passes_dealbreakers(property_data: PropertyListing, config: dict) -> bool:
    """
    Evaluates zero-cost, hard constraints before we spend money on Gemini or Maps APIs.
    """
    db = config["dealbreakers"]
    prop_id = property_data.id

    if property_data.price_pcm > db["max_price_pcm"]:
        logging.info(
            f"[{prop_id}] Rejected: Price £{property_data.price_pcm} pcm exceeds max £{db['max_price_pcm']} pcm."
        )
        return False

    if property_data.bedrooms < db["min_bedrooms"]:
        logging.info(
            f"[{prop_id}] Rejected: Bedrooms ({property_data.bedrooms}) below min required ({db['min_bedrooms']})."
        )
        return False



    if property_data.furnishing not in db.get("required_furnishing", ["unknown"]):
        logging.info(
            f"[{prop_id}] Rejected: Furnishing '{property_data.furnishing}' not in required list."
        )
        return False

    return True


def calculate_match_score(
    property_data: PropertyListing, visual_data, commute_metrics: dict, config: dict
) -> tuple[float, dict]:
    """
    Calculates the final match score out of 100 based on the weighted criteria.
    Returns the score and a breakdown dictionary.
    """
    weights = config["weights"]
    score = 0
    breakdown = {"pros": [], "cons": [], "scorecard": {}}

    # --- 1. Commute Scoring ---
    avg_commute = commute_metrics.get("average_mins", 999)
    max_commute = config["dealbreakers"]["max_commute_mins"]

    # --- Lift Dealbreaker ---
    floor_level = property_data.floor_level or 0
    has_lift = property_data.has_lift or False
    lift_threshold = config["dealbreakers"].get("requires_lift_if_above_floor", 1)
    
    if floor_level > lift_threshold and not has_lift:
        logging.info(f"Property {property_data.id} failed lift dealbreaker (Floor {floor_level}, No Lift).")
        return 0.0, {"pros": [], "cons": [f"Missing required lift (Floor {floor_level})"]}

    # Calculate score (positive if under max, negative if over)
    if max_commute == 0:
        commute_score = 0
    else:
        commute_score = weights["commute"] * (1 - (avg_commute / max_commute))
    
    # Cap the penalty so an unreachable location (999 mins) doesn't result in -300 points
    # Let's cap the maximum penalty at -30 points.
    if commute_score < -30:
        commute_score = -30
        
    score += commute_score
    breakdown["scorecard"]["commute"] = round(commute_score, 1)

    if commute_score > 0:
        breakdown["pros"].append(f"{math.ceil(avg_commute)}m Commute")
    elif commute_score < 0:
        breakdown["cons"].append(f"Long Commute ({math.ceil(avg_commute)}m)")

    # --- 2. Price Scoring ---
    # Rewards properties that leave headroom in the budget
    budget_headroom = (
        config["dealbreakers"]["max_price_pcm"] - property_data.price_pcm
    ) / max(1, config["dealbreakers"]["max_price_pcm"])
    price_score = min(weights["price"], weights["price"] * (budget_headroom / 0.3))
    price_score = max(0, price_score)
    score += price_score
    breakdown["scorecard"]["price"] = round(price_score, 1)

    # --- 3. Aesthetics Scoring (from Gemini) ---
    light_percentage = visual_data.natural_light_score / 10.0
    light_score = weights["natural_light"] * light_percentage
    score += light_score
    breakdown["scorecard"]["natural_light"] = round(light_score, 1)

    pf_score = weights["period_features"] if visual_data.is_period_property else 0
    score += pf_score
    breakdown["scorecard"]["period_features"] = pf_score
    if pf_score > 0: breakdown["pros"].append("Period Features")

    sw_score = weights["sash_windows"] if visual_data.has_sash_windows else 0
    score += sw_score
    breakdown["scorecard"]["sash_windows"] = sw_score
    if sw_score > 0: breakdown["pros"].append("Sash Windows")

    gd_score = weights["garden"] if property_data.has_garden else 0
    score += gd_score
    breakdown["scorecard"]["garden"] = gd_score
    if gd_score > 0: breakdown["pros"].append("Private Garden")

    if visual_data.exterior_material.lower() in ["pebble dash", "cladding"]:
        pen = config["penalties"]["ugly_exterior"]
        score += pen
        breakdown["scorecard"]["penalty_ugly_exterior"] = pen
        breakdown["cons"].append(f"Ugly exterior ({visual_data.exterior_material})")

    if getattr(visual_data, "has_virtual_staging", False):
        pen = config["penalties"].get("virtual_staging", -15)
        score += pen
        breakdown["scorecard"]["penalty_virtual_staging"] = pen
        breakdown["cons"].append("Virtual Staging")
    if getattr(visual_data, "has_wide_angle_distortion", False):
        pen = config["penalties"].get("wide_angle_distortion", -10)
        score += pen
        breakdown["scorecard"]["penalty_wide_angle_distortion"] = pen
        breakdown["cons"].append("Wide Angle Distortion")
    if getattr(visual_data, "epc_rating", "Unknown") in ["F", "G"]:
        pen = config["penalties"].get("poor_epc_rating", -30)
        score += pen
        breakdown["scorecard"]["penalty_poor_epc"] = pen
        breakdown["cons"].append(f"Poor EPC Rating ({visual_data.epc_rating})")

    if property_data.is_noisy_location:
        pen = config["penalties"].get("noisy_location", -20)
        score += pen
        breakdown["scorecard"]["penalty_noisy_location"] = pen
        breakdown["cons"].append("Noisy Location (near A-road/railway)")

    # --- 4. Size Scoring (Bonus) ---
    size_score = 0
    # If the floorplan extraction worked, grant proportion points
    if (property_data.sqft or 0) > 0:
        size_ratio = min(
            1.0, property_data.sqft / 1500
        )  # Assumes 1500 sqft is "perfect"
        size_score = weights["total_size"] * size_ratio
        score += size_score
    breakdown["scorecard"]["total_size"] = round(size_score, 1)

    bed_score = 0
    bedroom_length = property_data.master_bedroom_length_m or 0.0
    if bedroom_length > 0:
        bed_ratio = min(1.0, bedroom_length / 4.0) # Assumes 4m is perfect
        bed_score = weights["bedroom_size"] * bed_ratio
        score += bed_score
    breakdown["scorecard"]["bedroom_size"] = round(bed_score, 1)

    # --- 5. Floorplan Enhancements (Penalties & Bonuses) ---
    # If we successfully extracted floorplan details (reception_length_m > 0):
    reception_len = property_data.reception_length_m or 0.0
    if reception_len > 0:
        if reception_len < 2.14:
            pen = config["penalties"].get("small_reception", -20)
            score += pen
            breakdown["scorecard"]["penalty_small_reception"] = pen
            breakdown["cons"].append(f"Small Reception ({reception_len}m)")
        
        if property_data.reception_on_ground_floor is False:
            pen = config["penalties"].get("not_ground_floor_reception", -15)
            score += pen
            breakdown["scorecard"]["penalty_not_ground_floor_reception"] = pen
            breakdown["cons"].append("Upper Floor Reception")

    hc_score = 0
    ceiling_height = property_data.max_ceiling_height_m or 0.0
    if ceiling_height > 2.7:
        # Scale bonus proportionally up to 3.3m
        ratio = min(1.0, (ceiling_height - 2.7) / (3.3 - 2.7))
        hc_score = weights.get("high_ceilings", 15) * ratio
        score += hc_score
        breakdown["pros"].append(f"High Ceilings ({ceiling_height}m)")
    breakdown["scorecard"]["high_ceilings"] = round(hc_score, 1)

    return round(score, 2), breakdown
