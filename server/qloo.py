"""Qloo client, live API only. Every call hits hackathon.api.qloo.com with X-Api-Key."""
import os

import requests

from .countries import COUNTRIES

BASE = "https://hackathon.api.qloo.com"
TIMEOUT = 30


def real_search(query):
    res = requests.get(
        f"{BASE}/search",
        params={"query": query},
        headers={"X-Api-Key": os.environ.get("QLOO_API_KEY", "")},
        timeout=TIMEOUT,
    )
    if not res.ok:
        raise RuntimeError(f"Qloo search failed ({res.status_code})")
    results = (res.json() or {}).get("results") or []
    if not results:
        return None
    top = results[0]
    types = top.get("types") or []
    return {
        "id": top.get("entity_id") or top.get("id"),
        "name": top.get("name") or query,
        "type": types[0] if types else (top.get("type") or None),
    }


VISIT_DENY_TAGS = [
    "repair", "pharmacy", "hospital", "clinic", "school", "university",
    "dormitory", "student residence", "garage", "car repair", "real estate",
    "office", "bank", "atm", "parking lot", "laundry", "dry cleaner",
    "funeral", "tax", "insurance office",
]


def is_visitable(e):
    props = e.get("properties") or {}
    if props.get("hotel_class") is not None or props.get("number_of_rooms") is not None:
        return False
    tag_text = " ".join((t.get("name") or "") for t in (e.get("tags") or [])).lower()
    return not any(w in tag_text for w in VISIT_DENY_TAGS)


def classify_kind(e):
    props = e.get("properties") or {}
    if props.get("hotel_class") is not None or props.get("number_of_rooms") is not None:
        return "hotel"
    tag_text = " ".join((t.get("name") or "") for t in (e.get("tags") or [])).lower()

    def has(*words):
        return any(w in tag_text for w in words)

    if has("jazz", "live music", "concert", "cabaret", "opera", "theatre", "theater", "nightclub", "club"):
        return "music"
    if has("market", "bazaar"):
        return "market"
    if has("cafe", "coffee", "breakfast", "bakery", "tea house"):
        return "cafe"
    if has("bar", "cocktail", "beer", "wine bar", "pub"):
        return "bar"
    if props.get("cuisine_description") or props.get("menu_url") or props.get("specialty_dishes") or props.get("price_level") is not None:
        return "restaurant"
    if has("museum", "historic", "architecture", "monument", "park", "garden", "viewpoint",
           "landmark", "gallery", "temple", "palace", "cathedral", "church", "mosque",
           "bridge", "square", "beach", "lake", "mountain"):
        return "attraction"
    return "place"


def normalize_real_entity(e, signal_labels, source):
    props = e.get("properties") or {}
    q = e.get("query") or {}
    image = props.get("image")
    if isinstance(image, dict):
        image = image.get("url")
    elif not isinstance(image, str):
        image = None
    expl = []
    sig_list = (q.get("explainability") or {}).get("signal.interests.entities") or []
    for item in sig_list:
        if not item or not isinstance(item.get("score"), (int, float)):
            continue
        label = signal_labels.get(item.get("entity_id")) or item.get("entity_id")
        expl.append({"label": label, "score": round(item["score"] * 100) / 100})
    expl.sort(key=lambda x: -x["score"])
    if source == "city":
        popularity = e.get("popularity")
        affinity = round(popularity * 100) / 100 if isinstance(popularity, (int, float)) else 0.85
    else:
        qa = q.get("affinity")
        popularity = e.get("popularity")
        if isinstance(qa, (int, float)):
            affinity = round(qa * 100) / 100
        elif isinstance(popularity, (int, float)):
            affinity = round(popularity * 100) / 100
        else:
            affinity = 0.7
    business_rating = props.get("business_rating")
    price_level = props.get("price_level")
    return {
        "entity_id": e.get("entity_id") or e.get("id"),
        "name": e.get("name"),
        "type": e.get("subtype") or e.get("type") or "urn:entity:place",
        "kind": classify_kind(e),
        "source": source,
        "tags": [t.get("name") or "" for t in (e.get("tags") or []) if t.get("name")],
        "affinity": affinity,
        "explainability": expl,
        "properties": {
            "description": props.get("description") or props.get("short_description") or props.get("overall_description") or "",
            "image": image or None,
            "address": props.get("address") or None,
            "business_rating": round(business_rating * 10) / 10 if isinstance(business_rating, (int, float)) else None,
            "price_level": price_level if isinstance(price_level, (int, float)) else None,
            "geocode": props.get("geocode") or None,
        },
    }


def real_insights(location_query, signal_entity_ids, signal_labels, take=30):
    base = {
        "filter.type": "urn:entity:place",
        "filter.location.query": location_query,
        "filter.exists": "properties.image",
        "feature.explainability": "true",
    }
    taste_params = {**base, "take": str(take)}
    if signal_entity_ids:
        taste_params["signal.interests.entities"] = ",".join(signal_entity_ids)
    city_params = {**base, "take": "15"}

    def fetch_insights(params):
        res = requests.get(
            f"{BASE}/v2/insights",
            params=params,
            headers={"X-Api-Key": os.environ.get("QLOO_API_KEY", "")},
            timeout=TIMEOUT,
        )
        if not res.ok:
            raise RuntimeError(f"Qloo insights failed ({res.status_code}): {res.text[:200]}")
        return res.json()

    taste_data = fetch_insights(taste_params)
    city_data = None
    try:
        city_data = fetch_insights(city_params)
    except Exception as err:
        print("qloo city-essentials call failed, continuing with taste results:", err)

    locality = {"name": location_query, "country": ""}
    loc_filter = ((taste_data.get("query") or {}).get("localities") or {}).get("filter")
    if loc_filter and loc_filter[0]:
        locality = {"name": loc_filter[0].get("name") or location_query, "country": ""}

    seen = set()
    entities = []

    def add_all(data, source):
        raw = ((data or {}).get("results") or {}).get("entities") or []
        for e in raw:
            if not is_visitable(e):
                continue
            n = normalize_real_entity(e, signal_labels or {}, source)
            if not n["name"] or n["kind"] == "hotel" or n["entity_id"] in seen:
                continue
            seen.add(n["entity_id"])
            entities.append(n)

    add_all(taste_data, "taste")
    if city_data:
        add_all(city_data, "city")
    entities.sort(key=lambda x: -x["affinity"])
    return {"entities": entities, "locality": locality}


def country_name(code):
    return COUNTRIES.get((code or "").upper()) or code or ""


def normalize_destination(e, signal_labels):
    props = e.get("properties") or {}
    geo = props.get("geocode") or {}
    q = e.get("query") or {}
    expl = []
    sig_list = (q.get("explainability") or {}).get("signal.interests.entities") or []
    for item in sig_list:
        if not item or not isinstance(item.get("score"), (int, float)):
            continue
        expl.append({
            "label": signal_labels.get(item.get("entity_id")) or item.get("entity_id"),
            "score": round(item["score"] * 100) / 100,
        })
    expl.sort(key=lambda x: -x["score"])
    code = geo.get("country_code") or ""
    qa = q.get("affinity")
    popularity = e.get("popularity")
    if isinstance(qa, (int, float)):
        affinity = round(qa * 100) / 100
    elif isinstance(popularity, (int, float)):
        affinity = round(popularity * 100) / 100
    else:
        affinity = 0.7
    return {
        "entity_id": e.get("entity_id") or e.get("id"),
        "name": e.get("name"),
        "countryCode": code,
        "country": country_name(code),
        "region": geo.get("admin1_region") or "",
        "affinity": affinity,
        "popularity": round(popularity * 100) / 100 if isinstance(popularity, (int, float)) else None,
        "tags": [t.get("name") or "" for t in (e.get("tags") or []) if t.get("name")],
        "explainability": expl,
    }


def real_search_tag(query, parent_type):
    params = {"filter.query": query}
    if parent_type:
        params["filter.parents.types"] = parent_type
    res = requests.get(
        f"{BASE}/v2/tags",
        params=params,
        headers={"X-Api-Key": os.environ.get("QLOO_API_KEY", "")},
        timeout=TIMEOUT,
    )
    if not res.ok:
        return None
    data = res.json() or {}
    tags = ((data.get("results") or {}).get("tags") or data.get("tags") or [])
    if not tags:
        return None
    cuisine = next((t for t in tags if (t.get("type") or "").startswith("urn:tag:cuisine:")), None)
    top = cuisine or tags[0]
    return {"id": top.get("id") or top.get("tag_id"), "name": top.get("name") or query}


def _destination_query(params, signal_labels):
    res = requests.get(
        f"{BASE}/v2/insights",
        params=params,
        headers={"X-Api-Key": os.environ.get("QLOO_API_KEY", "")},
        timeout=TIMEOUT,
    )
    if not res.ok:
        raise RuntimeError(f"Qloo destination insights failed ({res.status_code}): {res.text[:200]}")
    raw = ((res.json() or {}).get("results") or {}).get("entities") or []
    seen = set()
    out = []
    for e in raw:
        n = normalize_destination(e, signal_labels or {})
        if not n["name"]:
            continue
        key = n["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    out.sort(key=lambda x: -x["affinity"])
    return out


def _drop_region_duplicates(out):
    city_names = {f"{c['countryCode']}|{c['name'].lower()}" for c in out}
    return [
        c for c in out
        if not (c["region"] and c["name"].lower() != c["region"].lower()
                and f"{c['countryCode']}|{c['region'].lower()}" in city_names)
    ]


def real_destination_insights(signal_entity_ids, signal_tag_ids, signal_labels, take=10):
    params = {
        "filter.type": "urn:entity:destination",
        "feature.explainability": "true",
        "take": str(take),
    }
    if signal_entity_ids:
        params["signal.interests.entities"] = ",".join(signal_entity_ids)
    if signal_tag_ids:
        params["signal.interests.tags"] = ",".join(signal_tag_ids)
    return _drop_region_duplicates(_destination_query(params, signal_labels))


def real_destination_insights_single(entity_id=None, tag_id=None, label=None, take=20):
    """One taste on its own: a single entity or a single cuisine tag.
    Used for the per-taste lists that RRF fuses. Returns Qloo rank order."""
    params = {
        "filter.type": "urn:entity:destination",
        "feature.explainability": "true",
        "take": str(take),
    }
    if entity_id:
        params["signal.interests.entities"] = entity_id
    if tag_id:
        params["signal.interests.tags"] = tag_id
    labels = {entity_id: label} if entity_id and label else {}
    return _drop_region_duplicates(_destination_query(params, labels))


def is_live():
    return bool(os.environ.get("QLOO_API_KEY", "").strip())


def search_taste(label):
    return real_search(label)


def get_place_insights(destination, tastes, signal_entity_ids, signal_labels, take=30):
    return real_insights(
        location_query=destination,
        signal_entity_ids=signal_entity_ids,
        signal_labels=signal_labels,
        take=take or 30,
    )


def get_destination_insights(tastes, signal_entity_ids, signal_tag_ids, signal_labels, take=10):
    return real_destination_insights(
        signal_entity_ids=signal_entity_ids,
        signal_tag_ids=signal_tag_ids,
        signal_labels=signal_labels,
        take=take or 10,
    )


def search_tag(query, parent_type):
    return real_search_tag(query, parent_type)


def get_destination_insights_single(entity_id=None, tag_id=None, label=None, take=20):
    return real_destination_insights_single(
        entity_id=entity_id, tag_id=tag_id, label=label, take=take or 20,
    )
