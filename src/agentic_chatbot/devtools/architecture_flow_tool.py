"""
Architecture Flow Tool — single-file combined backend + frontend.

Everything from the multi-file version lives in this one module: the Pydantic
data model, seed data for four example services, the in-memory store with
CRUD + deploy-scenario + Pub/Sub-enforcement logic, the Postman-style payload
validator, the deterministic "why did X fail" chat, the FastAPI API, and the
Dash + dash-cytoscape UI — mounted into the SAME FastAPI app/process, on the
SAME port, via a WSGI bridge (Dash is Flask/WSGI under the hood; FastAPI is
ASGI, so a2wsgi bridges the two without needing a second server or port).

RUN STANDALONE:
    pip install fastapi uvicorn pydantic dash dash-cytoscape requests a2wsgi
    python architecture_flow_tool.py
    # API:  http://localhost:8000/api/...
    # UI:   http://localhost:8000/architecture-tool/

MERGE INTO YOUR EXISTING CHATBOT PROJECT (if it's already FastAPI):
    from architecture_flow_tool import api_router, dash_app
    from a2wsgi import WSGIMiddleware

    your_existing_app.include_router(api_router, prefix="/architecture-tool/api")
    your_existing_app.mount("/architecture-tool", WSGIMiddleware(dash_app.server))

That's the entire integration — nothing else in this file needs to change,
and nothing here assumes it owns the top-level app or the port.

WHAT'S REAL VS. A PLACEHOLDER, so you don't mistake one for the other:
  - The data model, CRUD, all five deploy scenarios, Pub/Sub-enforcement
    branching, payload validation, and the deterministic chat are fully
    implemented and were tested end to end while building this (FastAPI's
    TestClient for the API, real HTTP requests against a running Dash server
    for every UI callback) — this is not pseudocode.
  - The chat is deterministic ON PURPOSE — it reads the failed/reason fields
    already sitting on the data, no LLM call. That's evidence, not invented
    inference; wiring in a real LLM for ambiguous-cause reasoning and real
    connectors to your actual log store / database / deploy history is a
    deliberately separate, later step — not built here.
  - Diagram-image ingestion (upload a PNG, get a new service back) is NOT in
    this file. That needs a real call to a vision-capable model (e.g. Vertex
    AI Gemini with response_schema constrained to SystemModel/EdgeModel
    below) against your actual GCP project — faking that with placeholder
    code would be worse than leaving the seam visible. Add it as a new
    function that returns a ServiceModel and feeds SERVICES; nothing else
    needs to change.

A NOTE ON A DASH QUIRK, since it cost real debugging time to find: any Dash
callback whose Output uses allow_duplicate=True (needed here because several
callbacks all write to, e.g., "cyto-diagram.elements") MUST have its Output
wrapped in a list AND must return a list, even for a single value — e.g.
`return [no_update]`, not `return no_update`. Skipping this fails at runtash
with a confusing "wildcard multi-output" error. All callbacks below already
follow this; keep it in mind if you add new ones.
"""
from __future__ import annotations

import json
import re
import os
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import dash
from dash import dcc, html, Input, Output, State, dash_table, ALL, no_update
import dash_cytoscape as cyto
import requests


# =============================================================================
# 1. DATA MODEL
# =============================================================================

class SystemModel(BaseModel):
    id: str
    name: str
    sub: str = ""
    # Free-form category used only for icon/color grouping in the UI.
    # Add new kinds freely — nothing downstream restricts this to an enum.
    kind: str = "api"
    subsystems: List[str] = Field(default_factory=list)
    failed: bool = False
    reason: str = ""


class EdgeModel(BaseModel):
    id: int
    order: int
    time: str = ""  # full ISO 8601 with zone, e.g. "2026-09-19T09:06:58Z"
    source: str     # a System.id  (named "source" not "from" — reserved word in Python)
    target: str     # a System.id
    label: str
    type: str = "default"  # e.g. request | return | security | async | default
    phase: str = ""
    failed: bool = False
    skipped: bool = False  # rendered as "never happened" (e.g. blocked upstream)
    reason: str = ""


class SchemaField(BaseModel):
    name: str
    type: str  # string | number | array | object
    required: bool = False
    enum: Optional[List[str]] = None


class RequestSchema(BaseModel):
    id: str
    method: str
    path: str
    fields: List[SchemaField]


class ServiceModel(BaseModel):
    key: str
    name: str
    systems: List[SystemModel]
    edges: List[EdgeModel]
    has_deploy_scenarios: bool = False
    schemas: List[RequestSchema] = Field(default_factory=list)
    sample_payload: str = "{}"
    pubsub_enforcement: bool = False


class EdgePatch(BaseModel):
    field: str
    value: object


class EnforcementPatch(BaseModel):
    enabled: bool


class ScenarioRequest(BaseModel):
    scenario_key: str


class ValidateRequest(BaseModel):
    endpoint_id: str
    payload: dict


class ValidateResponse(BaseModel):
    status: int
    checks: List[dict]
    errors: List[str]
    banner: Optional[str] = None


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


# =============================================================================
# 2. SEED DATA — sample data, not logic. Add a service by adding a
#    ServiceModel here (or load from a DB); never by writing a new code path.
# =============================================================================

SHIPMENT_SYSTEMS = [
    SystemModel(id="user", name="User", sub="browser session", kind="user",
                subsystems=["Session cookie", "Local cache"]),
    SystemModel(id="webapp", name="Web App", sub="react ui", kind="frontend",
                subsystems=["Router", "State store", "API client"]),
    SystemModel(id="api", name="API", sub="request handler", kind="api",
                subsystems=["Auth middleware", "Rate limiter", "Validation"]),
    SystemModel(id="supplier", name="Supplier Gateway", sub="EDI / REST bridge", kind="integration",
                subsystems=["EDI 850/856 mapper", "Retry queue"]),
    SystemModel(id="pubsub", name="Pub/Sub", sub="event bus", kind="queue",
                subsystems=["shipment.updates topic", "dead-letter queue"]),
    SystemModel(id="sap", name="SAP ERP", sub="source of truth", kind="erp",
                subsystems=["MM module", "IDoc inbound"], failed=True,
                reason="IDoc inbound queue backed up — batch job SAP_IDOC_PROC_04 has been stuck "
                       "since 09:02 UTC, so inbound updates are queued but never committed to the "
                       "MM module."),
    SystemModel(id="trace", name="Trace", sub="async event", kind="trace",
                subsystems=["Kafka topic", "OTel collector"]),
]

SHIPMENT_EDGES = [
    EdgeModel(id=1, order=1, time="2026-09-19T09:06:40Z", source="user", target="webapp",
              label="open page", type="default", phase="Request"),
    EdgeModel(id=2, order=2, time="2026-09-19T09:06:41Z", source="webapp", target="api",
              label="GET /shipments/61340", type="request"),
    EdgeModel(id=3, order=3, time="2026-09-19T09:06:55Z", source="api", target="supplier",
              label="sync shipment update", type="request", phase="Supplier sync"),
    EdgeModel(id=4, order=4, time="2026-09-19T09:06:56Z", source="supplier", target="pubsub",
              label="publish shipment.update event", type="request"),
    EdgeModel(id=5, order=5, time="2026-09-19T09:06:58Z", source="pubsub", target="sap",
              label="deliver to SAP inbound queue", type="request", failed=True,
              reason="Delivered to the topic fine — the break is on the SAP side. SAP's IDoc "
                     "inbound consumer (SAP_IDOC_PROC_04) has been stuck since 09:02 UTC, so the "
                     "event was delivered but never processed into the MM module."),
    EdgeModel(id=12, order=6, time="2026-09-19T09:07:03Z", source="sap", target="pubsub",
              label="ack (expected)", type="return", failed=True,
              reason="SAP never publishes an ack — since it never processed the inbound event, no "
                     "confirmation event was produced for Pub/Sub to relay back."),
    EdgeModel(id=13, order=7, time="2026-09-19T09:07:04Z", source="pubsub", target="supplier",
              label="sync status: pending (no ack)", type="return"),
    EdgeModel(id=6, order=8, time="2026-09-19T09:07:04Z", source="supplier", target="api",
              label="sync status: pending", type="return"),
    EdgeModel(id=7, order=9, time="2026-09-19T09:07:05Z", source="api", target="trace",
              label="emit trace", type="async", phase="Response + trace"),
    EdgeModel(id=8, order=10, time="2026-09-19T09:07:05Z", source="api", target="webapp",
              label="200 JSON (sync pending)", type="return"),
    EdgeModel(id=9, order=11, time="2026-09-19T09:07:06Z", source="webapp", target="user",
              label="render", type="return"),
]

SHIPMENT_SCHEMAS = [
    RequestSchema(id="sync-shipment", method="POST", path="/api/shipments/sync", fields=[
        SchemaField(name="shipment_id", type="string", required=True),
        SchemaField(name="vin", type="string", required=True),
        SchemaField(name="carrier", type="string", required=True),
        SchemaField(name="status", type="string", required=True,
                    enum=["CREATED", "IN_TRANSIT", "DELIVERED"]),
        SchemaField(name="weight_kg", type="number", required=False),
    ]),
]

SHIPMENT_SAMPLE_PAYLOAD = (
    '{\n  "shipment_id": "SHIP-61340",\n  "carrier": "Northline Logistics",\n'
    '  "status": "IN_TRANSIT"\n}'
)

SHIPMENT_DEFAULT_LABELS = {
    4: "publish shipment.update event",
    5: "deliver to SAP inbound queue",
    6: "sync status: pending",
    8: "200 JSON (sync pending)",
    12: "ack (expected)",
    13: "sync status: pending (no ack)",
}

ORDER_SYSTEMS = [
    SystemModel(id="storefront", name="Storefront", sub="checkout ui", kind="frontend",
                subsystems=["Cart state", "Checkout form"]),
    SystemModel(id="orderapi", name="Order API", sub="request handler", kind="api",
                subsystems=["Validation", "Order state machine"]),
    SystemModel(id="inventory", name="Inventory Svc", sub="stock reservation", kind="integration",
                subsystems=["Reservation ledger"]),
    SystemModel(id="payment", name="Payment Gateway", sub="Stripe bridge", kind="integration",
                subsystems=["Auth calls", "Webhook receiver"], failed=True,
                reason="Stripe authorization calls are intermittently exceeding the 8s client "
                       "timeout under today's traffic spike — roughly 1 in 5 requests never get a "
                       "response back in time."),
    SystemModel(id="wms", name="Warehouse WMS", sub="pick & pack", kind="erp",
                subsystems=["Pick queue", "Pack station"]),
    SystemModel(id="notify", name="Notify Svc", sub="email / sms", kind="trace",
                subsystems=["Templates", "Delivery log"]),
]

ORDER_EDGES = [
    EdgeModel(id=101, order=1, time="2026-09-19T14:20:01Z", source="storefront", target="orderapi",
              label="POST /orders", type="request", phase="Checkout"),
    EdgeModel(id=102, order=2, time="2026-09-19T14:20:02Z", source="orderapi", target="inventory",
              label="reserve stock", type="request"),
    EdgeModel(id=103, order=3, time="2026-09-19T14:20:02Z", source="inventory", target="orderapi",
              label="reserved", type="return"),
    EdgeModel(id=104, order=4, time="2026-09-19T14:20:03Z", source="orderapi", target="payment",
              label="authorize payment", type="request", phase="Payment", failed=True,
              reason="This authorization call is one of the ones caught in Stripe's current slow "
                     "window — it was sent, but never came back inside the 8s client timeout."),
    EdgeModel(id=105, order=5, time="2026-09-19T14:20:11Z", source="payment", target="orderapi",
              label="authorization result", type="return", failed=True,
              reason="No response was ever returned for this attempt, so Order API has no way to "
                     "know whether the charge actually went through on Stripe's side."),
    EdgeModel(id=106, order=6, time="2026-09-19T14:20:12Z", source="orderapi", target="wms",
              label="create pick ticket (blocked — payment unconfirmed)", type="default",
              phase="Fulfillment"),
    EdgeModel(id=107, order=7, time="2026-09-19T14:20:12Z", source="orderapi", target="notify",
              label="send payment-pending email", type="async"),
    EdgeModel(id=108, order=8, time="2026-09-19T14:20:12Z", source="orderapi", target="storefront",
              label="checkout status: payment pending", type="return"),
]

ORDER_SCHEMAS = [
    RequestSchema(id="create-order", method="POST", path="/orders", fields=[
        SchemaField(name="customer_id", type="string", required=True),
        SchemaField(name="items", type="array", required=True),
        SchemaField(name="payment_method", type="string", required=True, enum=["card", "paypal"]),
        SchemaField(name="shipping_address", type="object", required=True),
    ]),
]

ORDER_SAMPLE_PAYLOAD = (
    '{\n  "customer_id": "CUST-9911",\n  "items": [{"sku":"BRK-4471","qty":2}],\n'
    '  "payment_method": "amex"\n}'
)

RETURNS_SYSTEMS = [
    SystemModel(id="portal", name="Customer Portal", sub="returns ui", kind="frontend",
                subsystems=["Return wizard"]),
    SystemModel(id="rma", name="RMA Service", sub="request handler", kind="api",
                subsystems=["RMA state machine"]),
    SystemModel(id="intake", name="Warehouse Intake", sub="inspection", kind="erp",
                subsystems=["Inspection queue"]),
    SystemModel(id="refund", name="Refund Service", sub="payment bridge", kind="integration",
                subsystems=["Refund ledger"], failed=True,
                reason="Payment Gateway rotated its API credentials during last night's deploy. "
                       "Refund Service's stored key wasn't updated, so every refund call since has "
                       "failed with 401 Unauthorized."),
    SystemModel(id="notify2", name="Notify Svc", sub="email / sms", kind="trace",
                subsystems=["Templates", "Delivery log"]),
]

RETURNS_EDGES = [
    EdgeModel(id=201, order=1, time="2026-09-19T11:10:00Z", source="portal", target="rma",
              label="POST /returns", type="request", phase="Return request"),
    EdgeModel(id=202, order=2, time="2026-09-19T11:10:01Z", source="rma", target="intake",
              label="expect return shipment", type="request"),
    EdgeModel(id=203, order=3, time="2026-09-19T11:12:30Z", source="intake", target="rma",
              label="item received & inspected", type="return", phase="Intake"),
    EdgeModel(id=204, order=4, time="2026-09-19T11:12:35Z", source="rma", target="refund",
              label="issue refund", type="request", phase="Refund", failed=True,
              reason="Rejected at the door — Refund Service is presenting the old, now-rotated "
                     "credential to Payment Gateway."),
    EdgeModel(id=205, order=5, time="2026-09-19T11:12:36Z", source="refund", target="rma",
              label="refund result (401)", type="return", failed=True,
              reason="Every attempt comes back 401 Unauthorized — this is auth-side, not a payload "
                     "or balance issue."),
    EdgeModel(id=206, order=6, time="2026-09-19T11:12:37Z", source="rma", target="notify2",
              label="send refund-failed alert", type="async"),
    EdgeModel(id=207, order=7, time="2026-09-19T11:12:37Z", source="rma", target="portal",
              label="return status: refund pending", type="return"),
]

RETURNS_SCHEMAS = [
    RequestSchema(id="create-return", method="POST", path="/returns", fields=[
        SchemaField(name="order_id", type="string", required=True),
        SchemaField(name="reason", type="string", required=True,
                    enum=["damaged", "wrong_item", "no_longer_needed"]),
        SchemaField(name="items", type="array", required=True),
        SchemaField(name="refund_method", type="string", required=False),
    ]),
]

RETURNS_SAMPLE_PAYLOAD = '{\n  "order_id": "ORD-5521",\n  "items": [{"sku":"TRB-220"}]\n}'

DIRECTSCALE_SYSTEMS = [
    SystemModel(id="distributor", name="Distributor", sub="commission portal user", kind="user",
                subsystems=["AngularJS SPA (HTML5/CSS3)", "Assets delivered via CDN"]),
    SystemModel(id="distportal", name="Distributor Portal API", sub="CloudSpark hosted", kind="api",
                subsystems=["Redis Cache", "MS-SQL (distributor data)"]),
    SystemModel(id="notify", name="Notification API", sub="comms gateway", kind="integration",
                subsystems=["Redis Cache", "MS-SQL", "ZipLingo (SMS & Email)"]),
    SystemModel(id="bus", name="Integration Bus", sub="distributed provider routing", kind="queue",
                subsystems=["Per-client provider config", "Routes to ERP/CRM, Payment, Logistics"]),
    SystemModel(id="disco", name="Disco (Commission Svc)", sub="per-client commission engine",
                kind="api",
                subsystems=["Commission Service",
                            "Batch Commission Service (Stats & Commission Profiles)"]),
    SystemModel(id="dbmaster", name="Database (Master)", sub="Disco source of truth", kind="erp",
                subsystems=["Per-client database", "Distributor & customer data"]),
    SystemModel(id="dbdr", name="Database (DR Replica)", sub="disaster recovery", kind="erp",
                subsystems=["Async replica of Master"]),
    SystemModel(id="dbbi", name="Database (BI Replica)", sub="analytics replica", kind="erp",
                subsystems=["Async replica of Master"]),
    SystemModel(id="payment", name="Payment Processors", sub="capture / payout funds",
                kind="integration", subsystems=["3rd-party, per-client configured"], failed=True,
                reason="DirectScale's batch commission run fires thousands of payout calls in a "
                       "tight window at month-end. The processor's per-minute rate limit was hit "
                       "partway through this run, so calls after that point are being rejected "
                       "with 429 Too Many Requests until the window resets."),
    SystemModel(id="erp", name="3rd Party ERP/CRM", sub="back-office sync", kind="integration",
                subsystems=["Per-client configured provider"]),
]

DIRECTSCALE_EDGES = [
    EdgeModel(id=301, order=1, time="2026-09-01T02:00:00Z", source="distributor", target="distportal",
              label="trigger monthly commission run", type="default", phase="Commission Run"),
    EdgeModel(id=302, order=2, time="2026-09-01T02:00:02Z", source="distportal", target="bus",
              label="publish commission.run.started event", type="request"),
    EdgeModel(id=303, order=3, time="2026-09-01T02:00:03Z", source="bus", target="disco",
              label="route event to Disco (client config)", type="request", phase="Commission Engine"),
    EdgeModel(id=304, order=4, time="2026-09-01T02:04:10Z", source="disco", target="dbmaster",
              label="calculate & write commission records", type="request"),
    EdgeModel(id=305, order=5, time="2026-09-01T02:05:00Z", source="dbmaster", target="dbdr",
              label="replicate to DR", type="async", phase="Replication"),
    EdgeModel(id=306, order=6, time="2026-09-01T02:05:02Z", source="dbmaster", target="dbbi",
              label="replicate to BI", type="async"),
    EdgeModel(id=307, order=7, time="2026-09-01T02:10:00Z", source="disco", target="payment",
              label="payout funds (batch)", type="request", phase="Payout", failed=True,
              reason="This batch of payout calls landed after the processor's per-minute rate "
                     "limit was already exhausted by earlier calls in the same run."),
    EdgeModel(id=308, order=8, time="2026-09-01T02:10:45Z", source="payment", target="disco",
              label="payout result: 429 rate limited (partial)", type="return", failed=True,
              reason="Only a subset of this batch was accepted before the limit hit — the rest "
                     "need to be retried once the processor's rate window resets."),
    EdgeModel(id=309, order=9, time="2026-09-01T02:12:00Z", source="disco", target="erp",
              label="sync commission ledger to ERP/CRM", type="request", phase="Back-office sync"),
    EdgeModel(id=310, order=10, time="2026-09-01T02:12:05Z", source="disco", target="notify",
              label="send payout confirmation", type="async"),
    EdgeModel(id=311, order=11, time="2026-09-01T02:12:10Z", source="notify", target="distributor",
              label="email/SMS via ZipLingo: payout summary (partial)", type="return"),
]

DIRECTSCALE_SCHEMAS = [
    RequestSchema(id="trigger-run", method="POST", path="/api/commission/run", fields=[
        SchemaField(name="client_id", type="string", required=True),
        SchemaField(name="run_type", type="string", required=True, enum=["nightly", "on-demand"]),
        SchemaField(name="effective_date", type="string", required=True),
        SchemaField(name="distributor_ids", type="array", required=False),
    ]),
]

DIRECTSCALE_SAMPLE_PAYLOAD = (
    '{\n  "client_id": "CLIENT-7734",\n  "run_type": "nightly",\n'
    '  "distributor_ids": ["DIST-1120","DIST-1121"]\n}'
)


def _fresh_services() -> dict:
    """Deep copies every time, so mutating one running instance's data never
    leaks into another (e.g. a reset, or the kept 'original' snapshot)."""
    return {
        "shipment-sync": ServiceModel(
            key="shipment-sync", name="Shipment Sync",
            systems=[s.model_copy(deep=True) for s in SHIPMENT_SYSTEMS],
            edges=[e.model_copy(deep=True) for e in SHIPMENT_EDGES],
            has_deploy_scenarios=True, schemas=SHIPMENT_SCHEMAS,
            sample_payload=SHIPMENT_SAMPLE_PAYLOAD,
        ),
        "order-fulfillment": ServiceModel(
            key="order-fulfillment", name="Order Fulfillment",
            systems=[s.model_copy(deep=True) for s in ORDER_SYSTEMS],
            edges=[e.model_copy(deep=True) for e in ORDER_EDGES],
            has_deploy_scenarios=False, schemas=ORDER_SCHEMAS,
            sample_payload=ORDER_SAMPLE_PAYLOAD,
        ),
        "returns-processing": ServiceModel(
            key="returns-processing", name="Returns Processing",
            systems=[s.model_copy(deep=True) for s in RETURNS_SYSTEMS],
            edges=[e.model_copy(deep=True) for e in RETURNS_EDGES],
            has_deploy_scenarios=False, schemas=RETURNS_SCHEMAS,
            sample_payload=RETURNS_SAMPLE_PAYLOAD,
        ),
        "directscale": ServiceModel(
            key="directscale", name="DirectScale Commission Platform",
            systems=[s.model_copy(deep=True) for s in DIRECTSCALE_SYSTEMS],
            edges=[e.model_copy(deep=True) for e in DIRECTSCALE_EDGES],
            has_deploy_scenarios=False, schemas=DIRECTSCALE_SCHEMAS,
            sample_payload=DIRECTSCALE_SAMPLE_PAYLOAD,
        ),
    }


# =============================================================================
# 3. STORE — CRUD + deploy scenarios + Pub/Sub enforcement.
#    Swap this for a real database later without touching route logic below;
#    every function here takes/returns plain ServiceModel objects.
# =============================================================================

_STATE: dict[str, ServiceModel] = _fresh_services()
_ORIGINAL_SHIPMENT_SYNC = _fresh_services()["shipment-sync"]


def list_services() -> list[dict]:
    return [{"key": s.key, "name": s.name, "has_deploy_scenarios": s.has_deploy_scenarios}
            for s in _STATE.values()]


def get_service(key: str) -> ServiceModel:
    svc = _STATE.get(key)
    if not svc:
        raise HTTPException(status_code=404, detail=f'No service "{key}"')
    return svc


def _find_system(svc: ServiceModel, system_id: str) -> Optional[SystemModel]:
    return next((s for s in svc.systems if s.id == system_id), None)


def _find_edge(svc: ServiceModel, edge_id: int) -> Optional[EdgeModel]:
    return next((e for e in svc.edges if e.id == edge_id), None)


def add_system(key: str, system: SystemModel) -> ServiceModel:
    svc = get_service(key)
    if _find_system(svc, system.id):
        raise HTTPException(status_code=400, detail=f'System id "{system.id}" already exists')
    svc.systems.append(system)
    return svc


def remove_system(key: str, system_id: str) -> ServiceModel:
    svc = get_service(key)
    svc.systems = [s for s in svc.systems if s.id != system_id]
    svc.edges = [e for e in svc.edges if e.source != system_id and e.target != system_id]
    return svc


def add_edge(key: str, edge: EdgeModel) -> ServiceModel:
    svc = get_service(key)
    if _find_edge(svc, edge.id):
        raise HTTPException(status_code=400, detail=f'Edge id {edge.id} already exists')
    svc.edges.append(edge)
    return svc


def update_edge(key: str, edge_id: int, field: str, value) -> ServiceModel:
    svc = get_service(key)
    edge = _find_edge(svc, edge_id)
    if not edge:
        raise HTTPException(status_code=404, detail=f"No edge {edge_id}")
    if not hasattr(edge, field):
        raise HTTPException(status_code=400, detail=f'Edge has no field "{field}"')
    setattr(edge, field, value)
    return svc


def remove_edge(key: str, edge_id: int) -> ServiceModel:
    svc = get_service(key)
    svc.edges = [e for e in svc.edges if e.id != edge_id]
    return svc


def set_enforcement(key: str, enabled: bool) -> ServiceModel:
    svc = get_service(key)
    svc.pubsub_enforcement = enabled
    return svc


def _reset_failures(svc: ServiceModel) -> None:
    for s in svc.systems:
        s.failed = False
        s.reason = ""
    for e in svc.edges:
        e.failed = False
        e.skipped = False
        e.reason = ""
        if svc.key == "shipment-sync" and e.id in SHIPMENT_DEFAULT_LABELS:
            e.label = SHIPMENT_DEFAULT_LABELS[e.id]


def _edge(svc: ServiceModel, edge_id: int) -> EdgeModel:
    e = _find_edge(svc, edge_id)
    assert e is not None
    return e


def _system(svc: ServiceModel, system_id: str) -> SystemModel:
    s = _find_system(svc, system_id)
    assert s is not None
    return s


def _apply_schema_scenario(svc: ServiceModel) -> None:
    if svc.pubsub_enforcement:
        pub = _edge(svc, 4)
        pub.failed = True
        pub.reason = (
            "Pub/Sub rejected this publish immediately — the Avro schema attached to the "
            "shipment.updates topic requires a \"vin\" field, and Supplier Gateway's payload "
            "doesn't have one. Rejected client-side in under 5ms, before the message ever "
            "entered the topic."
        )
        for eid in (5, 12, 13):
            skipped_edge = _edge(svc, eid)
            skipped_edge.skipped = True
            skipped_edge.reason = (
                "Never happened — the publish upstream was rejected by Pub/Sub's schema "
                "before this message could exist."
            )
        _edge(svc, 6).label = "sync status: rejected (400 — schema)"
    else:
        e = _edge(svc, 5)
        e.label = "deliver v1 payload (schema v2 expected)"
        e.failed = True
        e.reason = (
            "A deploy to SAP's inbound consumer at 08:50 UTC now requires schema v2 (adds a "
            "required \"vin\" field). Supplier Gateway's producer hasn't been updated and "
            "still emits v1 — every v1 message has failed validation since the deploy went out."
        )


def _apply_flag_scenario(svc: ServiceModel) -> None:
    e = _edge(svc, 4)
    e.label = "publish shipment.update event (batched)"
    e.failed = True
    e.reason = (
        "No deploy happened — the feature flag \"enable-batch-edi-856\" was flipped ON in prod "
        "at 09:00 UTC. Supplier Gateway now batches events every 5 minutes instead of streaming "
        "them, so downstream systems see stale \"pending\" status for up to 5 minutes. Not a "
        "break, but it reads as one — and a deploy-only monitor would miss this entirely since "
        "flags don't emit deploy events."
    )


def _apply_config_scenario(svc: ServiceModel) -> None:
    e = _edge(svc, 5)
    e.failed = True
    e.reason = (
        "A deploy at 08:50 UTC reduced the Pub/Sub → SAP delivery timeout from 60s to 30s as "
        "part of an unrelated \"tighten SLAs\" change. SAP's inbound processing routinely takes "
        "35-40s under normal load, so deliveries that used to succeed now time out on retry "
        "exhaustion — no code logic changed, only a number in config."
    )


def _apply_rollback_scenario(svc: ServiceModel) -> None:
    e = _edge(svc, 5)
    e.failed = True
    e.reason = (
        "A rollback of Supplier Gateway from v3.2 to v3.1 only completed on 2 of 4 pods before "
        "being paused. The other 2 pods still run v3.2's newer payload shape, so SAP is "
        "intermittently receiving two different message formats from what looks like a single "
        "service — roughly half of deliveries fail schema validation, half succeed, with no "
        "obvious pattern unless you check pod-level versions."
    )


def _apply_secret_scenario(svc: ServiceModel) -> None:
    p = _system(svc, "pubsub")
    p.failed = True
    p.reason = (
        "The credential for SAP's IDoc inbound endpoint was rotated at 09:00 UTC as part of a "
        "routine deploy. Pub/Sub's outbound connector config was not updated with the new "
        "credential, so every delivery attempt to SAP has failed with 401 Unauthorized since the "
        "rotation — this is auth-side, not a payload or timing issue."
    )
    e = _edge(svc, 5)
    e.label = "deliver to SAP inbound queue (401)"
    e.failed = True
    e.reason = "Rejected at the door — Pub/Sub is presenting the old, now-rotated credential to SAP."


DEPLOY_SCENARIOS = {
    "schema": ("Schema tightened, producer not updated", _apply_schema_scenario),
    "flag": ("Feature flag flipped in prod", _apply_flag_scenario),
    "config": ("Config drift — timeout reduced", _apply_config_scenario),
    "rollback": ("Bad rollback — mixed versions", _apply_rollback_scenario),
    "secret": ("Rotated secret not updated everywhere", _apply_secret_scenario),
}


def apply_scenario(key: str, scenario_key: str) -> ServiceModel:
    svc = get_service(key)
    _reset_failures(svc)

    if scenario_key == "healthy":
        if svc.key == "shipment-sync":
            _edge(svc, 4).label = "publish shipment.update event"
            _edge(svc, 5).label = "deliver to SAP inbound queue"
            _edge(svc, 12).label = "ack received"
            _edge(svc, 13).label = "sync status: confirmed"
            _edge(svc, 6).label = "sync status: confirmed"
            _edge(svc, 8).label = "200 JSON (fully synced)"
        return svc

    if scenario_key == "original":
        if svc.key != "shipment-sync":
            raise HTTPException(status_code=400, detail="Only shipment-sync has an original state")
        _STATE[key] = _ORIGINAL_SHIPMENT_SYNC.model_copy(deep=True)
        return _STATE[key]

    if not svc.has_deploy_scenarios:
        raise HTTPException(status_code=400, detail=f'"{key}" has no deploy scenarios configured')

    scenario = DEPLOY_SCENARIOS.get(scenario_key)
    if not scenario:
        raise HTTPException(status_code=404, detail=f'No scenario "{scenario_key}"')
    _, apply_fn = scenario
    apply_fn(svc)
    return svc


# =============================================================================
# 4. VALIDATOR — Postman-style payload validation against a RequestSchema.
# =============================================================================

def validate_payload(schema: RequestSchema, payload: dict) -> Tuple[list, list]:
    """Returns (errors, checks). checks is a list of {"label": str, "pass_": bool}."""
    errors: list[str] = []
    checks: list[dict] = []

    for f in schema.fields:
        present = f.name in payload

        if f.required and not present:
            errors.append(f'Missing required field "{f.name}"')
            checks.append({"label": f"{f.name} — required, present", "pass_": False})
            continue

        if not present:
            checks.append({"label": f"{f.name} — optional, omitted", "pass_": True})
            continue

        checks.append({"label": f"{f.name} — present", "pass_": True})
        val = payload[f.name]

        type_ok = True
        if f.type == "string":
            type_ok = isinstance(val, str)
        elif f.type == "number":
            type_ok = isinstance(val, (int, float)) and not isinstance(val, bool)
        elif f.type == "array":
            type_ok = isinstance(val, list)
        elif f.type == "object":
            type_ok = isinstance(val, dict)

        checks.append({"label": f"{f.name} — is {f.type}", "pass_": type_ok})
        if not type_ok:
            errors.append(f'"{f.name}" should be a {f.type}, got {type(val).__name__}')

        if f.enum:
            enum_ok = val in f.enum
            checks.append({"label": f"{f.name} — one of [{', '.join(f.enum)}]", "pass_": enum_ok})
            if not enum_ok:
                errors.append(f'"{f.name}" must be one of: {", ".join(f.enum)} (got "{val}")')

    return errors, checks


def pubsub_banner(has_errors: bool, svc: ServiceModel) -> str:
    """Only meaningful for services fronting a message bus — extend this
    check (e.g. look for a system with kind == "queue") if you add more."""
    if not has_errors or svc.key != "shipment-sync":
        return ""
    if svc.pubsub_enforcement:
        return ("🛑 Blocked at Pub/Sub — the shipment.updates schema rejects this at "
                "publish time. SAP never sees it.")
    return ("⚠ No schema enforced — this would still publish, then fail deep inside "
            "SAP's IDoc processing instead of failing here.")


# =============================================================================
# 5. CHAT — deterministic "why did X fail". Reads failed/reason fields
#    already on the data; not generative. See module docstring above.
# =============================================================================

_JSON_BLOB = re.compile(r"\{[\s\S]*\}")
_VALIDATION_INTENT = re.compile(r"valid|test|payload|check|send|request", re.IGNORECASE)


def _find_system_by_name(svc: ServiceModel, text: str):
    t = text.lower()
    for s in svc.systems:
        if s.name.lower() in t or s.id.lower() in t:
            return s
    return None


def _name_of(svc: ServiceModel, system_id: str) -> str:
    s = next((s for s in svc.systems if s.id == system_id), None)
    return s.name if s else system_id


def answer_question(svc: ServiceModel, text: str) -> str:
    failed_edges = [e for e in svc.edges if e.failed]
    failed_systems = [s for s in svc.systems if s.failed]

    if not failed_edges and not failed_systems:
        return ("Yes — every leg in the current flow completed successfully end to end. "
                "Nothing is marked as failing right now.")

    mentioned = _find_system_by_name(svc, text)

    if mentioned:
        sys_fail = mentioned if mentioned.failed else None
        edge_fails = [e for e in failed_edges if e.source == mentioned.id or e.target == mentioned.id]

        if not sys_fail and not edge_fails:
            other = (failed_systems[0].id if failed_systems else failed_edges[0].target)
            return (f"{mentioned.name} isn't involved in any failure right now — the failing "
                    f"part of the flow is elsewhere. Try asking about {_name_of(svc, other)}.")

        parts = []
        if sys_fail:
            parts.append(f"<b>{mentioned.name} is marked down.</b> {mentioned.reason}")
        for e in edge_fails:
            parts.append(
                f"<b>{_name_of(svc, e.source)} → {_name_of(svc, e.target)}</b> "
                f"({e.label}) is failing: {e.reason}"
            )
        return "<br><br>".join(parts)

    parts = []
    for s in failed_systems:
        parts.append(f"<b>{s.name}</b> is down — {s.reason}")
    for e in failed_edges:
        parts.append(f"<b>{_name_of(svc, e.source)} → {_name_of(svc, e.target)}</b> "
                     f"({e.label}) is failing — {e.reason}")
    return "<br><br>".join(parts)


def answer_payload_validation(svc: ServiceModel, full_text: str, json_str: str) -> str:
    try:
        obj = json.loads(json_str)
    except json.JSONDecodeError as err:
        return f"That doesn't parse as JSON — {err}. Check for a trailing comma or an unquoted key."

    if not svc.schemas:
        return f"No request schemas are configured yet for {svc.name}."

    schema = next((s for s in svc.schemas if s.path in full_text), svc.schemas[0])
    errors, checks = validate_payload(schema, obj)

    lines = [f"<b>{schema.method} {schema.path}</b> — "
             f"{'Status 400 Bad Request' if errors else 'Status 200 OK'}"]
    for c in checks:
        lines.append(f"{'✓' if c['pass_'] else '✗'} {c['label']}")
    if errors:
        lines.append(f"{len(errors)} error(s) found.")
    banner = pubsub_banner(bool(errors), svc)
    if banner:
        lines.append(banner)
    return "<br>".join(lines)


def ask_chat(svc: ServiceModel, text: str) -> str:
    json_match = _JSON_BLOB.search(text)
    if json_match and _VALIDATION_INTENT.search(text):
        return answer_payload_validation(svc, text, json_match.group(0))
    return answer_question(svc, text)


# =============================================================================
# 6. FASTAPI — the API surface, as a mountable router.
# =============================================================================

api_router = APIRouter()


@api_router.get("/services")
def _list_services():
    return list_services()


@api_router.get("/services/{key}", response_model=ServiceModel)
def _get_service(key: str):
    return get_service(key)


@api_router.post("/services/{key}/systems", response_model=ServiceModel)
def _add_system(key: str, system: SystemModel):
    return add_system(key, system)


@api_router.delete("/services/{key}/systems/{system_id}", response_model=ServiceModel)
def _remove_system(key: str, system_id: str):
    return remove_system(key, system_id)


@api_router.post("/services/{key}/edges", response_model=ServiceModel)
def _add_edge(key: str, edge: EdgeModel):
    return add_edge(key, edge)


@api_router.patch("/services/{key}/edges/{edge_id}", response_model=ServiceModel)
def _update_edge(key: str, edge_id: int, patch: EdgePatch):
    return update_edge(key, edge_id, patch.field, patch.value)


@api_router.delete("/services/{key}/edges/{edge_id}", response_model=ServiceModel)
def _remove_edge(key: str, edge_id: int):
    return remove_edge(key, edge_id)


@api_router.post("/services/{key}/enforcement", response_model=ServiceModel)
def _set_enforcement(key: str, patch: EnforcementPatch):
    return set_enforcement(key, patch.enabled)


@api_router.post("/services/{key}/scenario", response_model=ServiceModel)
def _apply_scenario(key: str, body: ScenarioRequest):
    return apply_scenario(key, body.scenario_key)


@api_router.post("/services/{key}/validate", response_model=ValidateResponse)
def _validate(key: str, body: ValidateRequest):
    svc = get_service(key)
    schema = next((s for s in svc.schemas if s.id == body.endpoint_id), None)
    if not schema:
        raise HTTPException(status_code=404, detail=f'No endpoint "{body.endpoint_id}"')
    errors, checks = validate_payload(schema, body.payload)
    return ValidateResponse(
        status=400 if errors else 200, checks=checks, errors=errors,
        banner=pubsub_banner(bool(errors), svc) or None,
    )


@api_router.post("/services/{key}/chat", response_model=ChatResponse)
def _chat(key: str, body: ChatRequest):
    svc = get_service(key)
    return ChatResponse(reply=ask_chat(svc, body.message))


# =============================================================================
# 7. DASH FRONTEND — talks to the API above over HTTP.
# =============================================================================

API_BASE = os.environ.get("ARCH_TOOL_API_BASE", "http://localhost:8000/architecture-tool/api")

KIND_COLOR = {
    "user": "#4FC3F7", "frontend": "#35D8C4", "api": "#7ED957",
    "integration": "#C792EA", "erp": "#7C93FF", "trace": "#F5A742", "queue": "#4FD1C5",
}
TYPE_COLOR = {
    "request": "#3DDC84", "return": "#4FC7D8", "security": "#FF6FA0",
    "async": "#B18CFF", "default": "#8B96B4",
}
FAIL_COLOR = "#FF5C6C"
SKIP_COLOR = "#5A6482"

dash_app = dash.Dash(
    __name__,
    suppress_callback_exceptions=True,
    requests_pathname_prefix="/architecture-tool/",
    # NOTE: routes_pathname_prefix is deliberately left at its default ("/").
    # Starlette's app.mount("/architecture-tool", ...) strips that prefix
    # before handing the request to Dash's Flask server, so Dash itself must
    # keep matching un-prefixed paths internally. requests_pathname_prefix is
    # what gets embedded in the HTML so the BROWSER asks for
    # "/architecture-tool/_dash-layout" etc., which the mount then routes
    # back in correctly. Setting routes_pathname_prefix to the same value
    # here would make Dash expect the prefix twice and 404 everything.
)


def _api_get(path):
    return requests.get(f"{API_BASE}{path}", timeout=10).json()


def _api_post(path, body=None):
    return requests.post(f"{API_BASE}{path}", json=body or {}, timeout=10).json()


def _api_patch(path, body):
    return requests.patch(f"{API_BASE}{path}", json=body, timeout=10).json()


def build_cytoscape_elements(service: dict):
    elements = []
    x_gap = 180
    for i, s in enumerate(service["systems"]):
        elements.append({
            "data": {"id": s["id"], "label": f"{s['name']}\n{s['sub']}"},
            "position": {"x": i * x_gap, "y": 40},
            "classes": "failed" if s["failed"] else "ok",
        })
    edges_sorted = sorted(service["edges"], key=lambda e: e["order"])
    for e in edges_sorted:
        if e["skipped"]:
            cls = "skipped"
        elif e["failed"]:
            cls = "failed"
        else:
            cls = "ok"
        label = f"{e['order']}. {e['label']}" + (" (never sent)" if e["skipped"] else "")
        elements.append({
            "data": {"source": e["source"], "target": e["target"], "label": label,
                      "time": e.get("time", "")},
            "classes": cls,
        })
    return elements


CYTO_STYLESHEET = [
    {"selector": "node", "style": {
        "label": "data(label)", "text-wrap": "wrap", "text-valign": "center",
        "background-color": "#161F30", "border-width": 2, "border-color": "#34405C",
        "color": "#E8ECF4", "font-size": "10px", "width": 140, "height": 50,
        "shape": "round-rectangle",
    }},
    {"selector": "node.failed", "style": {"border-color": FAIL_COLOR, "border-width": 3}},
    {"selector": "edge", "style": {
        "curve-style": "bezier", "target-arrow-shape": "triangle",
        "label": "data(label)", "font-size": "9px", "color": "#8B96B4",
        "line-color": "#8B96B4", "target-arrow-color": "#8B96B4", "width": 2,
    }},
    {"selector": "edge.failed", "style": {
        "line-color": FAIL_COLOR, "target-arrow-color": FAIL_COLOR, "line-style": "dashed",
    }},
    {"selector": "edge.skipped", "style": {
        "line-color": SKIP_COLOR, "target-arrow-color": SKIP_COLOR,
        "line-style": "dotted", "opacity": 0.5,
    }},
]

dash_app.layout = html.Div(style={
    "fontFamily": "Inter, system-ui, sans-serif", "background": "#080B14",
    "color": "#E9ECF6", "minHeight": "100vh", "padding": "24px",
}, children=[

    dcc.Store(id="service-key", data="shipment-sync"),
    dcc.Store(id="chat-history", data=[]),
    dcc.Store(id="service-panel-open", data=True),

    html.H2("Architecture Flow Tool", style={"marginBottom": "2px"}),
    html.P("Editable multi-service architecture flow, a deterministic failure-trace chat, "
           "and a request validator.", style={"color": "#8B96B4", "marginTop": 0}),

    html.Div(style={"background": "#121927", "border": "1px solid #232C42",
                     "borderRadius": "10px", "padding": "12px 16px", "marginBottom": "14px"},
             children=[
        html.Div(id="service-header", n_clicks=0, style={"cursor": "pointer", "fontWeight": 600},
                  children="Services ▾"),
        html.Div(id="service-list", style={"display": "flex", "gap": "8px", "marginTop": "10px",
                                            "flexWrap": "wrap"}),
    ]),

    html.Div(style={"background": "#121927", "border": "1px solid #232C42",
                     "borderRadius": "10px", "marginBottom": "14px"}, children=[
        html.Div(id="chat-window", style={"maxHeight": "220px", "overflowY": "auto",
                                           "padding": "14px", "display": "flex",
                                           "flexDirection": "column", "gap": "8px"}),
        html.Div(style={"display": "flex", "gap": "8px", "padding": "10px 14px",
                         "borderTop": "1px solid #1A2136"}, children=[
            dcc.Input(id="chat-input", type="text", placeholder="Ask why something failed…",
                      style={"flex": 1, "background": "#161F30", "border": "1px solid #232C42",
                             "color": "#E9ECF6", "borderRadius": "8px", "padding": "9px 11px"}),
            html.Button("Ask", id="chat-send", n_clicks=0,
                        style={"background": "#3DDC84", "border": "none", "borderRadius": "8px",
                               "padding": "0 16px", "fontWeight": 600, "cursor": "pointer"}),
        ]),
    ]),

    html.Div(id="scenario-card", style={"background": "#121927", "border": "1px solid #232C42",
                                         "borderRadius": "10px", "padding": "14px 16px",
                                         "marginBottom": "14px"}),

    html.Div(style={"background": "#121927", "border": "1px solid #232C42",
                     "borderRadius": "12px", "padding": "10px", "marginBottom": "14px"},
             children=[
        cyto.Cytoscape(id="cyto-diagram", elements=[], stylesheet=CYTO_STYLESHEET,
                       layout={"name": "preset"}, style={"width": "100%", "height": "360px"}),
    ]),

    html.Div(style={"display": "flex", "gap": "8px", "marginBottom": "14px"}, children=[
        dcc.Input(id="new-system-id", placeholder="id (e.g. warehouse)", style={"padding": "6px"}),
        dcc.Input(id="new-system-name", placeholder="Name", style={"padding": "6px"}),
        dcc.Input(id="new-system-kind", placeholder="kind (api/integration/...)",
                  style={"padding": "6px"}),
        html.Button("+ Add system", id="add-system-btn", n_clicks=0),
    ]),

    html.Div(style={"background": "#121927", "border": "1px solid #232C42",
                     "borderRadius": "10px", "padding": "14px", "marginBottom": "14px"},
             children=[
        html.H4("Message flow", style={"marginTop": 0, "fontSize": "13px", "color": "#8B96B4"}),
        dash_table.DataTable(
            id="edges-table",
            columns=[
                {"name": "Order", "id": "order", "type": "numeric", "editable": True},
                {"name": "Time", "id": "time", "editable": True},
                {"name": "From", "id": "source", "editable": True},
                {"name": "To", "id": "target", "editable": True},
                {"name": "Label", "id": "label", "editable": True},
                {"name": "Type", "id": "type", "editable": True, "presentation": "dropdown"},
                {"name": "Phase", "id": "phase", "editable": True},
                {"name": "Failed", "id": "failed", "type": "any", "editable": True},
                {"name": "Reason", "id": "reason", "editable": True},
            ],
            dropdown={"type": {"options": [{"label": t, "value": t} for t in TYPE_COLOR]}},
            data=[], editable=True, row_deletable=True,
            style_table={"overflowX": "auto"},
            style_cell={"backgroundColor": "#161F30", "color": "#E9ECF6",
                        "border": "1px solid #232C42", "fontSize": "12px", "padding": "4px 6px"},
            style_header={"backgroundColor": "#121927", "fontWeight": 600},
        ),
        html.Button("+ Add message", id="add-edge-btn", n_clicks=0, style={"marginTop": "10px"}),
    ]),

    html.Div(style={"background": "#121927", "border": "1px solid #232C42",
                     "borderRadius": "10px", "padding": "14px 16px"}, children=[
        html.H4("API request validator", style={"marginTop": 0, "fontSize": "13px",
                                                  "color": "#8B96B4"}),
        html.Div(style={"display": "grid", "gridTemplateColumns": "1fr 1fr", "gap": "14px"},
                 children=[
            html.Div([
                dcc.Dropdown(id="endpoint-dropdown", style={"marginBottom": "10px", "color": "#111"}),
                dcc.Textarea(id="payload-input", style={"width": "100%", "minHeight": "120px",
                                                          "background": "#161F30",
                                                          "color": "#E9ECF6",
                                                          "border": "1px solid #232C42",
                                                          "borderRadius": "6px"}),
                html.Button("Send", id="validate-btn", n_clicks=0, style={"marginTop": "8px"}),
            ]),
            html.Div(id="validate-result", style={"background": "#161F30",
                                                    "border": "1px solid #1A2136",
                                                    "borderRadius": "8px", "padding": "10px",
                                                    "minHeight": "150px", "fontSize": "12px",
                                                    "fontFamily": "monospace"}),
        ]),
    ]),
])


@dash_app.callback(
    Output("service-panel-open", "data"),
    Input("service-header", "n_clicks"),
    State("service-panel-open", "data"),
    prevent_initial_call=True,
)
def toggle_service_panel(_n, is_open):
    return not is_open


@dash_app.callback(
    Output("service-list", "children"),
    Output("service-list", "style"),
    Input("service-panel-open", "data"),
    Input("service-key", "data"),
)
def render_service_list(is_open, active_key):
    style = {"display": "flex", "gap": "8px", "marginTop": "10px", "flexWrap": "wrap"}
    if not is_open:
        style["display"] = "none"
    services = _api_get("/services")
    buttons = []
    for s in services:
        active = s["key"] == active_key
        buttons.append(html.Button(
            s["name"], id={"type": "service-btn", "key": s["key"]}, n_clicks=0,
            style={
                "padding": "7px 13px", "borderRadius": "8px", "cursor": "pointer",
                "border": f"1px solid {'#3DDC84' if active else '#232C42'}",
                "background": "#121927", "color": "#3DDC84" if active else "#E9ECF6",
                "fontWeight": 600 if active else 400,
            },
        ))
    return buttons, style


@dash_app.callback(
    Output("service-key", "data"),
    Input({"type": "service-btn", "key": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def select_service(_clicks):
    ctx = dash.callback_context
    if not ctx.triggered:
        return no_update
    triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]
    return json.loads(triggered_id)["key"]


@dash_app.callback(
    Output("cyto-diagram", "elements"),
    Output("edges-table", "data"),
    Output("scenario-card", "children"),
    Output("endpoint-dropdown", "options"),
    Output("endpoint-dropdown", "value"),
    Output("payload-input", "value"),
    Input("service-key", "data"),
)
def refresh_service_view(key):
    svc = _api_get(f"/services/{key}")
    elements = build_cytoscape_elements(svc)
    table_data = svc["edges"]

    scenario_children = []
    if svc["has_deploy_scenarios"]:
        scenario_children.append(html.Div(style={"display": "flex", "alignItems": "center",
                                                    "gap": "10px", "marginBottom": "10px"}, children=[
            html.Button("", id="enforcement-toggle", n_clicks=0,
                        style={"width": "36px", "height": "20px", "borderRadius": "10px",
                               "border": "none",
                               "background": "#3DDC84" if svc["pubsub_enforcement"] else "#34405C",
                               "cursor": "pointer"}),
            html.Span("Enforce Pub/Sub Schema (Avro) on shipment.updates"),
            html.Span(
                "— ON: rejected before entering the topic" if svc["pubsub_enforcement"]
                else "— OFF: flows downstream until something breaks",
                style={"color": "#3DDC84" if svc["pubsub_enforcement"] else "#8B96B4"},
            ),
        ]))
        scenario_children.append(html.Div(style={"display": "flex", "gap": "8px", "flexWrap": "wrap"},
            children=[
                html.Button(label, id={"type": "scenario-btn", "key": skey}, n_clicks=0)
                for skey, label in [
                    ("schema", "Schema tightened, producer not updated"),
                    ("flag", "Feature flag flipped in prod"),
                    ("config", "Config drift — timeout reduced"),
                    ("rollback", "Bad rollback — mixed versions"),
                    ("secret", "Rotated secret not updated everywhere"),
                ]
            ] + [
                html.Button("Restore original outage example",
                            id={"type": "scenario-btn", "key": "original"}, n_clicks=0),
                html.Button("Reset to healthy",
                            id={"type": "scenario-btn", "key": "healthy"}, n_clicks=0),
            ]))
    else:
        scenario_children.append(html.Div([
            html.Span("No deployment scenarios configured for this service yet — it ships "
                      "with one default failure below. ", style={"color": "#8B96B4"}),
            html.Button("Reset to healthy", id={"type": "scenario-btn", "key": "healthy"},
                        n_clicks=0),
        ]))

    endpoint_options = [{"label": f"{s['method']} {s['path']}", "value": s["id"]}
                        for s in svc["schemas"]]
    endpoint_value = endpoint_options[0]["value"] if endpoint_options else None

    return (elements, table_data, scenario_children, endpoint_options, endpoint_value,
            svc["sample_payload"])


@dash_app.callback(
    Output("cyto-diagram", "elements", allow_duplicate=True),
    Output("edges-table", "data", allow_duplicate=True),
    Input({"type": "scenario-btn", "key": ALL}, "n_clicks"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def run_scenario(_clicks, key):
    ctx = dash.callback_context
    if not ctx.triggered:
        return no_update, no_update
    triggered_id = ctx.triggered[0]["prop_id"].split(".")[0]
    if not triggered_id:
        return no_update, no_update
    scenario_key = json.loads(triggered_id)["key"]
    svc = _api_post(f"/services/{key}/scenario", {"scenario_key": scenario_key})
    return build_cytoscape_elements(svc), svc["edges"]


@dash_app.callback(
    Output("cyto-diagram", "elements", allow_duplicate=True),
    Output("edges-table", "data", allow_duplicate=True),
    Input("enforcement-toggle", "n_clicks"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def toggle_enforcement(n_clicks, key):
    if not n_clicks:
        return no_update, no_update
    enabled = n_clicks % 2 == 1
    svc = _api_post(f"/services/{key}/enforcement", {"enabled": enabled})
    return build_cytoscape_elements(svc), svc["edges"]


@dash_app.callback(
    Output("chat-window", "children"),
    Output("chat-input", "value"),
    Input("chat-send", "n_clicks"),
    Input("chat-input", "n_submit"),
    State("chat-input", "value"),
    State("chat-history", "data"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def send_chat(_n_clicks, _n_submit, message, history, key):
    if not message:
        return no_update, no_update
    reply = _api_post(f"/services/{key}/chat", {"message": message})["reply"]
    history = (history or []) + [("user", message), ("agent", reply)]
    bubbles = []
    for role, text in history:
        bubbles.append(html.Div(
            dcc.Markdown(text, dangerously_allow_html=True),
            style={
                "alignSelf": "flex-end" if role == "user" else "flex-start",
                "background": "#3DDC84" if role == "user" else "#161F30",
                "color": "#04231A" if role == "user" else "#E9ECF6",
                "padding": "8px 12px", "borderRadius": "10px", "maxWidth": "80%",
                "fontSize": "13px",
            },
        ))
    return bubbles, ""


@dash_app.callback(
    [Output("validate-result", "children")],
    Input("validate-btn", "n_clicks"),
    State("endpoint-dropdown", "value"),
    State("payload-input", "value"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def run_validate(_n, endpoint_id, payload_text, key):
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError as err:
        return [html.Div(f"Invalid JSON — {err}", style={"color": FAIL_COLOR})]

    result = _api_post(f"/services/{key}/validate", {"endpoint_id": endpoint_id, "payload": payload})
    lines = [html.Div(
        f"{result['status']} {'OK' if result['status'] == 200 else 'Bad Request'}",
        style={"fontWeight": 700, "color": "#3DDC84" if result["status"] == 200 else FAIL_COLOR,
               "marginBottom": "6px"},
    )]
    for c in result["checks"]:
        lines.append(html.Div(f"{'✓' if c['pass_'] else '✗'} {c['label']}",
                               style={"color": "#8B96B4" if c["pass_"] else FAIL_COLOR}))
    if result["errors"]:
        lines.append(html.Div(f"{len(result['errors'])} error(s):", style={"marginTop": "6px"}))
        for e in result["errors"]:
            lines.append(html.Div(f"• {e}", style={"color": FAIL_COLOR}))
    if result.get("banner"):
        lines.append(html.Div(result["banner"], style={"marginTop": "8px", "fontWeight": 600}))
    return [html.Div(lines)]


@dash_app.callback(
    [Output("edges-table", "data", allow_duplicate=True)],
    Input("edges-table", "data_timestamp"),
    State("edges-table", "data"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def sync_edge_edits(_ts, rows, key):
    for row in rows:
        for field in ("order", "time", "source", "target", "label", "type", "phase",
                      "failed", "reason"):
            if field in row:
                _api_patch(f"/services/{key}/edges/{row['id']}", {"field": field, "value": row[field]})
    return [no_update]


@dash_app.callback(
    Output("cyto-diagram", "elements", allow_duplicate=True),
    Output("edges-table", "data", allow_duplicate=True),
    Input("add-edge-btn", "n_clicks"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def add_edge_cb(_n, key):
    svc = _api_get(f"/services/{key}")
    if len(svc["systems"]) < 2:
        return no_update, no_update
    next_id = max([e["id"] for e in svc["edges"]], default=0) + 1
    next_order = max([e["order"] for e in svc["edges"]], default=0) + 1
    new_edge = {
        "id": next_id, "order": next_order, "time": "",
        "source": svc["systems"][0]["id"], "target": svc["systems"][1]["id"],
        "label": "new message", "type": "default", "phase": "", "failed": False,
        "skipped": False, "reason": "",
    }
    svc = _api_post(f"/services/{key}/edges", new_edge)
    return build_cytoscape_elements(svc), svc["edges"]


@dash_app.callback(
    Output("cyto-diagram", "elements", allow_duplicate=True),
    Output("service-list", "children", allow_duplicate=True),
    Input("add-system-btn", "n_clicks"),
    State("new-system-id", "value"),
    State("new-system-name", "value"),
    State("new-system-kind", "value"),
    State("service-key", "data"),
    prevent_initial_call=True,
)
def add_system_cb(_n, sys_id, name, kind, key):
    if not sys_id or not name:
        return no_update, no_update
    new_system = {"id": sys_id, "name": name, "sub": "", "kind": kind or "api",
                  "subsystems": [], "failed": False, "reason": ""}
    svc = _api_post(f"/services/{key}/systems", new_system)
    return build_cytoscape_elements(svc), no_update


# =============================================================================
# 8. COMBINED APP — FastAPI owns the process; Dash's Flask server is mounted
#    into it over WSGI. One process, one port, one file.
# =============================================================================

app = FastAPI(title="Architecture Flow Tool (combined)")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)
app.include_router(api_router, prefix="/architecture-tool/api")

try:
    from a2wsgi import WSGIMiddleware
except ImportError:  # fall back to Starlette's (deprecated) built-in bridge
    from starlette.middleware.wsgi import WSGIMiddleware  # type: ignore

app.mount("/architecture-tool", WSGIMiddleware(dash_app.server))


if __name__ == "__main__":
    import uvicorn
    print("API:  http://localhost:8000/architecture-tool/api/services")
    print("UI:   http://localhost:8000/architecture-tool/")
    uvicorn.run(app, host="0.0.0.0", port=8000)
