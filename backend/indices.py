"""Elasticsearch index definitions + idempotent creation.

The interesting bits — the reason Elastic is the agent's superpower:
  * technicians.location / jobs.location / parts.stock_location -> geo_point
    (powers geo_distance technician ranking)
  * incidents.description_semantic -> semantic_text (ELSER) for semantic recall
    of similar past jobs; incidents.description kept as text for fallback.
"""
from __future__ import annotations

from elasticsearch import Elasticsearch

from .config import (
    IDX_ACTIONS,
    IDX_INCIDENTS,
    IDX_JOBS,
    IDX_PARTS,
    IDX_TECHNICIANS,
    settings,
)

KEYWORD = {"type": "keyword"}
TEXT = {"type": "text"}
GEO = {"type": "geo_point"}
DATE = {"type": "date"}


def _mappings() -> dict[str, dict]:
    return {
        IDX_TECHNICIANS: {
            "properties": {
                "id": KEYWORD,
                "name": {"type": "text", "fields": {"raw": KEYWORD}},
                "skills": KEYWORD,
                "certifications": KEYWORD,
                "service_types": KEYWORD,
                "location": GEO,
                "status": KEYWORD,
                "rating": {"type": "float"},
                "shift_start": KEYWORD,
                "shift_end": KEYWORD,
                "current_job_id": KEYWORD,
                "vehicle_id": KEYWORD,
                "phone": KEYWORD,
                "languages": KEYWORD,
            }
        },
        IDX_JOBS: {
            "properties": {
                "id": KEYWORD,
                "customer_name": {"type": "text", "fields": {"raw": KEYWORD}},
                "customer_phone": KEYWORD,
                "customer_language": KEYWORD,
                "address": TEXT,
                "location": GEO,
                "service_type": KEYWORD,
                "description": TEXT,
                "required_skills": KEYWORD,
                "required_parts": KEYWORD,
                "severity": KEYWORD,
                "status": KEYWORD,
                "created_at": DATE,
                "sla_deadline": DATE,
                "scheduled_window": KEYWORD,
                "assigned_technician_id": KEYWORD,
                "notes": TEXT,
            }
        },
        IDX_PARTS: {
            "properties": {
                "id": KEYWORD,
                "name": {"type": "text", "fields": {"raw": KEYWORD}},
                "sku": KEYWORD,
                "category": KEYWORD,
                "quantity_on_hand": {"type": "integer"},
                "reorder_threshold": {"type": "integer"},
                "stock_location": GEO,
                "compatible_service_types": KEYWORD,
            }
        },
        IDX_INCIDENTS: {
            "properties": {
                "id": KEYWORD,
                "service_type": KEYWORD,
                "title": {"type": "text", "fields": {"raw": KEYWORD}},
                "description": TEXT,
                "description_semantic": {
                    "type": "semantic_text",
                    "inference_id": settings.semantic_inference_id,
                },
                "root_cause": TEXT,
                "parts_used": KEYWORD,
                "skills_used": KEYWORD,
                "duration_minutes": {"type": "integer"},
                "resolution_notes": TEXT,
                "occurred_at": DATE,
            }
        },
        IDX_ACTIONS: {
            "properties": {
                "id": KEYWORD,
                "timestamp": DATE,
                "actor": KEYWORD,
                "action_type": KEYWORD,
                "job_id": KEYWORD,
                "summary": TEXT,
                "payload": {"type": "object", "enabled": False},
                "status": KEYWORD,
            }
        },
    }


def create_indices(client: Elasticsearch, recreate: bool = False) -> list[str]:
    """Create all indices if absent. If recreate=True, delete + rebuild.

    Returns the list of indices that were (re)created.
    """
    created: list[str] = []
    for name, mapping in _mappings().items():
        exists = client.indices.exists(index=name)
        if exists and recreate:
            client.indices.delete(index=name)
            exists = False
        if not exists:
            client.indices.create(index=name, mappings=mapping)
            created.append(name)
    return created


if __name__ == "__main__":
    from .elastic_client import get_client

    made = create_indices(get_client(), recreate=False)
    print("Created indices:", made or "(all already existed)")
