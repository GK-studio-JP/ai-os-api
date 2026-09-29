from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from orchestrator import finish_run, start_run

app = FastAPI(title="AIOS Orchestration API", version="1")


def _authorize(authorization: str | None) -> None:
    token = os.getenv("AIOS_API_TOKEN")
    if not token:
        raise HTTPException(status_code=503, detail="AIOS_API_TOKEN is not configured")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "ai-os-api"}


@app.post("/api/runs")
def create_run(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return start_run(payload)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/runs/finish")
def complete_run(
    payload: dict[str, Any],
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    _authorize(authorization)
    try:
        return finish_run(payload)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
