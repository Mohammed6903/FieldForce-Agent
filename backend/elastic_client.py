"""Single Elasticsearch client factory.

Cached so every tool/route shares one connection. Reads creds from `settings`.
"""
from __future__ import annotations

from functools import lru_cache

from elasticsearch import Elasticsearch

from .config import settings


@lru_cache(maxsize=1)
def get_client() -> Elasticsearch:
    if settings.elastic_api_key:
        return Elasticsearch(
            settings.elastic_endpoint,
            api_key=settings.elastic_api_key,
            request_timeout=30,
        )
    # Local / no-auth fallback (dev only)
    return Elasticsearch(settings.elastic_endpoint, request_timeout=30)


def ping() -> bool:
    try:
        return bool(get_client().ping())
    except Exception:
        return False
