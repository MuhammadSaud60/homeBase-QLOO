"""Gemini narration. The plan may only contain places Qloo returned."""
import json
import os

import requests

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


def is_live():
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def why_line(stop, destination=None):
    if stop.get("source") == "city":
        return f"One of {destination}'s most loved places, picked by Qloo."
    expl = stop.get("explainability") or []
    if expl:
        return f"Qloo links this to your love of {expl[0]['label']}."
    return "Qloo picked this for your taste."


def to_stop(p, destination=None):
    expl = p.get("explainability") or []
    props = p.get("properties") or {}
    return {
        "name": p["name"],
        "kind": p.get("kind") or "place",
        "qlooEntityId": p.get("entity_id"),
        "affinity": p.get("affinity"),
        "why": why_line(p, destination),
        "tastes": [e["label"] for e in expl[:2]],
        "image": props.get("image") or None,
        "address": props.get("address") or None,
        "rating": props.get("business_rating") or None,
        "priceLevel": props.get("price_level") or None,
    }


def _call_gemini(prompt, max_output_tokens):
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
        f"?key={requests.utils.quote(os.environ.get('GEMINI_API_KEY', ''))}"
    )
    res = requests.post(
        url,
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": max_output_tokens,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        headers={"Content-Type": "application/json"},
        timeout=120,
    )
    if not res.ok:
        raise RuntimeError(f"Gemini failed ({res.status_code}): {res.text[:200]}")
    data = res.json() or {}
   
    candidates = data.get("candidates") or []
    parts = []
    if candidates:
        parts = ((candidates[0].get("content") or {}).get("parts") or [])
    text = "".join(p.get("text") or "" for p in parts)

    start = text.find("{")
    end = text.rfind("}")
    parsed = None
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            parsed = None
    if not parsed:
        finish = (candidates[0] or {}).get("finishReason") if candidates else None
        print("gemini unparseable response len:", len(text), "finish:", finish,
              "head:", text[:200], "tail:", text[-300:])
        raise RuntimeError("Gemini returned unparseable JSON")
    return parsed


WEEK_TITLES = ["Settle in", "Eat like a local", "Find your people", "Make it yours"]


def compose_first_month_with_gemini(destination, tastes, places):
    place_list = [
        {
            "index": i,
            "name": p["name"],
            "kind": p.get("kind"),
            "tags": p.get("tags"),
            "affinity": p.get("affinity"),
            "why": why_line(p, destination),
            "address": (p.get("properties") or {}).get("address") or None,
            "rating": (p.get("properties") or {}).get("business_rating") or None,
            "priceLevel": (p.get("properties") or {}).get("price_level") or None,
        }
        for i, p in enumerate(places)
    ]
    taste_labels = ", ".join(t.get("label") for t in tastes)
    prompt = (
        f"You are a relocation guide. Build a first-month plan (4 weeks) for someone who just moved to {destination} "
        f"and loves: {taste_labels}.\n"
        "You may ONLY use the places in this list, given by Qloo's taste graph. "
        "Do not invent, rename or add any place, restaurant, bar or venue. Use each place at most once. "
        "4 stops per week, in this shape:\n"
        'Week 1 "Settle in": neighbourhood essentials, cafes, markets, a first walk.\n'
        'Week 2 "Eat like a local": restaurants and food spots.\n'
        'Week 3 "Find your people": music venues, bars and gathering spots.\n'
        'Week 4 "Make it yours": the best remaining places, a mix.\n'
        "Return JSON ONLY, no other text, in this exact shape:\n"
        '{"weeks":[{"week":1,"title":"Settle in","theme":"one short theme line","stops":[0,3,5,7]}]}\n'
        "The \"stops\" arrays contain the index numbers from the place list below.\n"
        f"Places:\n{json.dumps(place_list)}"
    )
    parsed = _call_gemini(prompt, 8192)
    used = set()
    out_weeks = []
    for w in (parsed.get("weeks") or [])[:4]:
        stops = []
        for idx in (w.get("stops") or []):
            if not isinstance(idx, int) or idx < 0 or idx >= len(places):
                continue
            if idx in used:
                continue
            used.add(idx)
            stops.append(to_stop(places[idx], destination))
        if not stops:
            continue
        title = w.get("title") or (WEEK_TITLES[len(out_weeks)] if len(out_weeks) < len(WEEK_TITLES) else "A week in")
        out_weeks.append({
            "week": w.get("week") or len(out_weeks) + 1,
            "title": title[:60],
            "theme": (w.get("theme") or "")[:160],
            "stops": stops,
        })
    if not out_weeks:
        raise RuntimeError("Gemini produced no valid weeks")
    return {"weeks": out_weeks}
