"""HomeBase server. Flask app serving public/ and the /api endpoints."""
import os
import time

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

from . import llm, qloo  # noqa: E402

app = Flask(__name__, static_folder="../public", static_url_path="")
PORT = int(os.environ.get("PORT", "3000"))

_hits = {}


def rate_limited():
    now = time.time()
    key = request.remote_addr or "unknown"
    stamps = [t for t in _hits.get(key, []) if now - t < 60]
    stamps.append(now)
    _hits[key] = stamps
    return len(stamps) > 20


@app.before_request
def _rate_limit():
    if request.path.startswith("/api/") and request.path != "/api/health" and rate_limited():
        return jsonify({"ok": False, "error": "Too many requests. Wait a minute and try again."}), 429


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "qlooLive": qloo.is_live(), "llmLive": llm.is_live()})


TASTE_KINDS = ["musician", "film", "cuisine", "brand", "other"]


def clean_tastes(body):
    tastes = body.get("tastes") if isinstance(body, dict) else None
    if not isinstance(tastes, list):
        tastes = []
    out = []
    for t in tastes[:5]:
        if not isinstance(t, dict):
            continue
        label = t.get("label")
        if not isinstance(label, str) or not label.strip():
            continue
        out.append({
            "kind": t.get("kind") if t.get("kind") in TASTE_KINDS else "other",
            "label": label.strip()[:80],
        })
    return out


def _first_word(text):
    for part in (text or "").lower().replace(",", " ").split():
        cleaned = "".join(ch for ch in part if ch.isalnum())
        if cleaned:
            return cleaned
    return ""


def resolve_tastes(clean):
    signals = []
    tag_signals = []
    status = []
    for t in clean:
        try:
            hit = qloo.search_taste(t["label"])
            if not hit:
                status.append({"label": t["label"], "kind": t["kind"], "status": "unmatched"})
                continue
            # Qloo fuzzy-matches almost anything, so check the hit is really this taste:
            # the first word of what the user typed must appear in the matched name.
            first = _first_word(t["label"])
            if first and first not in (hit.get("name") or "").lower():
                status.append({"label": t["label"], "kind": t["kind"], "status": "unmatched"})
                continue
            if t["kind"] == "cuisine" and hit.get("type") == "urn:entity:place":
                tag = qloo.search_tag(t["label"], "urn:entity:place")
                if tag:
                    tag_signals.append({"label": t["label"], "tagId": tag["id"]})
                    signals.append({**t, "entityId": hit["id"], "placeOnly": True})
                else:
                    # No cuisine tag: keep the entity as a signal instead of dropping the taste.
                    signals.append({**t, "entityId": hit["id"]})
            else:
                signals.append({**t, "entityId": hit["id"]})
            status.append({"label": t["label"], "kind": t["kind"], "status": "matched"})
        except Exception as err:
            print("taste resolve failed:", t["label"], err)
            status.append({"label": t["label"], "kind": t["kind"], "status": "unmatched"})
    return signals, tag_signals, status


def confidence_for(affinity):
    if affinity >= 0.75:
        return {"label": "Strong fit", "level": "strong"}
    if affinity >= 0.6:
        return {"label": "Good fit", "level": "good"}
    return {"label": "Loose fit", "level": "loose"}


@app.post("/api/cities")
def cities():
    try:
        body = request.get_json(silent=True) or {}
        clean = clean_tastes(body)
        if len(clean) < 3:
            return jsonify({"ok": False, "error": "Add 3 to 5 things you love, so Qloo has taste to rank with."}), 400

        signals, tag_signals, taste_status = resolve_tastes(clean)
        entity_signals = [s for s in signals if not s.get("placeOnly")]
        if not entity_signals and not tag_signals:
            return jsonify({"ok": False, "error": "Qloo could not read your tastes. Try different examples."}), 502
        signal_ids = [s["entityId"] for s in entity_signals if s.get("entityId")]
        signal_labels = {s["entityId"]: s["label"] for s in entity_signals if s.get("entityId")}

        try:
            cities = qloo.get_destination_insights(
                tastes=clean,
                signal_entity_ids=signal_ids,
                signal_tag_ids=[t["tagId"] for t in tag_signals],
                signal_labels=signal_labels,
                take=10,
            )
        except Exception as err:
            print("qloo destination insights failed:", err)
            return jsonify({"ok": False, "error": "Qloo is not answering right now. Try again in a minute."}), 502

        if not cities:
            return jsonify({
                "ok": False,
                "error": "Qloo has no city match for these tastes. Try a musician or film with a bigger audience, or a different cuisine.",
            })

        top = []
        for c in cities[:5]:
            reasons = [
                {"label": e["label"], "score": e["score"]}
                for e in (c.get("explainability") or [])[:2]
            ]
            top.append({
                "name": c["name"],
                "country": c.get("country") or "",
                "countryCode": c.get("countryCode") or "",
                "region": c.get("region") or "",
                "fitScore": round(c["affinity"] * 100),
                "confidence": confidence_for(c["affinity"]),
                "reasons": reasons,
                "tags": (c.get("tags") or [])[:4],
                "qlooEntityId": c.get("entity_id"),
            })

        best = top[0]["fitScore"] / 100
        note = None
        if best < 0.6:
            note = ("Qloo found only a loose match for these tastes. Treat this ranking as a starting point, "
                    "not a verdict. Try swapping in a musician or film with a wider audience.")

        return jsonify({"ok": True, "meta": {"qlooLive": qloo.is_live()}, "cities": top, "tasteStatus": taste_status, "note": note})
    except Exception as err:
        print("cities error:", err)
        return jsonify({"ok": False, "error": "Something went wrong ranking cities. Try again."}), 500


@app.post("/api/first-month")
def first_month():
    try:
        body = request.get_json(silent=True) or {}
        destination = body.get("destination") if isinstance(body.get("destination"), str) else ""
        destination = (destination or "").strip()
        clean = clean_tastes(body)
        if not destination:
            return jsonify({"ok": False, "error": "Pick a city first."}), 400
        if not clean:
            return jsonify({"ok": False, "error": "Add at least one thing you love, so Qloo has taste to work with."}), 400

        signals, _tag_signals, _taste_status = resolve_tastes(clean)
        if not signals:
            return jsonify({"ok": False, "error": "Qloo could not read your tastes. Try different examples."}), 502
        signal_ids = [s["entityId"] for s in signals if s.get("entityId")]
        signal_labels = {s["entityId"]: s["label"] for s in signals if s.get("entityId")}

        try:
            insights = qloo.get_place_insights(
                destination=destination,
                tastes=clean,
                signal_entity_ids=signal_ids,
                signal_labels=signal_labels,
                take=30,
            )
        except Exception as err:
            print("qloo insights failed:", err)
            return jsonify({"ok": False, "error": "Qloo is not answering right now. Try again in a minute."}), 502

        places = [p for p in (insights.get("entities") or []) if p.get("name")]
        if len(places) < 12 and qloo.is_live():
            try:
                wider = qloo.get_place_insights(
                    destination=destination,
                    tastes=clean,
                    signal_entity_ids=signal_ids,
                    signal_labels=signal_labels,
                    take=50,
                )
                seen = {p["entity_id"] for p in places}
                for p in (wider.get("entities") or []):
                    if p.get("name") and p["entity_id"] not in seen:
                        places.append(p)
                        seen.add(p["entity_id"])
            except Exception as err:
                print("qloo wider retry failed:", err)

        if not places:
            return jsonify({
                "ok": False,
                "error": f"Qloo has thin data for {destination}. Try a bigger city, or different tastes. We would rather say so than invent places.",
            })

        llm_live = llm.is_live()
        if not llm_live:
            return jsonify({"ok": False, "error": "Gemini key is not set, so the first-month plan cannot be written. Add GEMINI_API_KEY and try again."}), 502
        try:
            plan = llm.compose_first_month_with_gemini(destination=destination, tastes=clean, places=places)
        except Exception as err:
            print("llm compose failed:", err)
            return jsonify({"ok": False, "error": "Gemini is not answering right now. Try again in a minute."}), 502

        matches = []
        for t in clean:
            found = []
            for week in plan["weeks"]:
                for stop in week["stops"]:
                    if len(found) >= 2:
                        break
                    if t["label"] in (stop.get("tastes") or []):
                        found.append(stop["name"])
            if found:
                matches.append({"taste": t["label"], "places": found})

        return jsonify({
            "ok": True,
            "meta": {
                "qlooLive": qloo.is_live(),
                "llmLive": llm_live,
                "destination": destination,
                "localityMatched": (insights.get("locality") or {}).get("name"),
            },
            "plan": {"weeks": plan["weeks"]},
            "matches": matches,
        })
    except Exception as err:
        print("first-month error:", err)
        return jsonify({"ok": False, "error": "Something went wrong building your first month. Try again."}), 500


if __name__ == "__main__":
    print(f"HomeBase listening on port {PORT} (qlooLive={qloo.is_live()} llmLive={llm.is_live()})")
    app.run(host="0.0.0.0", port=PORT)
