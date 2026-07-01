from sentence_transformers import SentenceTransformer, util


MODEL_NAME = "all-MiniLM-L6-v2"

_model = None
_candidate_cache = {}


MERCHANT_HINTS = {
    # Shopping / retail
    "lovisa": "jewellery accessories fashion retail shopping",
    "boots": "pharmacy beauty cosmetics healthcare retail shopping",
    "boots retail": "pharmacy beauty cosmetics healthcare retail shopping",
    "dunnes": "supermarket groceries clothing home department store retail shopping",
    "dunnes stores": "supermarket groceries clothing home department store retail shopping",
    "tesco store": "supermarket groceries food shopping",
    "tesco stores": "supermarket groceries food shopping",
    "aldi": "supermarket groceries food shopping",
    "centra": "convenience store groceries food shopping",

    # Mobile / internet / bills
    "tesco mobile": "mobile phone bill sim plan phone recharge",
    "48months": "mobile phone bill sim plan phone recharge",
    "48months.ie": "mobile phone bill sim plan phone recharge",
    "virgin media": "wifi broadband internet household bill utility bill",

    # Transport / commute
    "luas": "tram commute public transport travel leap card",
    "luas transdev": "tram commute public transport travel leap card",

    # Going out / food / entertainment
    "boi restaur": "restaurant eating out dining food cafe takeaway going out",
    "restaur": "restaurant eating out dining food cafe takeaway going out",
    "restaurant": "restaurant eating out dining food cafe takeaway going out",
    "mugg ugly": "coffee cafe eating out going out",
    "bread 41": "bakery cafe coffee eating out going out",
    "lane7": "arcade bowling entertainment games going out",
    "square one": "restaurant cafe entertainment going out",
    "super asia": "asian restaurant food eating out takeaway going out",
    "cafe": "cafe coffee eating out going out",
    "coffee": "coffee cafe eating out going out",

    # Travel / accommodation
    "makemytrip": "travel accommodation hotel booking stay holiday trip going out",
    "make my trip": "travel accommodation hotel booking stay holiday trip going out",
    "booking.com": "travel accommodation hotel booking stay holiday trip",
    "airbnb": "travel accommodation stay holiday trip"
}


INVESTMENT_WORDS = {
    "investment",
    "investments",
    "invest",
    "trading",
    "stocks",
    "shares",
    "etf",
    "crypto",
    "revolut investment",
    "wealth",
    "broker",
    "brokerage"
}


RESTAURANT_WORDS = [
    "boi restaur",
    "restaur",
    "restaurant",
    "dining",
    "eating out",
    "takeaway",
    "take away",
    "cafe",
    "coffee",
    "bakery",
    "bread 41",
    "mugg ugly",
    "super asia",
    "lane7",
    "square one"
]


GROCERY_WORDS = [
    "supermarket",
    "groceries",
    "grocery",
    "food shopping",
    "tesco store",
    "tesco stores",
    "aldi",
    "lidl",
    "centra",
    "supervalu"
]


def get_model():
    global _model

    if _model is None:
        print("Loading local AI categorization model...")
        _model = SentenceTransformer(MODEL_NAME)

    return _model


def normalize_text(value):
    return str(value or "").lower().strip()


def clean_transaction_description(description):
    text = normalize_text(description)

    noise_tokens = [
        "vdc-",
        "vdp-",
        "posc",
        "pos",
        "d/d",
        "txn",
        "txndate",
        "www.aib.ie",
        "standardconditions",
        "allied",
        "irish",
        "banks",
        "regulated",
        "p.l.c.",
        "p.l.c",
        "plc"
    ]

    for token in noise_tokens:
        text = text.replace(token, " ")

    month_tokens = [
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec"
    ]

    parts = text.split()
    cleaned_parts = []

    for part in parts:
        lower_part = part.lower()

        looks_like_date_token = False

        for month in month_tokens:
            if month in lower_part and any(char.isdigit() for char in lower_part):
                looks_like_date_token = True
                break

        if not looks_like_date_token:
            cleaned_parts.append(part)

    return " ".join(cleaned_parts).strip()


def get_merchant_context(description):
    cleaned = clean_transaction_description(description)
    hints = []

    for merchant_pattern, merchant_hint in MERCHANT_HINTS.items():
        if merchant_pattern in cleaned:
            hints.append(merchant_hint)

    if hints:
        return f"{cleaned}. Extra merchant context: {' '.join(hints)}"

    return cleaned


def build_category_candidates(config):
    candidates = []

    for category in config.get("categories", []):
        category_id = category.get("category_id", "")
        category_name = category.get("name", "")
        category_color = category.get("color", "")
        category_description = category.get("description", "")

        for subcategory in category.get("subcategories", []):
            subcategory_id = subcategory.get("subcategory_id", "")
            subcategory_name = subcategory.get("name", "")
            subcategory_description = subcategory.get("description", "")

            examples = ", ".join(subcategory.get("examples", []))
            keywords = ", ".join(subcategory.get("keywords", []))

            candidate_text = f"""
            Budget category name: {category_name}
            Budget category color: {category_color}
            Budget category meaning: {category_description}

            Budget subcategory name: {subcategory_name}
            Budget subcategory meaning: {subcategory_description}

            Example merchants or references: {examples}
            Known keywords: {keywords}

            This candidate is for personal budgeting transactions that belong to:
            {category_name} > {subcategory_name}
            """

            candidates.append({
                "category_id": category_id,
                "category_name": category_name,
                "subcategory_id": subcategory_id,
                "subcategory": subcategory_name,
                "candidate_text": candidate_text
            })

    return candidates


def get_candidate_embeddings(config):
    model = get_model()

    cache_key = str(config.get("categories", []))

    if cache_key in _candidate_cache:
        return _candidate_cache[cache_key]

    candidates = build_category_candidates(config)
    candidate_texts = [candidate["candidate_text"] for candidate in candidates]

    if not candidate_texts:
        _candidate_cache[cache_key] = {
            "candidates": [],
            "embeddings": None
        }
        return _candidate_cache[cache_key]

    embeddings = model.encode(candidate_texts, convert_to_tensor=True)

    _candidate_cache[cache_key] = {
        "candidates": candidates,
        "embeddings": embeddings
    }

    return _candidate_cache[cache_key]


def keyword_overlap_score(transaction_text, candidate):
    candidate_text = normalize_text(candidate.get("candidate_text", ""))

    transaction_words = set(
        word.strip(".,:-_/()")
        for word in normalize_text(transaction_text).split()
        if len(word.strip(".,:-_/()")) >= 4
    )

    candidate_words = set(
        word.strip(".,:-_/()")
        for word in candidate_text.split()
        if len(word.strip(".,:-_/()")) >= 4
    )

    if not transaction_words or not candidate_words:
        return 0.0

    overlap = transaction_words.intersection(candidate_words)

    return min(len(overlap) * 0.04, 0.20)


def candidate_is_going_out(candidate):
    subcategory = normalize_text(candidate.get("subcategory", ""))
    candidate_text = normalize_text(candidate.get("candidate_text", ""))

    return (
        "going out" in subcategory
        or "eating out" in candidate_text
        or "restaurant" in candidate_text
        or "restaurants" in candidate_text
        or "cafe" in candidate_text
        or "coffee" in candidate_text
        or "arcade" in candidate_text
        or "entertainment" in candidate_text
        or "activities" in candidate_text
    )


def candidate_is_groceries(candidate):
    subcategory = normalize_text(candidate.get("subcategory", ""))
    candidate_text = normalize_text(candidate.get("candidate_text", ""))

    return (
        "grocer" in subcategory
        or "grocer" in candidate_text
        or "supermarket" in candidate_text
        or "food shopping" in candidate_text
    )


def candidate_is_bills(candidate):
    subcategory = normalize_text(candidate.get("subcategory", ""))
    candidate_text = normalize_text(candidate.get("candidate_text", ""))

    return (
        "bill" in subcategory
        or "bills" in subcategory
        or "utility" in candidate_text
        or "electricity" in candidate_text
        or "gas" in candidate_text
    )


def domain_boost_score(transaction_text, candidate):
    text = normalize_text(transaction_text)
    subcategory = normalize_text(candidate.get("subcategory", ""))
    candidate_text = normalize_text(candidate.get("candidate_text", ""))

    score = 0.0

    shopping_words = [
        "jewellery", "accessories", "fashion", "clothing",
        "beauty", "cosmetics", "retail", "department store",
        "shopping"
    ]

    going_out_words = [
        "restaurant", "restaur", "cafe", "coffee", "bakery", "takeaway",
        "dining", "eating out", "arcade", "bowling", "entertainment",
        "games", "lounge", "super asia", "boi restaur", "bread 41",
        "mugg ugly", "lane7", "square one"
    ]

    grocery_words = [
        "supermarket", "groceries", "grocery", "food shopping",
        "tesco store", "tesco stores", "aldi", "centra", "lidl", "supervalu"
    ]

    mobile_words = [
        "mobile", "phone bill", "sim plan", "phone recharge",
        "48months", "tesco mobile"
    ]

    wifi_words = [
        "wifi", "broadband", "internet", "virgin media"
    ]

    commute_words = [
        "luas", "tram", "transport", "commute", "leap card"
    ]

    accommodation_travel_words = [
        "travel", "flight", "hotel", "holiday", "booking",
        "trip", "accommodation", "stay", "makemytrip"
    ]

    if any(word in text for word in shopping_words):
        if "shopping" in subcategory or "shopping" in candidate_text:
            score += 0.20

    if any(word in text for word in going_out_words):
        if candidate_is_going_out(candidate):
            score += 0.45

    if any(word in text for word in grocery_words):
        if candidate_is_groceries(candidate):
            score += 0.25

    if any(word in text for word in mobile_words):
        if "mobile" in subcategory or "phone" in candidate_text:
            score += 0.30

    if any(word in text for word in wifi_words):
        if "wifi" in subcategory or "internet" in candidate_text or "broadband" in candidate_text:
            score += 0.30

    if any(word in text for word in commute_words):
        if "commute" in subcategory or "transport" in candidate_text or "luas" in candidate_text:
            score += 0.30

    if any(word in text for word in accommodation_travel_words):
        if (
            "going out" in subcategory
            or "travel" in candidate_text
            or "holiday" in candidate_text
            or "accommodation" in candidate_text
            or "hotel" in candidate_text
            or "trip" in candidate_text
        ):
            score += 0.35

    return score


def penalty_score(transaction_text, candidate):
    text = normalize_text(transaction_text)
    subcategory = normalize_text(candidate.get("subcategory", ""))
    candidate_text = normalize_text(candidate.get("candidate_text", ""))

    penalty = 0.0

    candidate_is_investment = (
        "investment" in subcategory
        or "investment" in candidate_text
        or "trading" in candidate_text
    )

    transaction_has_investment_signal = any(
        word in text
        for word in INVESTMENT_WORDS
    )

    if candidate_is_investment and not transaction_has_investment_signal:
        penalty += 0.30

    restaurant_signal = any(word in text for word in RESTAURANT_WORDS)

    if restaurant_signal and candidate_is_groceries(candidate):
        penalty += 0.45

    if restaurant_signal and candidate_is_bills(candidate):
        penalty += 0.45

    grocery_signal = any(word in text for word in GROCERY_WORDS)

    if grocery_signal and candidate_is_going_out(candidate):
        penalty += 0.20

    travel_or_accommodation_signal = any(
        word in text
        for word in [
            "makemytrip",
            "hotel",
            "accommodation",
            "holiday",
            "trip",
            "booking",
            "stay"
        ]
    )

    if travel_or_accommodation_signal and candidate_is_bills(candidate):
        penalty += 0.40

    return penalty


def apply_final_safety_override(scored_candidates, transaction_text):
    """
    Final guardrail.

    If the transaction clearly looks like a restaurant/cafe/eating-out transaction
    and the user has a going-out style subcategory, choose that instead of groceries.

    This is still user-configurable because it only selects among the user's own
    categories/subcategories.
    """
    text = normalize_text(transaction_text)

    restaurant_signal = any(word in text for word in RESTAURANT_WORDS)

    if not restaurant_signal:
        return scored_candidates[0]

    going_out_candidates = [
        item for item in scored_candidates
        if candidate_is_going_out(item["candidate"])
    ]

    if going_out_candidates:
        going_out_candidates.sort(
            key=lambda item: item["final_score"],
            reverse=True
        )

        best_going_out = going_out_candidates[0]

        # Use going out if it is reasonably close, or if current best is groceries/bills.
        current_best = scored_candidates[0]
        current_best_is_bad_food_match = (
            candidate_is_groceries(current_best["candidate"])
            or candidate_is_bills(current_best["candidate"])
        )

        if current_best_is_bad_food_match:
            return best_going_out

        if best_going_out["final_score"] >= current_best["final_score"] - 0.15:
            return best_going_out

    return scored_candidates[0]


def ai_categorize_transaction(description, config, threshold=0.28, review_threshold=0.45):
    model = get_model()
    candidate_data = get_candidate_embeddings(config)

    candidates = candidate_data["candidates"]
    candidate_embeddings = candidate_data["embeddings"]

    if not candidates or candidate_embeddings is None:
        return {
            "category_id": "uncategorized",
            "category_name": "Uncategorized",
            "subcategory": "Uncategorized",
            "matched_keyword": "",
            "category_needs_review": "yes",
            "categorization_method": "ai_no_categories",
            "ai_confidence": "0",
            "ai_error": ""
        }

    enriched_description = get_merchant_context(description)

    transaction_text = f"""
    Bank transaction merchant/reference: {description}
    Cleaned merchant/reference with context: {enriched_description}

    Choose the closest personal budgeting subcategory from the user's own budget setup.
    """

    transaction_embedding = model.encode(transaction_text, convert_to_tensor=True)

    semantic_scores = util.cos_sim(transaction_embedding, candidate_embeddings)[0]

    scored_candidates = []

    for index, candidate in enumerate(candidates):
        semantic_score = float(semantic_scores[index])
        overlap_boost = keyword_overlap_score(transaction_text, candidate)
        domain_boost = domain_boost_score(transaction_text, candidate)
        penalty = penalty_score(transaction_text, candidate)

        final_score = semantic_score + overlap_boost + domain_boost - penalty

        scored_candidates.append({
            "candidate": candidate,
            "semantic_score": semantic_score,
            "overlap_boost": overlap_boost,
            "domain_boost": domain_boost,
            "penalty": penalty,
            "final_score": final_score
        })

    scored_candidates.sort(
        key=lambda item: item["final_score"],
        reverse=True
    )

    best = apply_final_safety_override(scored_candidates, transaction_text)

    best_candidate = best["candidate"]
    best_score = best["final_score"]

    if best_score < threshold:
        return {
            "category_id": "uncategorized",
            "category_name": "Uncategorized",
            "subcategory": "Uncategorized",
            "matched_keyword": "",
            "category_needs_review": "yes",
            "categorization_method": "ai_low_confidence",
            "ai_confidence": str(round(best_score, 3)),
            "ai_error": ""
        }

    return {
        "category_id": best_candidate["category_id"],
        "category_name": best_candidate["category_name"],
        "subcategory": best_candidate["subcategory"],
        "matched_keyword": "",
        "category_needs_review": "yes" if best_score < review_threshold else "no",
        "categorization_method": "ai_embedding",
        "ai_confidence": str(round(best_score, 3)),
        "ai_error": ""
    }