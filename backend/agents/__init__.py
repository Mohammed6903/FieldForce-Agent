"""The ADK agent crew package — runtime contract for the API layer.

Exposes:
  root_agent          — the coordinator LlmAgent (the crew's entry point).
  run_dispatch(job_id)        — async: run the crew on a job, return the plan dict.
  run_dispatch_sync(job_id)   — sync wrapper for sync FastAPI handlers.
  execute_plan(plan)          — execute a (human-approved) plan's proposed actions.
"""
from __future__ import annotations

import asyncio

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from ..config import IDX_JOBS
from ..elastic_client import get_client
from ..tools import action_tools
from .coordinator import _PLANS, root_agent

__all__ = [
    "root_agent",
    "run_dispatch",
    "run_dispatch_sync",
    "execute_plan",
]

_APP_NAME = "fieldforce"

# Maps a ProposedAction.action_type to the write tool that fulfills it.
_ACTION_DISPATCH = {
    "assign_job": action_tools.assign_job,
    "reserve_parts": action_tools.reserve_parts,
    "notify": action_tools.notify,
    "reschedule_job": action_tools.reschedule_job,
}


def _build_prompt(job: dict) -> str:
    """Render a job document into the user message for the coordinator."""
    loc = job.get("location") or {}
    lat = loc.get("lat", "")
    lon = loc.get("lon", "")
    return (
        "Plan the dispatch for this field-service job. Run diagnostics, then "
        "resourcing, then comms, then submit_dispatch_plan exactly once.\n\n"
        f"job_id: {job.get('id')}\n"
        f"customer_name: {job.get('customer_name')}\n"
        f"customer_phone: {job.get('customer_phone')}\n"
        f"customer_language: {job.get('customer_language', 'en')}\n"
        f"address: {job.get('address')}\n"
        f"latitude: {lat}\n"
        f"longitude: {lon}\n"
        f"service_type: {job.get('service_type')}\n"
        f"severity: {job.get('severity')}\n"
        f"required_skills: {job.get('required_skills', [])}\n"
        f"description: {job.get('description')}\n"
    )


async def run_dispatch(job_id: str) -> dict:
    """Run the agent crew on a single job and return the proposed DispatchPlan.

    Fetches the job from Elasticsearch, runs the coordinator (which delegates to
    the specialists and calls submit_dispatch_plan), then returns the resulting
    plan dict. No state is written to the job/technician indices — the plan only
    *proposes* actions.

    Args:
        job_id: The id of the job document in the jobs index.

    Returns:
        The DispatchPlan as a dict. If the crew never produced one, a minimal
        error-shaped dict {"job_id", "summary", "proposed_actions": []}.
    """
    job = get_client().get(index=IDX_JOBS, id=job_id)["_source"]

    session_service = InMemorySessionService()
    runner = Runner(
        agent=root_agent,
        app_name=_APP_NAME,
        session_service=session_service,
    )
    user_id = "dispatcher"
    session = await session_service.create_session(
        app_name=_APP_NAME, user_id=user_id, session_id=f"dispatch_{job_id}"
    )

    content = types.Content(
        role="user", parts=[types.Part(text=_build_prompt(job))]
    )

    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session.id, new_message=content
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final_text = "".join(
                p.text for p in event.content.parts if getattr(p, "text", None)
            )

    plan = _PLANS.get(job_id)
    if plan is not None:
        return plan
    return {
        "job_id": job_id,
        "summary": final_text or "No dispatch plan was produced.",
        "proposed_actions": [],
    }


def run_dispatch_sync(job_id: str) -> dict:
    """Synchronous wrapper around `run_dispatch` for sync FastAPI handlers.

    Uses asyncio.run when no loop is running; if a loop is already running in
    this thread, executes the coroutine on a dedicated background loop thread.

    Args:
        job_id: The id of the job document in the jobs index.

    Returns:
        The DispatchPlan dict (see run_dispatch).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread — the simple, common case.
        return asyncio.run(run_dispatch(job_id))

    # A loop is already running (e.g. called from within async context). Run the
    # coroutine to completion on a separate thread with its own event loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(run_dispatch(job_id)))
        return future.result()


def execute_plan(plan: dict) -> dict:
    """Execute the proposed actions of a (human-approved) dispatch plan.

    Maps each proposed action's action_type to the matching write tool in
    backend.tools.action_tools and calls it with the action's args. Collects
    the returned action records (the audit-trail entries) for the UI timeline.

    Args:
        plan: A DispatchPlan dict (as returned by run_dispatch).

    Returns:
        {"executed": [action_record, ...], "count": int}
    """
    executed: list[dict] = []
    for action in plan.get("proposed_actions", []):
        action_type = action.get("action_type")
        args = dict(action.get("args", {}))
        fn = _ACTION_DISPATCH.get(action_type)
        if fn is None:
            executed.append(
                {"action_type": action_type, "ok": False,
                 "error": f"unknown action_type: {action_type}"}
            )
            continue
        result = fn(**args)
        record = result.get("action") if isinstance(result, dict) else None
        if record is not None:
            executed.append(record)
        else:
            executed.append(
                {"action_type": action_type, "ok": result.get("ok", False),
                 "error": result.get("error")}
            )
    return {"executed": executed, "count": len(executed)}
