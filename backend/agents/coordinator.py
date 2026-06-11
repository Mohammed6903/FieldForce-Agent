"""Coordinator — the root orchestrator agent.

This is the judged centerpiece: a single LlmAgent that owns the dispatch
workflow and delegates to three specialists (diagnostics, resourcing, comms),
each wrapped as an AgentTool. It also has read-only situational awareness via
`sla_risk_summary`, and exactly one terminal tool, `submit_dispatch_plan`,
through which it emits the finished, structured DispatchPlan.

The coordinator NEVER calls a write tool. Every write (assign, reserve,
notify, reschedule) is proposed inside the plan and only executed later, after
a human approves it via the API's execute_plan path.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.tool_context import ToolContext

from ..config import settings
from ..models import DispatchPlan, ProposedAction
from .analyst import operations_analyst_agent
from .comms import comms_agent
from .diagnostics import diagnostics_agent
from .resourcing import resourcing_agent

# Module-level store of the most recent plan per job. The API reads this after
# a run completes (the ToolContext.state is session-scoped and ephemeral).
_PLANS: dict[str, dict] = {}


def submit_dispatch_plan(
    tool_context: ToolContext,
    job_id: str,
    summary: str,
    root_cause: str,
    required_skills: list[str],
    required_parts: list[str],
    est_duration_minutes: int,
    technician_id: str,
    technician_name: str,
    eta_minutes: int,
    parts: list[str],
    customer_message: str,
    technician_message: str,
    customer_phone: str,
    customer_language: str,
) -> dict:
    """Emit the finished, structured dispatch plan for human approval.

    Call this EXACTLY ONCE, at the very end, after diagnostics, resourcing,
    and comms have all reported. It assembles the diagnosis, the recommended
    technician, the parts, the drafted messages, and the ordered list of
    proposed (not-yet-executed) write actions into a DispatchPlan.

    Args:
        tool_context: Injected by ADK; used to stash the plan in session state.
        job_id: The job this plan resolves.
        summary: One-paragraph human summary of the recommended dispatch.
        root_cause: Likely root cause from diagnostics.
        required_skills: Skills the job needs.
        required_parts: Part names the job needs.
        est_duration_minutes: Estimated on-site duration.
        technician_id: Chosen technician's id.
        technician_name: Chosen technician's name.
        eta_minutes: Chosen technician's drive ETA in minutes.
        parts: Part identifiers/names to reserve (ids preferred, names ok).
        customer_message: Customer-facing message in the customer's language.
        technician_message: Technician dispatch brief (English).
        customer_phone: Customer phone (notify recipient).
        customer_language: Customer language code.

    Returns:
        {"ok": True}
    """
    scheduled_window = f"ASAP (~{eta_minutes} min)"

    proposed_actions = [
        ProposedAction(
            action_type="assign_job",
            args={
                "job_id": job_id,
                "technician_id": technician_id,
                "scheduled_window": scheduled_window,
            },
            description=(
                f"Assign job {job_id} to {technician_name} ({technician_id}), "
                f"arriving {scheduled_window}."
            ),
        ),
        ProposedAction(
            action_type="reserve_parts",
            args={"job_id": job_id, "part_ids": list(parts)},
            description=(
                f"Reserve {len(parts)} part(s) for job {job_id}: "
                f"{', '.join(parts) if parts else 'none'}."
            ),
        ),
        ProposedAction(
            action_type="notify",
            args={
                "recipient": customer_phone,
                "channel": "sms",
                "message": customer_message,
                "job_id": job_id,
            },
            description=f"SMS the customer ({customer_language}) about the dispatch.",
        ),
        ProposedAction(
            action_type="notify",
            args={
                "recipient": technician_id,
                "channel": "push",
                "message": technician_message,
                "job_id": job_id,
            },
            description=f"Push the dispatch brief to technician {technician_id}.",
        ),
    ]

    plan = DispatchPlan(
        job_id=job_id,
        summary=summary,
        diagnosis={
            "root_cause": root_cause,
            "required_skills": list(required_skills),
            "required_parts": list(required_parts),
            "est_duration_minutes": est_duration_minutes,
        },
        recommended_technician={
            "id": technician_id,
            "name": technician_name,
            "eta_minutes": eta_minutes,
        },
        parts=[{"ref": p} for p in parts],
        messages=[
            {"audience": "customer", "channel": "sms", "language": customer_language,
             "text": customer_message},
            {"audience": "technician", "channel": "push", "language": "en",
             "text": technician_message},
        ],
        proposed_actions=proposed_actions,
    )

    plan_dict = plan.model_dump()
    tool_context.state["dispatch_plan"] = plan_dict
    _PLANS[job_id] = plan_dict
    return {"ok": True}


_INSTRUCTION = """\
You are the COORDINATOR of a field-service dispatch crew. You own one job at a
time and must produce a single, approved-ready dispatch plan for it.

You have four specialist agents as tools and must orchestrate them IN ORDER.
You must NEVER assign, reserve, notify, or reschedule directly — those are
proposed in the plan and executed later only after a human approves.

Workflow for the job you are given:
1. Call `operations_analyst` to get a LIVE situational briefing (current open
   jobs by severity and how many technicians are available). This analyst reads
   the live Elasticsearch cluster through the Elastic MCP server. Use its
   briefing to gauge how much resource contention this job faces, and reflect it
   in your final summary.
2. Call `diagnostics` with the job's description (and service type / severity).
   Capture: root cause, required skills, required parts, estimated duration.
3. Call `resourcing` with the job's latitude, longitude, service type, the
   required skills, and required parts from step 2. Capture: the chosen
   technician id, name, ETA, the technician's languages, and parts stock
   (each part's name + id + in_stock).
4. Call `comms` with the customer name, customer language, the root cause, the
   chosen technician's name and ETA, and the service type. Capture both the
   customer message (in the customer's language) and the technician message.
5. Call `submit_dispatch_plan` EXACTLY ONCE with everything assembled:
   - For `parts`, pass the part IDs returned by resourcing when available; if
     only names are available, pass the names.
   - `summary` is a one-paragraph plain-English recommendation a dispatcher can
     approve at a glance.

After `submit_dispatch_plan` returns {"ok": true}, stop and give a one-line
confirmation. Do not call any other tool after submitting the plan.
"""

root_agent = LlmAgent(
    name="coordinator",
    model=settings.gemini_model,
    description=(
        "Root dispatch orchestrator. Delegates to diagnostics, resourcing, and "
        "comms specialists, then emits a single structured DispatchPlan for "
        "human approval. Never writes state directly."
    ),
    instruction=_INSTRUCTION,
    tools=[
        AgentTool(agent=operations_analyst_agent),
        AgentTool(agent=diagnostics_agent),
        AgentTool(agent=resourcing_agent),
        AgentTool(agent=comms_agent),
        submit_dispatch_plan,
    ],
)
