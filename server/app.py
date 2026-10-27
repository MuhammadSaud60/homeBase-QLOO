"""HomeBase server. Flask app serving public/ and the /api endpoints."""
import os
import time
from concurrent.futures import ThreadPoolExecutor

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
            candidates = qloo.search_taste_candidates(t["label"])
            if not candidates:
                status.append({"label": t["label"], "kind": t["kind"], "status": "unmatched"})
                continue
            # Qloo fuzzy-matches almost anything, so check the hit is really this taste:
            # the first word of what the user typed must appear in the matched name.
            first = _first_word(t["label"])
            passing = [h for h in candidates
                       if not first or first in (h.get("name") or "").lower()]
            if not passing:
                status.append({"label": t["label"], "kind": t["kind"], "status": "unmatched"})
                continue
            # Books cannot be Qloo insights signals (the endpoint rejects
            # them), so prefer the first non-book match: a book title that
            # is also a film resolves to the film.
            hit = next((h for h in passing if h.get("type") != "urn:entity:book"), None)
            if hit is None:
                status.append({"label": t["label"], "kind": t["kind"], "status": "unmatched"})
                continue
            read_as = None
            if t["kind"] == "cuisine" and hit.get("type") == "urn:entity:place":
                tag = qloo.search_tag(t["label"], "urn:entity:place")
                if not tag:
                    # No tag for the full phrase: retry with a simpler term.
                    # Only the tag endpoint is retried, never entity search:
                    # a simpler word can match the wrong thing as an entity
                    # ("chai" is a band), while a tag stays food.
                    words = [w for w in (t["label"].split() or []) if len(w) >= 3]
                    for term in ([words[-1], words[0]] if words else []):
                        if term.lower() == t["label"].lower():
                            continue
                        tag = qloo.search_tag(term, "urn:entity:place")
                        if tag:
                            read_as = tag["name"]
                            break
                if tag:
                    tag_signals.append({"label": t["label"], "tagId": tag["id"]})
                    signals.append({**t, "entityId": hit["id"], "placeOnly": True})
                else:
                    # No cuisine tag: keep the entity as a signal instead of dropping the taste.
                    signals.append({**t, "entityId": hit["id"]})
            else:
                signals.append({**t, "entityId": hit["id"]})
            entry = {"label": t["label"], "kind": t["kind"], "status": "matched"}
            if read_as:
                entry["readAs"] = read_as
            status.append(entry)
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


RRF_K = 60
RRF_TAKE = 20
# Fixed floor for the displayed fit number: the fused score of a city that
# is #1 in exactly one list, 1 / (k + 1). Anchoring the bottom here instead
# of at the observed minimum means the number only spreads when the raw
# scores really do, and five near-identical cities all read near 95.
RRF_FLOOR = 1.0 / (RRF_K + 1)


def _city_key(c):
    return f"{c.get('countryCode') or ''}|{(c.get('name') or '').lower()}"


def fuse_city_rankings(per_taste_lists, combined_list):
    """Reciprocal Rank Fusion (Cormack, Clarke, Buettcher 2009):
    score(city) = sum over lists of 1 / (k + rank), k = 60, rank 1-based.
    Only rank positions count, never raw affinities, so no single taste's
    score scale can swamp the others.

    The fused lists are one ranked list per taste, PLUS the combined
    all-tastes list as one more list. The combined list matters because
    some cities only emerge for the full combination: Hong Kong and
    Singapore never top any single-taste list for Taylor Swift + Blade
    Runner + ramen, yet they are the best joint match. Each list is one
    equal voice; a city needs support from more than one list to lead.
    """
    fused = {}
    records = {}
    from_combined = set()
    combined_affinity = {}
    best_taste_affinity = {}
    per_taste = {}
    for label, cities in per_taste_lists:
        for i, c in enumerate(cities):
            key = _city_key(c)
            r = 1.0 / (RRF_K + i + 1)
            fused[key] = fused.get(key, 0.0) + r
            per_taste.setdefault(key, {})[label] = per_taste.get(key, {}).get(label, 0.0) + r
            if key not in records:
                records[key] = c
            aff = c.get("affinity")
            if isinstance(aff, (int, float)):
                best_taste_affinity[key] = max(best_taste_affinity.get(key, 0.0), aff)
    for i, c in enumerate(combined_list or []):
        key = _city_key(c)
        r = 1.0 / (RRF_K + i + 1)
        fused[key] = fused.get(key, 0.0) + r
        # Prefer the combined record: its explainability attributes the
        # city to the tastes jointly, which is what the UI shows.
        records[key] = c
        from_combined.add(key)
        aff = c.get("affinity")
        if isinstance(aff, (int, float)):
            combined_affinity[key] = aff
    ranked = sorted(fused.items(), key=lambda kv: -kv[1])
    out = []
    for key, score in ranked:
        out.append({
            "city": records[key],
            "rrfScore": score,
            "fromCombined": key in from_combined,
            "combinedAffinity": combined_affinity.get(key),
            "bestTasteAffinity": best_taste_affinity.get(key),
            "perTaste": per_taste.get(key, {}),
        })
    return out


def pick_top_diverse(fused, limit=5, max_per_country=2):
    """Pick the top cities, at most `max_per_country` per country, so four
    near-identical results from one country cannot fill the list. The
    remaining slots are filled from lower ranks. If the cap leaves fewer
    than `limit` cities, it is relaxed rather than showing a short list.
    The result is always re-sorted by fused score, so the displayed order
    never disagrees with the displayed percentage."""
    picked = []
    picked_keys = set()
    counts = {}
    for item in fused:
        if len(picked) >= limit:
            break
        key = _city_key(item["city"])
        bucket = item["city"].get("countryCode") or key
        if counts.get(bucket, 0) >= max_per_country:
            continue
        counts[bucket] = counts.get(bucket, 0) + 1
        picked.append(item)
        picked_keys.add(key)
    if len(picked) < limit:
        for item in fused:
            if len(picked) >= limit:
                break
            if _city_key(item["city"]) in picked_keys:
                continue
            picked.append(item)
    picked.sort(key=lambda item: -item["rrfScore"])
    return picked


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

        # One ranked list per taste (a cuisine taste runs on its tag when
        # one was found, otherwise on its place entity), plus the combined
        # all-tastes list. Fused with RRF so no single taste can sweep
        # the top five. A failed list is skipped, not fatal.
        tag_by_label = {t["label"]: t["tagId"] for t in tag_signals}
        jobs = []
        seen_labels = set()
        for s in signals:
            label = s["label"]
            if label in seen_labels:
                continue
            seen_labels.add(label)
            if s.get("placeOnly"):
                if label in tag_by_label:
                    jobs.append((label, "tag", tag_by_label[label]))
                else:
                    jobs.append((label, "entity", s["entityId"]))
            else:
                jobs.append((label, "entity", s["entityId"]))

        per_taste_lists = []
        combined_list = []
        invalid_labels = set()

        def run_job(job):
            label, kind, _id = job
            if kind == "tag":
                return label, qloo.get_destination_insights_single(tag_id=_id, label=label, take=RRF_TAKE)
            return label, qloo.get_destination_insights_single(entity_id=_id, label=label, take=RRF_TAKE)

        def run_combined():
            return qloo.get_destination_insights(
                tastes=clean,
                signal_entity_ids=signal_ids,
                signal_tag_ids=[t["tagId"] for t in tag_signals],
                signal_labels=signal_labels,
                take=RRF_TAKE,
            )

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(run_job, j): j[0] for j in jobs}
            combined_future = pool.submit(run_combined)
            for fut in list(futures):
                try:
                    label, cities = fut.result()
                    if cities:
                        per_taste_lists.append((label, cities))
                except Exception as err:
                    print("qloo per-taste insights failed:", futures[fut], err)
                    # Qloo rejected this taste's entity as a signal, so the
                    # taste contributes nothing: report it as unread rather
                    # than claiming Qloo read it.
                    if "were invalid" in str(err):
                        invalid_labels.add(futures[fut])
            try:
                combined_list = combined_future.result() or []
            except Exception as err:
                print("qloo combined insights failed:", err)
                combined_list = []

        if invalid_labels:
            taste_status = [
                {**s, "status": "unmatched"} if s["label"] in invalid_labels else s
                for s in taste_status
            ]

        if not per_taste_lists and not combined_list:
            return jsonify({"ok": False, "error": "Qloo is not answering right now. Try again in a minute."}), 502

        fused = fuse_city_rankings(per_taste_lists, combined_list)
        if not fused:
            return jsonify({
                "ok": False,
                "error": "Qloo has no city match for these tastes. Try a musician or film with a bigger audience, or a different cuisine.",
            })

        picked = pick_top_diverse(fused, limit=5, max_per_country=2)
        # The displayed number must follow the ranking, so it is the fused
        # RRF score rescaled across the five shown results: best 95%,
        # fifth 70%. The bottom is anchored at the fixed floor (a city #1
        # in exactly one list), not at the observed minimum, so the spread
        # reflects the real gap instead of stretching small differences.
        _scores = [item["rrfScore"] for item in picked]
        _hi = max(_scores) if _scores else 0.0

        def rescaled_fit(item):
            if _hi <= RRF_FLOOR:
                return 82
            value = 70 + 25 * (item["rrfScore"] - RRF_FLOOR) / (_hi - RRF_FLOOR)
            return max(70, min(95, round(value)))

        top = []
        for item in picked:
            c = item["city"]
            fit_score = rescaled_fit(item)
            # Reasons: whichever source names more of the user's tastes,
            # up to three. Qloo's explainability never cites tag tastes
            # (cuisine), while the fused shares cover every taste; the
            # shares win only when they name more tastes.
            expl = [
                {"label": e["label"], "score": e["score"]}
                for e in (c.get("explainability") or [])[:3]
            ] if item["fromCombined"] else []
            total = sum(item["perTaste"].values())
            shares_sorted = sorted(item["perTaste"].items(), key=lambda kv: -kv[1])
            share_reasons = [
                {"label": lab, "score": round(v / total * 100) / 100}
                for lab, v in shares_sorted[:3]
            ] if total else []
            if len({r["label"] for r in share_reasons}) > len({r["label"] for r in expl}):
                reasons = share_reasons
                from_shares = True
            else:
                reasons = expl if expl else share_reasons
                from_shares = False
            # A city matched by exactly one taste is not a blend: show the
            # taste without a percentage (100% would claim all inputs agree)
            # and badge it as a single-taste match.
            single_taste = from_shares and len(share_reasons) == 1
            if single_taste:
                reasons = [{"label": share_reasons[0]["label"], "score": None}]
                confidence = {"label": "Single-taste match", "level": "loose"}
            else:
                confidence = confidence_for(fit_score / 100)
            top.append({
                "name": c["name"],
                "country": c.get("country") or "",
                "countryCode": c.get("countryCode") or "",
                "region": c.get("region") or "",
                "fitScore": fit_score,
                "confidence": confidence,
                "reasons": reasons,
                "tags": (c.get("tags") or [])[:4],
                "qlooEntityId": c.get("entity_id"),
            })

        # The loose-match note still judges Qloo's own affinity underneath,
        # not the rescaled number.
        best_affinity = picked[0]["combinedAffinity"] if picked else None
        if best_affinity is None and picked:
            best_affinity = picked[0]["bestTasteAffinity"]
        if best_affinity is None:
            best_affinity = 0.7
        note = None
        if best_affinity < 0.6:
            note = ("Qloo found only a loose match for these tastes. Treat this ranking as a starting point, "
                    "not a verdict. Try swapping in a musician or film with a wider audience.")

        # One honest line per city, written by Gemini from Qloo's data.
        # A failure here must never break the ranking: no lines, no error.
        if top and llm.is_live():
            try:
                stories = llm.compose_city_lines(top)
                for c in top:
                    if c["name"] in stories:
                        c["story"] = stories[c["name"]]
            except Exception as err:
                print("city lines failed:", err)

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
