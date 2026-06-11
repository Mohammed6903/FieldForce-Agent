"""Write tools — the agent crew's *hands*.

Actions are simulated-but-persisted: each one mutates real Elasticsearch state
and appends to the actions_log index that drives the UI timeline. No external
services are called, so the demo is self-contained and deterministic.

The API layer only lets these run AFTER the human approves the proposed plan,
which is the 'keep the human in control' guarantee.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from ..config import IDX_ACTIONS, IDX_JOBS, IDX_TECHNICIANS
from ..elastic_client import get_client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_action(
    actor: str, action_type: str, job_id: str | None, summary: str, payload: dict
) -> dict:
    doc = {
        "id": f"act_{uuid.uuid4().hex[:10]}",
        "timestamp": _now(),
        "actor": actor,
        "action_type": action_type,
        "job_id": job_id,
        "summary": summary,
        "payload": payload,
        "status": "executed",
    }
    get_client().index(index=IDX_ACTIONS, id=doc["id"], document=doc, refresh=True)
    return doc


def assign_job(
    job_id: str,
    technician_id: str,
    scheduled_window: str,
    actor: str = "coordinator",
) -> dict:
    """Assign a job to a technician and mark both records accordingly.

    Sets the job to status=assigned with the technician + window, and flips the
    technician to status=on_job. Records the action in the timeline.

    Args:
        job_id: Job to assign.
        technician_id: Technician to dispatch.
        scheduled_window: Human-readable window, e.g. "Today 14:30-15:30".
        actor: Which agent performed the action (for the audit trail).

    Returns:
        {"ok": bool, "action": {...}, "error": str | None}
    """
    client = get_client()
    try:
        client.update(
            index=IDX_JOBS,
            id=job_id,
            doc={
                "status": "assigned",
                "assigned_technician_id": technician_id,
                "scheduled_window": scheduled_window,
            },
            refresh=True,
        )
        client.update(
            index=IDX_TECHNICIANS,
            id=technician_id,
            doc={"status": "on_job", "current_job_id": job_id},
            refresh=True,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "action": None}

    action = _log_action(
        actor,
        "assign_job",
        job_id,
        f"Assigned job {job_id} to technician {technician_id} ({scheduled_window}).",
        {"technician_id": technician_id, "scheduled_window": scheduled_window},
    )
    return {"ok": True, "action": action, "error": None}


def reserve_parts(job_id: str, part_ids: list[str], actor: str = "coordinator") -> dict:
    """Reserve parts for a job (decrement on-hand quantity by 1 each).

    Args:
        job_id: Job the parts are for.
        part_ids: Elasticsearch ids of the parts to reserve.
        actor: Which agent performed the action.

    Returns:
        {"ok": bool, "reserved": [part_id], "action": {...}, "error": str | None}
    """
    from ..config import IDX_PARTS

    client = get_client()
    reserved: list[str] = []
    try:
        for pid in part_ids:
            client.update(
                index=IDX_PARTS,
                id=pid,
                script={
                    "source": "if (ctx._source.quantity_on_hand > 0) "
                    "{ ctx._source.quantity_on_hand -= 1 }"
                },
                refresh=True,
            )
            reserved.append(pid)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "reserved": reserved, "action": None}

    action = _log_action(
        actor,
        "reserve_parts",
        job_id,
        f"Reserved {len(reserved)} part(s) for job {job_id}.",
        {"part_ids": reserved},
    )
    return {"ok": True, "reserved": reserved, "action": action, "error": None}


def notify(
    recipient: str,
    channel: str,
    message: str,
    job_id: str | None = None,
    actor: str = "comms",
) -> dict:
    """Send a (simulated) notification to a customer or technician.

    Persists the message to the timeline so it appears in the UI as if sent.

    Args:
        recipient: Name/phone of the recipient.
        channel: "sms" | "email" | "push".
        message: The message body (already drafted, may be multilingual).
        job_id: Related job, if any.
        actor: Which agent performed the action.

    Returns:
        {"ok": True, "action": {...}}
    """
    action = _log_action(
        actor,
        "notify",
        job_id,
        f"{channel.upper()} to {recipient}: {message}",
        {"recipient": recipient, "channel": channel, "message": message},
    )
    return {"ok": True, "action": action}


def reschedule_job(
    job_id: str, new_window: str, reason: str = "", actor: str = "scheduling"
) -> dict:
    """Move a job to a new time window (e.g. to free a tech for an emergency).

    Args:
        job_id: Job to move.
        new_window: New human-readable window.
        reason: Why it was moved (for the audit trail / customer message).
        actor: Which agent performed the action.

    Returns:
        {"ok": bool, "action": {...}, "error": str | None}
    """
    client = get_client()
    try:
        client.update(
            index=IDX_JOBS,
            id=job_id,
            doc={"status": "scheduled", "scheduled_window": new_window},
            refresh=True,
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e), "action": None}

    action = _log_action(
        actor,
        "reschedule_job",
        job_id,
        f"Rescheduled job {job_id} to {new_window}. {reason}".strip(),
        {"new_window": new_window, "reason": reason},
    )
    return {"ok": True, "action": action, "error": None}
