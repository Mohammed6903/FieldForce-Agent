"""Synthetic data generator — seeds the five Elastic indices for the demo.

Produces realistic, demo-ready field-service data for a plumbing/HVAC/electrical
company operating in ``settings.dispatch_city`` around the configured city
center. Everything is deterministic for a given ``--seed`` so the demo is
reproducible (no wall-clock-derived ids, fixed Faker/random seeds).

Run with::

    uv run python -m data.generate --recreate

The generator:
  * (re)creates the indices via ``backend.indices.create_indices``,
  * bulk-indexes technicians, parts, incidents and active jobs,
  * mirrors each incident ``description`` into ``description_semantic`` so the
    ELSER-backed semantic search in ``find_similar_incidents`` works,
  * guarantees the HERO job (Maria Flores, burst water pipe) is present, urgent,
    and that the parts it needs are in stock.

This is "the data step" — kept flat, parameterized, and easy to tune.
"""
from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Iterable

from elasticsearch.helpers import bulk
from faker import Faker

from backend.config import (
    IDX_INCIDENTS,
    IDX_JOBS,
    IDX_PARTS,
    IDX_TECHNICIANS,
    settings,
)
from backend.elastic_client import get_client
from backend.indices import create_indices

# ── Domain taxonomy ──────────────────────────────────────────────────────────
# Skills grouped by trade. A technician draws 2-4 skills from exactly ONE trade.
TRADE_SKILLS: dict[str, list[str]] = {
    "plumbing": ["leak_detection", "pipe_repair", "drain_cleaning", "water_heater"],
    "hvac": ["ac_repair", "furnace_repair", "thermostat", "refrigerant"],
    "electrical": ["wiring", "panel_upgrade", "lighting", "ev_charger"],
}
TRADES: list[str] = list(TRADE_SKILLS)

# Certifications offered per trade (technicians get 1-2).
TRADE_CERTS: dict[str, list[str]] = {
    "plumbing": ["Journeyman Plumber", "Backflow Certified", "Master Plumber"],
    "hvac": ["EPA 608 Universal", "NATE Certified", "HVAC Excellence"],
    "electrical": ["Journeyman Electrician", "Master Electrician", "OSHA 30"],
}

# Parts catalog: (name, trade). quantity / sku / location filled in at gen time.
PARTS_CATALOG: list[tuple[str, str]] = [
    ("SharkBite fitting", "plumbing"),
    ("pipe cutter", "plumbing"),
    ("PEX pipe", "plumbing"),
    ("compression coupling", "plumbing"),
    ("water heater element", "plumbing"),
    ("drain snake", "plumbing"),
    ("capacitor", "hvac"),
    ("contactor", "hvac"),
    ("thermostat", "hvac"),
    ("refrigerant R-410A", "hvac"),
    ("breaker 20A", "electrical"),
    ("romex wire", "electrical"),
    ("GFCI outlet", "electrical"),
    ("EV charger unit", "electrical"),
]

LANGUAGES_EXTRA = ["es", "zh", "fr"]

# Incident "templates" per trade: (title, description, root_cause, skills, parts).
# The descriptions are deliberately vivid so semantic recall has signal to match.
INCIDENT_TEMPLATES: dict[str, list[dict]] = {
    "plumbing": [
        {
            "title": "Burst pipe flooding kitchen",
            "description": "Customer reported water gushing from under the kitchen sink, "
            "soaking the cabinet and floor. A copper supply line had burst at a corroded joint.",
            "root_cause": "Corroded copper joint failed under pressure",
            "skills": ["leak_detection", "pipe_repair"],
            "parts": ["SharkBite fitting", "pipe cutter"],
        },
        {
            "title": "Slow draining bathroom sink",
            "description": "Bathroom sink draining very slowly and backing up. Heavy hair and "
            "soap-scum clog found several feet down the trap arm.",
            "root_cause": "Hair and soap-scum buildup in trap arm",
            "skills": ["drain_cleaning"],
            "parts": ["drain snake"],
        },
        {
            "title": "No hot water from water heater",
            "description": "Tenant has no hot water anywhere in the unit. Lower heating element "
            "in the electric water heater had failed open.",
            "root_cause": "Failed lower heating element",
            "skills": ["water_heater"],
            "parts": ["water heater element"],
        },
        {
            "title": "Dripping leak under bathroom vanity",
            "description": "Slow persistent drip detected under the bathroom vanity, water pooling "
            "overnight. Loose compression fitting on the cold supply.",
            "root_cause": "Loose compression fitting on supply line",
            "skills": ["leak_detection", "pipe_repair"],
            "parts": ["compression coupling"],
        },
        {
            "title": "Repipe of failing galvanized line",
            "description": "Repeated pinhole leaks in old galvanized branch line. Replaced the run "
            "with PEX to stop recurring leaks.",
            "root_cause": "End-of-life galvanized piping",
            "skills": ["pipe_repair", "leak_detection"],
            "parts": ["PEX pipe", "SharkBite fitting"],
        },
    ],
    "hvac": [
        {
            "title": "AC not cooling on hot day",
            "description": "Air conditioner running but blowing warm air during a heat wave. "
            "Start capacitor on the condenser had bulged and failed.",
            "root_cause": "Failed condenser start capacitor",
            "skills": ["ac_repair"],
            "parts": ["capacitor"],
        },
        {
            "title": "Furnace will not ignite",
            "description": "Furnace blower runs but no heat; burners never light on a cold morning. "
            "Worn contactor was not pulling in the ignition sequence.",
            "root_cause": "Worn contactor failing to engage",
            "skills": ["furnace_repair"],
            "parts": ["contactor"],
        },
        {
            "title": "Thermostat reading wrong temperature",
            "description": "Smart thermostat showing temperature far off from the room and short "
            "cycling the system. Faulty thermostat replaced and recalibrated.",
            "root_cause": "Defective thermostat sensor",
            "skills": ["thermostat"],
            "parts": ["thermostat"],
        },
        {
            "title": "Low refrigerant causing weak cooling",
            "description": "AC cooling capacity dropped over the season with ice forming on the "
            "line set. System was low on refrigerant from a slow leak; recharged after repair.",
            "root_cause": "Refrigerant undercharge from slow leak",
            "skills": ["refrigerant", "ac_repair"],
            "parts": ["refrigerant R-410A"],
        },
    ],
    "electrical": [
        {
            "title": "Tripping breaker on kitchen circuit",
            "description": "Kitchen circuit repeatedly tripping under load. An overloaded, weak 20A "
            "breaker was failing thermally and was replaced.",
            "root_cause": "Weak/overloaded breaker tripping under load",
            "skills": ["panel_upgrade", "wiring"],
            "parts": ["breaker 20A"],
        },
        {
            "title": "Dead outlets in living room",
            "description": "Several living-room outlets went dead at once. A loose back-stab wire "
            "connection had overheated and broken the run.",
            "root_cause": "Loose back-stab connection overheated",
            "skills": ["wiring"],
            "parts": ["romex wire"],
        },
        {
            "title": "GFCI outlet not resetting in bathroom",
            "description": "Bathroom GFCI outlet would not reset and lost power to the vanity. "
            "Old GFCI device had failed internally and was replaced.",
            "root_cause": "Failed GFCI device",
            "skills": ["wiring", "lighting"],
            "parts": ["GFCI outlet"],
        },
        {
            "title": "EV charger install and circuit",
            "description": "Homeowner requested a Level 2 EV charger in the garage. Installed a "
            "dedicated 240V circuit and mounted the charger unit.",
            "root_cause": "New dedicated circuit required for EV charging",
            "skills": ["ev_charger", "panel_upgrade"],
            "parts": ["EV charger unit"],
        },
    ],
}

# Active (status="new") job templates, by trade. The HERO job is injected
# separately and always placed first.
JOB_TEMPLATES: list[dict] = [
    {
        "service_type": "plumbing",
        "description": "Water heater leaking from the base in the garage, small puddle spreading.",
        "required_skills": ["water_heater"],
        "required_parts": ["water heater element"],
        "severity": "high",
    },
    {
        "service_type": "plumbing",
        "description": "Kitchen sink completely clogged, water not draining at all.",
        "required_skills": ["drain_cleaning"],
        "required_parts": ["drain snake"],
        "severity": "medium",
    },
    {
        "service_type": "hvac",
        "description": "AC blowing warm air, house getting hot during the afternoon.",
        "required_skills": ["ac_repair"],
        "required_parts": ["capacitor"],
        "severity": "high",
    },
    {
        "service_type": "hvac",
        "description": "Thermostat screen blank and the system will not turn on.",
        "required_skills": ["thermostat"],
        "required_parts": ["thermostat"],
        "severity": "medium",
    },
    {
        "service_type": "electrical",
        "description": "Breaker keeps tripping whenever the microwave runs.",
        "required_skills": ["panel_upgrade"],
        "required_parts": ["breaker 20A"],
        "severity": "medium",
    },
    {
        "service_type": "electrical",
        "description": "Half the outlets in the office are dead, possible bad connection.",
        "required_skills": ["wiring"],
        "required_parts": ["romex wire"],
        "severity": "low",
    },
    {
        "service_type": "hvac",
        "description": "Furnace not igniting, no heat overnight and temperature dropping.",
        "required_skills": ["furnace_repair"],
        "required_parts": ["contactor"],
        "severity": "high",
    },
]

# SLA window per severity (added to created_at to get the deadline).
SLA_BY_SEVERITY: dict[str, timedelta] = {
    "critical": timedelta(minutes=30),
    "high": timedelta(hours=2),
    "medium": timedelta(hours=4),
    "low": timedelta(hours=8),
}

EARTH_RADIUS_KM = 6371.0


# ── Geo helpers ──────────────────────────────────────────────────────────────
def _random_point_near(
    fake: Faker, lat: float, lon: float, max_km: float
) -> dict[str, float]:
    """Return a {lat, lon} point uniformly within ``max_km`` of (lat, lon)."""
    # sqrt for uniform area distribution; bearing uniform over the circle.
    radius_km = max_km * math.sqrt(fake.random.random())
    bearing = fake.random.random() * 2 * math.pi
    d = radius_km / EARTH_RADIUS_KM
    lat1, lon1 = math.radians(lat), math.radians(lon)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2),
    )
    return {"lat": round(math.degrees(lat2), 6), "lon": round(math.degrees(lon2), 6)}


def _iso(dt: datetime) -> str:
    """UTC ISO-8601 string, what Elasticsearch date fields expect."""
    return dt.astimezone(timezone.utc).isoformat()


# ── Generators ───────────────────────────────────────────────────────────────
def generate_technicians(fake: Faker, now: datetime, count: int = 16) -> list[dict]:
    """Build ~``count`` technicians spread across the three trades."""
    techs: list[dict] = []
    for i in range(count):
        trade = TRADES[i % len(TRADES)]
        skills = fake.random_sample(
            TRADE_SKILLS[trade], length=fake.random_int(2, len(TRADE_SKILLS[trade]))
        )
        certs = fake.random_sample(
            TRADE_CERTS[trade], length=fake.random_int(1, 2)
        )
        # Mostly available; a few busy / off shift to make ranking interesting.
        roll = fake.random.random()
        status = "available" if roll < 0.72 else ("on_job" if roll < 0.88 else "off_shift")
        langs = ["en"]
        if fake.random.random() < 0.45:
            langs.append(fake.random_element(LANGUAGES_EXTRA))
        techs.append(
            {
                "id": f"tech-{i + 1:03d}",
                "name": fake.name(),
                "skills": skills,
                "certifications": certs,
                "service_types": [trade],
                "location": _random_point_near(
                    fake, settings.dispatch_city_lat, settings.dispatch_city_lon, 12.0
                ),
                "status": status,
                "rating": round(fake.random.uniform(3.8, 5.0), 1),
                "shift_start": fake.random_element(["07:00", "08:00", "09:00"]),
                "shift_end": fake.random_element(["17:00", "18:00", "19:00"]),
                "current_job_id": None,
                "vehicle_id": f"veh-{i + 1:03d}",
                "phone": fake.numerify("+1-415-###-####"),
                "languages": langs,
            }
        )
    return techs


def generate_parts(fake: Faker) -> list[dict]:
    """Build the parts inventory; a couple are forced out-of-stock for the demo."""
    # 2-3 warehouse stock points near the city center.
    warehouses = [
        _random_point_near(
            fake, settings.dispatch_city_lat, settings.dispatch_city_lon, 4.0
        )
        for _ in range(3)
    ]
    parts: list[dict] = []
    # Names that should always be in stock so the HERO job stays resolvable.
    must_stock = {"SharkBite fitting", "pipe cutter"}
    # Indices that should show as out-of-stock to demo the parts gap.
    out_of_stock_idx = {3, 9}  # compression coupling, refrigerant R-410A
    for i, (name, trade) in enumerate(PARTS_CATALOG):
        if name in must_stock:
            qty = fake.random_int(4, 12)
        elif i in out_of_stock_idx:
            qty = 0
        else:
            qty = fake.random_int(0, 12)
        parts.append(
            {
                "id": f"part-{i + 1:03d}",
                "name": name,
                "sku": fake.bothify("??-#####").upper(),
                "category": trade,
                "quantity_on_hand": qty,
                "reorder_threshold": 2,
                "stock_location": fake.random_element(warehouses),
                "compatible_service_types": [trade],
            }
        )
    return parts


def generate_incidents(fake: Faker, now: datetime, count: int = 45) -> list[dict]:
    """Build ~``count`` resolved past jobs across all trades.

    The free-text ``description`` is mirrored into ``description_semantic`` so the
    ELSER semantic_text field is populated for semantic recall.
    """
    flat_templates: list[tuple[str, dict]] = [
        (trade, tpl) for trade, tpls in INCIDENT_TEMPLATES.items() for tpl in tpls
    ]
    incidents: list[dict] = []
    for i in range(count):
        trade, tpl = fake.random_element(flat_templates)
        occurred = now - timedelta(
            days=fake.random_int(1, 730), minutes=fake.random_int(0, 1440)
        )
        # Light variation so duplicates of a template still read distinctly.
        prefix = fake.random_element(
            ["", "Repeat call: ", "After-hours: ", "Follow-up visit: ", "Emergency: "]
        )
        description = f"{prefix}{tpl['description']}"
        incidents.append(
            {
                "id": f"inc-{i + 1:04d}",
                "service_type": trade,
                "title": tpl["title"],
                "description": description,
                "description_semantic": description,
                "root_cause": tpl["root_cause"],
                "parts_used": tpl["parts"],
                "skills_used": tpl["skills"],
                "duration_minutes": fake.random_int(30, 180),
                "resolution_notes": f"Resolved on site. {tpl['root_cause']}. "
                f"Customer confirmed issue cleared.",
                "occurred_at": _iso(occurred),
            }
        )
    return incidents


def _hero_job(fake: Faker, now: datetime) -> dict:
    """The demo centerpiece: Maria Flores' burst water pipe (critical, Spanish)."""
    created = now - timedelta(minutes=5)
    return {
        "id": "job-001",
        "customer_name": "Maria Flores",
        "customer_phone": fake.numerify("+1-415-###-####"),
        "customer_language": "es",
        "address": fake.street_address() + f", {settings.dispatch_city}",
        "location": _random_point_near(
            fake, settings.dispatch_city_lat, settings.dispatch_city_lon, 6.0
        ),
        "service_type": "plumbing",
        "description": "Burst water pipe under the kitchen sink — water is spraying everywhere "
        "and flooding the kitchen floor fast. Customer has shut off nothing and is panicking. "
        "Needs immediate emergency response before water damage spreads to the unit below.",
        "required_skills": ["leak_detection", "pipe_repair"],
        "required_parts": ["SharkBite fitting", "pipe cutter"],
        "severity": "critical",
        "status": "new",
        "created_at": _iso(created),
        # 45 minutes out from *now* so it reads as the most urgent open job.
        "sla_deadline": _iso(now + timedelta(minutes=45)),
        "scheduled_window": None,
        "assigned_technician_id": None,
        "notes": "Customer prefers Spanish. Unit above a neighbor — escalate.",
    }


def generate_jobs(fake: Faker, now: datetime) -> list[dict]:
    """Build the active job queue. job-001 is always the HERO job, placed first."""
    jobs: list[dict] = [_hero_job(fake, now)]
    for i, tpl in enumerate(JOB_TEMPLATES):
        # Created somewhere in the last few hours; SLA derives from severity.
        created = now - timedelta(minutes=fake.random_int(10, 240))
        sla = created + SLA_BY_SEVERITY[tpl["severity"]]
        langs = ["en", "es", "zh", "fr"]
        cust_lang = fake.random_element(langs) if fake.random.random() < 0.3 else "en"
        jobs.append(
            {
                "id": f"job-{i + 2:03d}",
                "customer_name": fake.name(),
                "customer_phone": fake.numerify("+1-415-###-####"),
                "customer_language": cust_lang,
                "address": fake.street_address() + f", {settings.dispatch_city}",
                "location": _random_point_near(
                    fake, settings.dispatch_city_lat, settings.dispatch_city_lon, 10.0
                ),
                "service_type": tpl["service_type"],
                "description": tpl["description"],
                "required_skills": tpl["required_skills"],
                "required_parts": tpl["required_parts"],
                "severity": tpl["severity"],
                "status": "new",
                "created_at": _iso(created),
                "sla_deadline": _iso(sla),
                "scheduled_window": None,
                "assigned_technician_id": None,
                "notes": None,
            }
        )
    return jobs


# ── Indexing ─────────────────────────────────────────────────────────────────
def _actions(index: str, docs: Iterable[dict]) -> Iterable[dict]:
    """Yield bulk actions with doc _id set to the record id."""
    for doc in docs:
        yield {"_index": index, "_id": doc["id"], "_source": doc}


def seed(client, recreate: bool, seed_value: int) -> dict[str, int]:
    """Generate and bulk-index all five indices. Returns a per-index count."""
    fake = Faker()
    Faker.seed(seed_value)
    random.seed(seed_value)

    # A fixed "now" keeps the run reproducible (ids/dates don't drift per call).
    now = datetime(2026, 6, 11, 12, 0, 0, tzinfo=timezone.utc)

    create_indices(client, recreate=recreate)

    techs = generate_technicians(fake, now)
    parts = generate_parts(fake)
    incidents = generate_incidents(fake, now)
    jobs = generate_jobs(fake, now)

    payload = {
        IDX_TECHNICIANS: techs,
        IDX_PARTS: parts,
        IDX_INCIDENTS: incidents,
        IDX_JOBS: jobs,
    }
    counts: dict[str, int] = {}
    for index, docs in payload.items():
        bulk(client, _actions(index, docs), refresh=True)
        counts[index] = len(docs)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the FieldForce Elastic indices.")
    parser.add_argument(
        "--recreate",
        action="store_true",
        default=True,
        help="Delete and rebuild the indices before seeding (default: True).",
    )
    parser.add_argument(
        "--no-recreate",
        dest="recreate",
        action="store_false",
        help="Seed into existing indices without dropping them.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seed for Faker and random for reproducible data (default: 42).",
    )
    args = parser.parse_args()

    client = get_client()
    counts = seed(client, recreate=args.recreate, seed_value=args.seed)

    print(f"Seeded FieldForce data for {settings.dispatch_city} (seed={args.seed}):")
    for index, n in counts.items():
        print(f"  {index:16s} {n:4d} docs")
    print("  (HERO job = job-001, Maria Flores, critical burst pipe)")


if __name__ == "__main__":
    main()
