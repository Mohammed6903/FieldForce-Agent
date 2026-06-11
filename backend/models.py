"""Pydantic domain models — the shape of every document in Elasticsearch.

These are the contract between the data generator, the tools, and the API.
Geo points are plain {"lat": float, "lon": float} dicts to match ES geo_point.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

GeoPoint = dict  # {"lat": float, "lon": float}

JobStatus = Literal[
    "new", "diagnosing", "assigned", "scheduled", "in_progress", "done", "cancelled"
]
Severity = Literal["low", "medium", "high", "critical"]
TechStatus = Literal["available", "on_job", "off_shift"]


class Technician(BaseModel):
    id: str
    name: str
    skills: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    service_types: list[str] = Field(default_factory=list)
    location: GeoPoint
    status: TechStatus = "available"
    rating: float = 4.5
    shift_start: str = "08:00"
    shift_end: str = "18:00"
    current_job_id: Optional[str] = None
    vehicle_id: Optional[str] = None
    phone: Optional[str] = None
    languages: list[str] = Field(default_factory=lambda: ["en"])


class Job(BaseModel):
    id: str
    customer_name: str
    customer_phone: str
    customer_language: str = "en"
    address: str
    location: GeoPoint
    service_type: str
    description: str
    required_skills: list[str] = Field(default_factory=list)
    required_parts: list[str] = Field(default_factory=list)
    severity: Severity = "medium"
    status: JobStatus = "new"
    created_at: datetime
    sla_deadline: datetime
    scheduled_window: Optional[str] = None
    assigned_technician_id: Optional[str] = None
    notes: Optional[str] = None


class Part(BaseModel):
    id: str
    name: str
    sku: str
    category: str
    quantity_on_hand: int = 0
    reorder_threshold: int = 2
    stock_location: GeoPoint
    compatible_service_types: list[str] = Field(default_factory=list)


class Incident(BaseModel):
    """A historical, resolved job — the corpus for semantic similarity search."""
    id: str
    service_type: str
    title: str
    description: str          # indexed into semantic_text (ELSER) + plain text
    root_cause: str
    parts_used: list[str] = Field(default_factory=list)
    skills_used: list[str] = Field(default_factory=list)
    duration_minutes: int = 60
    resolution_notes: str = ""
    occurred_at: datetime


class ProposedAction(BaseModel):
    """One executable step the crew proposes. The API maps action_type -> the
    matching function in backend.tools.action_tools and calls it with `args`."""
    action_type: Literal["assign_job", "reserve_parts", "notify", "reschedule_job"]
    args: dict
    description: str = ""


class DispatchPlan(BaseModel):
    """The crew's proposed resolution for a job — returned to the human for
    approval BEFORE any write executes. Produced read-only by the agents."""
    job_id: str
    summary: str = ""
    diagnosis: dict = Field(default_factory=dict)
    recommended_technician: Optional[dict] = None
    parts: list[dict] = Field(default_factory=list)
    messages: list[dict] = Field(default_factory=list)
    proposed_actions: list[ProposedAction] = Field(default_factory=list)


class ActionLog(BaseModel):
    """Append-only record of what the agent crew proposed / executed.

    Drives the UI timeline. status moves proposed -> approved -> executed.
    """
    id: str
    timestamp: datetime
    actor: str               # which agent ("coordinator", "resourcing", ...)
    action_type: str         # assign_job | reserve_parts | notify | reschedule_job
    job_id: Optional[str] = None
    summary: str = ""
    payload: dict = Field(default_factory=dict)
    status: Literal["proposed", "approved", "executed"] = "executed"
