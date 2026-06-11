"""FastAPI backend for the FieldForce dispatch agent.

Exposes a small JSON API over the Elasticsearch-backed data and the ADK agent
crew: read endpoints for technicians / jobs / actions / SLA, plus the dispatch
loop (propose a plan, then approve it for execution). Serves the static
frontend from the ``frontend/`` directory.

Run with ``uv run python -m backend.main`` or ``uvicorn backend.main:app``.
"""
from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.agents import execute_plan, run_dispatch, root_agent  # noqa: F401
from backend.config import (
    IDX_ACTIONS,
    IDX_JOBS,
    IDX_TECHNICIANS,
    settings,
)
from backend.elastic_client import get_client, ping
from backend.tools.search_tools import sla_risk_summary

app = FastAPI(title="FieldForce Dispatch Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _safe_search(index: str, **kwargs) -> list[dict]:
    """Run an ES search returning the ``_source`` of each hit.

    Wraps the query so a missing index (the data step may not have run yet) or
    any connection error yields an empty list instead of a 500.

    Args:
        index: Index to search.
        **kwargs: Passed through to ``Elasticsearch.search``.

    Returns:
        List of document sources (possibly empty).
    """
    try:
        res = get_client().search(index=index, **kwargs)
        return [hit["_source"] for hit in res["hits"]["hits"]]
    except Exception:
        return []


@app.get("/api/health")
def health() -> dict:
    """Liveness probe: Elastic reachability and the configured model."""
    return {"elastic": ping(), "model": settings.gemini_model}


@app.get("/api/technicians")
def list_technicians() -> list[dict]:
    """All technicians."""
    return _safe_search(IDX_TECHNICIANS, size=200, query={"match_all": {}})


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    """All jobs, sorted by severity then creation time."""
    return _safe_search(
        IDX_JOBS,
        size=200,
        query={"match_all": {}},
        sort=[{"severity": "desc"}, {"created_at": "desc"}],
    )


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    """A single job by id."""
    hits = _safe_search(IDX_JOBS, size=1, query={"term": {"id": job_id}})
    if not hits:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    return hits[0]


@app.get("/api/actions")
def list_actions() -> list[dict]:
    """The 50 most recent entries in the actions log."""
    return _safe_search(
        IDX_ACTIONS,
        size=50,
        query={"match_all": {}},
        sort=[{"timestamp": "desc"}],
    )


@app.get("/api/sla")
def sla() -> dict:
    """Jobs at risk of breaching SLA, grouped by severity."""
    try:
        return sla_risk_summary()
    except Exception:
        return {"at_risk_total": 0, "by_severity": {}, "jobs": []}


@app.post("/api/dispatch/{job_id}")
async def dispatch(job_id: str) -> dict:
    """Run the agent crew read-only to propose a dispatch plan for a job."""
    return await run_dispatch(job_id)


@app.post("/api/approve")
def approve(plan: dict) -> dict:
    """Execute the proposed actions in an approved dispatch plan."""
    return execute_plan(plan)


# ── Static frontend ──
# Mounted last so the /api routes always take precedence. Guarded in case the
# frontend has not been built yet.
_FRONTEND_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_FRONTEND_DIR):
    app.mount(
        "/", StaticFiles(directory=_FRONTEND_DIR, html=True), name="static"
    )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.app_port)
