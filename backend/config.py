"""Central configuration, loaded from environment / .env.

Every module imports `settings` from here so there is a single source of truth
for credentials and demo parameters. Nothing else reads os.environ directly.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Populate os.environ from .env so the google-genai SDK (used by ADK) — which
# reads os.environ directly, not our Settings object — can see the credentials.
load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Elastic ──
    elastic_endpoint: str = "http://localhost:9200"
    elastic_api_key: str = ""
    semantic_inference_id: str = ".elser-2-elasticsearch"

    # ── Gemini / Vertex ──
    google_genai_use_vertexai: bool = True
    google_cloud_project: str = ""
    google_cloud_location: str = "us-central1"
    gemini_model: str = "gemini-3-pro-preview"
    google_api_key: str = ""

    # ── Demo geography ──
    dispatch_city: str = "San Francisco"
    dispatch_city_lat: float = 37.7749
    dispatch_city_lon: float = -122.4194

    # ── App ──
    app_port: int = 8080


settings = Settings()


def _configure_genai_env() -> None:
    """Deterministically point the google-genai SDK at one auth path.

    Vertex requires a project; if one is set we use Vertex (the hackathon /
    Agent Engine path). Otherwise, if an API key is present, we fall back to the
    Gemini Developer API (AI Studio). This avoids the ambiguous state where
    USE_VERTEXAI is true but no project is configured.
    """
    use_vertex = settings.google_genai_use_vertexai and bool(settings.google_cloud_project)
    if use_vertex:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "TRUE"
        os.environ["GOOGLE_CLOUD_PROJECT"] = settings.google_cloud_project
        os.environ["GOOGLE_CLOUD_LOCATION"] = settings.google_cloud_location
    elif settings.google_api_key:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "FALSE"
        os.environ["GOOGLE_API_KEY"] = settings.google_api_key


_configure_genai_env()

# Index names — referenced everywhere, kept here so they never drift.
IDX_TECHNICIANS = "technicians"
IDX_JOBS = "jobs"
IDX_PARTS = "parts_inventory"
IDX_INCIDENTS = "incidents"
IDX_ACTIONS = "actions_log"
