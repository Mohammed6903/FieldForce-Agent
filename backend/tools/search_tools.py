"""Read tools — the agent crew's *senses*.

Each function is exposed to ADK as a tool (ADK builds the JSON schema from the
type hints + docstring, so both matter). They wrap Elasticsearch queries that a
plain LLM cannot do: geo_distance ranking, multi-constraint filtering,
semantic recall, and aggregations.

All functions return plain JSON-serializable dicts/lists.
"""
from __future__ import annotations

from ..config import IDX_INCIDENTS, IDX_JOBS, IDX_PARTS, IDX_TECHNICIANS
from ..elastic_client import get_client

AVG_SPEED_KMH = 32.0  # city driving, used to turn distance into an ETA


def _eta_minutes(distance_km: float) -> int:
    return round(distance_km / AVG_SPEED_KMH * 60)


def find_candidate_technicians(
    latitude: float,
    longitude: float,
    required_skills: list[str] | None = None,
    service_type: str | None = None,
    max_results: int = 3,
) -> dict:
    """Rank the nearest AVAILABLE technicians who have the required skills.

    Uses an Elasticsearch geo_distance sort around the job location, filtered to
    status=available and (if given) matching skills / service_type. Returns each
    candidate with their straight-line distance and an estimated drive ETA.

    Args:
        latitude: Job site latitude.
        longitude: Job site longitude.
        required_skills: Skills the technician must have (all must match).
        service_type: Optional service category the technician must cover.
        max_results: How many candidates to return (default 3).

    Returns:
        {"candidates": [{id, name, distance_km, eta_minutes, skills, rating,
        status, phone, languages}], "count": int}
    """
    filters: list[dict] = [{"term": {"status": "available"}}]
    for skill in required_skills or []:
        filters.append({"term": {"skills": skill}})
    if service_type:
        filters.append({"term": {"service_types": service_type}})

    res = get_client().search(
        index=IDX_TECHNICIANS,
        size=max_results,
        query={"bool": {"filter": filters}},
        sort=[
            {
                "_geo_distance": {
                    "location": {"lat": latitude, "lon": longitude},
                    "order": "asc",
                    "unit": "km",
                }
            }
        ],
    )
    candidates = []
    for hit in res["hits"]["hits"]:
        src = hit["_source"]
        dist = float(hit.get("sort", [0.0])[0])
        candidates.append(
            {
                "id": src.get("id"),
                "name": src.get("name"),
                "distance_km": round(dist, 2),
                "eta_minutes": _eta_minutes(dist),
                "skills": src.get("skills", []),
                "rating": src.get("rating"),
                "status": src.get("status"),
                "phone": src.get("phone"),
                "languages": src.get("languages", ["en"]),
            }
        )
    return {"candidates": candidates, "count": len(candidates)}


def find_similar_incidents(description: str, max_results: int = 3) -> dict:
    """Find the most similar past jobs to build a 'playbook' for a new job.

    Primary path is semantic search over the ELSER-backed semantic_text field;
    if semantic search is unavailable it falls back to full-text matching so the
    tool always returns something usable.

    Args:
        description: Free-text description of the new problem.
        max_results: Number of similar past incidents to return.

    Returns:
        {"incidents": [{id, title, service_type, root_cause, parts_used,
        skills_used, duration_minutes, score}], "count": int, "mode": str}
    """
    client = get_client()
    mode = "semantic"
    try:
        res = client.search(
            index=IDX_INCIDENTS,
            size=max_results,
            query={"semantic": {"field": "description_semantic", "query": description}},
        )
    except Exception:
        mode = "text"
        res = client.search(
            index=IDX_INCIDENTS,
            size=max_results,
            query={
                "multi_match": {
                    "query": description,
                    "fields": ["title^2", "description", "root_cause"],
                }
            },
        )
    incidents = []
    for hit in res["hits"]["hits"]:
        src = hit["_source"]
        incidents.append(
            {
                "id": src.get("id"),
                "title": src.get("title"),
                "service_type": src.get("service_type"),
                "root_cause": src.get("root_cause"),
                "parts_used": src.get("parts_used", []),
                "skills_used": src.get("skills_used", []),
                "duration_minutes": src.get("duration_minutes"),
                "score": round(float(hit.get("_score") or 0.0), 3),
            }
        )
    return {"incidents": incidents, "count": len(incidents), "mode": mode}


def check_parts_availability(
    part_names: list[str], latitude: float, longitude: float
) -> dict:
    """Check stock for required parts, nearest stock location first.

    Args:
        part_names: Part names/keywords to look up.
        latitude: Job site latitude (to sort stock by proximity).
        longitude: Job site longitude.

    Returns:
        {"parts": [{name, in_stock, quantity_on_hand, nearest_stock_km,
        sku, id}], "all_available": bool}
    """
    client = get_client()
    results = []
    all_available = True
    for name in part_names:
        res = client.search(
            index=IDX_PARTS,
            size=1,
            query={
                "bool": {
                    "must": [{"match": {"name": name}}],
                    "filter": [{"range": {"quantity_on_hand": {"gt": 0}}}],
                }
            },
            sort=[
                {
                    "_geo_distance": {
                        "stock_location": {"lat": latitude, "lon": longitude},
                        "order": "asc",
                        "unit": "km",
                    }
                }
            ],
        )
        hits = res["hits"]["hits"]
        if hits:
            src = hits[0]["_source"]
            dist = float(hits[0].get("sort", [0.0])[0])
            results.append(
                {
                    "name": name,
                    "in_stock": True,
                    "quantity_on_hand": src.get("quantity_on_hand"),
                    "nearest_stock_km": round(dist, 2),
                    "sku": src.get("sku"),
                    "id": src.get("id"),
                }
            )
        else:
            all_available = False
            results.append({"name": name, "in_stock": False, "quantity_on_hand": 0})
    return {"parts": results, "all_available": all_available}


def sla_risk_summary() -> dict:
    """Summarize jobs at risk of breaching SLA, grouped by severity.

    Aggregation over open jobs whose sla_deadline is within the next 2 hours.

    Returns:
        {"at_risk_total": int, "by_severity": {severity: count},
        "jobs": [{id, customer_name, severity, sla_deadline, status}]}
    """
    client = get_client()
    res = client.search(
        index=IDX_JOBS,
        size=25,
        query={
            "bool": {
                "must_not": [{"terms": {"status": ["done", "cancelled"]}}],
                "filter": [{"range": {"sla_deadline": {"lte": "now+2h"}}}],
            }
        },
        sort=[{"sla_deadline": "asc"}],
        aggs={"by_severity": {"terms": {"field": "severity"}}},
    )
    by_severity = {
        b["key"]: b["doc_count"]
        for b in res.get("aggregations", {}).get("by_severity", {}).get("buckets", [])
    }
    jobs = [
        {
            "id": h["_source"].get("id"),
            "customer_name": h["_source"].get("customer_name"),
            "severity": h["_source"].get("severity"),
            "sla_deadline": h["_source"].get("sla_deadline"),
            "status": h["_source"].get("status"),
        }
        for h in res["hits"]["hits"]
    ]
    total = res["hits"]["total"]["value"] if isinstance(res["hits"]["total"], dict) else len(jobs)
    return {"at_risk_total": total, "by_severity": by_severity, "jobs": jobs}
