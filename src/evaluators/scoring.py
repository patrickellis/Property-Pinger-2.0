import logging
import math
from core.models import PropertyListing

def passes_dealbreakers(property_data: PropertyListing, config: dict) -> tuple[bool, str | None]:
    """
    Evaluates zero-cost, hard constraints before we spend money on Gemini or Maps APIs.
    """
    import re
    db = config["dealbreakers"]
    prop_id = property_data.id

    if property_data.bedrooms < db["min_bedrooms"]:
        reason = f"Bedrooms ({property_data.bedrooms}) below min required ({db['min_bedrooms']})."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    if property_data.bedrooms > db.get("max_bedrooms", 5):
        reason = f"Bedrooms ({property_data.bedrooms}) above max required ({db.get('max_bedrooms', 5)})."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    if property_data.price_pcm > db["max_price_pcm"]:
        reason = f"Price (£{property_data.price_pcm}) exceeds maximum budget (£{db['max_price_pcm']})."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    if property_data.furnishing not in db.get("required_furnishing", ["unknown"]):
        reason = f"Furnishing '{property_data.furnishing}' not in required list."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    desc = property_data.description.lower() if property_data.description else ""

    if re.search(r'\b(short let|short-let|shortlet|holiday let|airbnb)\b', desc):
        reason = "Description indicates a short let."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    if re.search(r'\b(student|students only|student accommodation|hmo|house share|room to rent)\b', desc) and not re.search(r'\b(suitable for professionals|not a hmo)\b', desc):
        reason = "Description indicates student accommodation or HMO."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    if re.search(r'\b(retirement|over 55s|over 60s|over-55|over-60)\b', desc):
        reason = "Description indicates retirement property."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason
        
    if re.search(r'\b(cash buyers only|cash buyer only)\b', desc):
        reason = "Description indicates cash buyers only."
        logging.info(f"[{prop_id}] Rejected: {reason}")
        return False, reason

    return True, None


def calculate_match_score(
    property_data: PropertyListing, visual_data, commute_metrics: dict, config: dict
) -> tuple[float, dict]:
    """
    Calculates the final match score out of 100 based on the weighted criteria.
    Returns the score and a breakdown dictionary.
    """
    weights = config.get("weights", {})
    penalties = config.get("penalties", {})
    score = 0
    breakdown = {"pros": [], "cons": [], "scorecard": {}}

    # --- 1. Commute Scoring ---
    avg_commute = commute_metrics.get("average_mins", 999)
    property_data.commute_mins = avg_commute if avg_commute != 999 else None
    max_commute = config["dealbreakers"]["max_commute_mins"]

    # --- Lift Dealbreaker ---
    floor_level = property_data.floor_level or 0
    has_lift = property_data.has_lift or False
    lift_threshold = config["dealbreakers"].get("requires_lift_if_above_floor", 1)
    
    if floor_level > lift_threshold and not has_lift:
        logging.info(f"Property {property_data.id} failed lift dealbreaker (Floor {floor_level}, No Lift).")
        breakdown["cons"].append(f"Missing required lift (Floor {floor_level})")

    # Calculate score (positive if under max, negative if over)
    commute_weight = weights.get("commute", 0)
    if commute_weight != 0:
        if max_commute == 0:
            commute_score = 0
        else:
            commute_score = commute_weight * (1 - (avg_commute / max_commute))
        
        # Cap the penalty so an unreachable location (999 mins) doesn't result in -300 points
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
    price_weight = weights.get("price", 0)
    if price_weight != 0:
        budget_headroom = (
            config["dealbreakers"]["max_price_pcm"] - property_data.price_pcm
        ) / max(1, config["dealbreakers"]["max_price_pcm"])
        price_score = min(price_weight, price_weight * (budget_headroom / 0.3))
        price_score = max(0, price_score)
        score += price_score
        breakdown["scorecard"]["price"] = round(price_score, 1)

    # --- 3. Natural Light Scoring (Deterministic) ---
    total_sunlight_hours = 0
    max_possible_hours = 6.0 # 6 hours of average direct sunlight is considered perfect
    
    if property_data.floorplan_graph and "rooms" in property_data.floorplan_graph:
        from evaluators.solar import calculate_average_sunlight_hours
        from core.models import Window
        
        main_rooms_count = 0
        for room in property_data.floorplan_graph["rooms"]:
            # Check if there are windows
            if "windows" in room and room["windows"]:
                windows = []
                for w_data in room["windows"]:
                    if isinstance(w_data, dict):
                        windows.append(Window(**w_data))
                    else:
                        windows.append(w_data)
                        
                hours = calculate_average_sunlight_hours(
                    property_data.latitude or 51.5, 
                    property_data.longitude or -0.1, 
                    windows
                )
                room["sunlight_hours"] = hours
                
                if room.get("room_type") in ["reception", "kitchen", "bedroom"]:
                    total_sunlight_hours += hours
                    main_rooms_count += 1
            else:
                room["sunlight_hours"] = 0.0
                if room.get("room_type") in ["reception", "kitchen", "bedroom"]:
                    main_rooms_count += 1
                    
        if main_rooms_count > 0:
            avg_sunlight = total_sunlight_hours / main_rooms_count
            light_percentage = min(1.0, avg_sunlight / max_possible_hours)
        else:
            light_percentage = visual_data.natural_light_score / 10.0
    else:
        light_percentage = visual_data.natural_light_score / 10.0

    light_weight = weights.get("natural_light", 0)
    if light_weight != 0:
        light_score = light_weight * light_percentage
        score += light_score
        breakdown["scorecard"]["natural_light"] = round(light_score, 1)

    pf_weight = weights.get("period_features", 0)
    if pf_weight != 0:
        pf_score = pf_weight if visual_data.is_period_property else 0
        score += pf_score
        breakdown["scorecard"]["period_features"] = pf_score
        if pf_score > 0: breakdown["pros"].append("Period Features")

    sw_weight = weights.get("sash_windows", 0)
    if sw_weight != 0:
        sw_score = sw_weight if visual_data.has_sash_windows else 0
        score += sw_score
        breakdown["scorecard"]["sash_windows"] = sw_score
        if sw_score > 0: breakdown["pros"].append("Sash Windows")

    gd_weight = weights.get("garden", 0)
    if gd_weight != 0:
        gd_score = gd_weight if property_data.has_garden else 0
        score += gd_score
        breakdown["scorecard"]["garden"] = gd_score
        if gd_score > 0: breakdown["pros"].append("Private Garden")


    if getattr(visual_data, "has_wide_angle_distortion", False):
        pen = penalties.get("wide_angle_distortion", 0)
        if pen != 0:
            score += pen
            breakdown["scorecard"]["penalty_wide_angle_distortion"] = pen
            breakdown["cons"].append("Wide Angle Distortion")
            
    if getattr(visual_data, "epc_rating", "Unknown") in ["F", "G"]:
        pen = penalties.get("poor_epc_rating", 0)
        if pen != 0:
            score += pen
            breakdown["scorecard"]["penalty_poor_epc"] = pen
            breakdown["cons"].append(f"Poor EPC Rating ({visual_data.epc_rating})")

    if property_data.is_noisy_location:
        pen = penalties.get("noisy_location", 0)
        if pen != 0:
            score += pen
            breakdown["scorecard"]["penalty_noisy_location"] = pen
            breakdown["cons"].append("Noisy Location (near A-road/railway)")

    # --- 4. Size Scoring (Bonus) ---
    scoring_params = config.get("scoring_parameters", {})
    size_score = 0
    # If the floorplan extraction worked, grant proportion points
    ts_weight = weights.get("total_size", 0)
    if ts_weight != 0 and (property_data.sqft or 0) > 0:
        opt_sqft = scoring_params.get("optimal_total_sqft", 1500)
        size_ratio = min(1.0, property_data.sqft / opt_sqft)
        size_score = ts_weight * ((size_ratio - 0.5) * 2)
        score += size_score
        breakdown["scorecard"]["total_size"] = round(size_score, 1)

    # --- 5. Floorplan Enhancements (Penalties & Bonuses) ---
    if property_data.reception_on_ground_floor is False:
        pen = penalties.get("not_ground_floor_reception", 0)
        if pen != 0:
            score += pen
            breakdown["scorecard"]["penalty_not_ground_floor_reception"] = pen
            breakdown["cons"].append("Upper Floor Reception")

    hc_score = 0
    ceiling_height = property_data.max_ceiling_height_m or 0.0
    hc_threshold = scoring_params.get("high_ceiling_threshold_m", 2.7)
    hc_max_scale = scoring_params.get("high_ceiling_max_scale_m", 3.3)
    
    hc_weight = weights.get("high_ceilings", 0)
    if hc_weight != 0 and ceiling_height > 0:
        if ceiling_height >= hc_threshold:
            scale_range = hc_max_scale - hc_threshold
            if scale_range <= 0:
                ratio = 1.0
            else:
                ratio = min(1.0, (ceiling_height - hc_threshold) / scale_range)
            hc_score = hc_weight * ratio
            breakdown["pros"].append(f"High Ceilings ({ceiling_height}m)")
        else:
            low_threshold = 2.4
            if ceiling_height < low_threshold:
                ratio = min(1.0, (low_threshold - ceiling_height) / 0.4)
                hc_score = -hc_weight * ratio
                breakdown["cons"].append(f"Low Ceilings ({ceiling_height}m)")
        
        score += hc_score
        breakdown["scorecard"]["high_ceilings"] = round(hc_score, 1)

    return round(score, 2), breakdown
