from __future__ import annotations

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from typing import Any

BOARD_REPOSITORY = os.getenv("AIOS_BOARD_REPOSITORY", "GK-studio-JP/ai-bulletin-board")
GITHUB_API = "https://api.github.com"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _request_json(method: str, url: str, payload: Any = None, headers: dict[str, str] | None = None) -> Any:
    request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return None if not raw else json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def _service_url(name: str) -> str:
    key = f"AIOS_{name.upper()}_URL"
    value = os.getenv(key)
    if not value:
        raise RuntimeError(f"missing environment variable {key}")
    return value.rstrip("/")


def _service_post(name: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    headers: dict[str, str] = {}
    token = os.getenv("AIOS_SERVICE_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _request_json("POST", _service_url(name) + path, payload, headers)


def _github_headers() -> dict[str, str]:
    token = os.getenv("AIOS_GITHUB_TOKEN")
    if not token:
        raise RuntimeError("missing environment variable AIOS_GITHUB_TOKEN")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _github(method: str, path: str, payload: Any = None) -> Any:
    return _request_json(method, GITHUB_API + path, payload, _github_headers())


def _board_path(suffix: str) -> str:
    owner, repo = BOARD_REPOSITORY.split("/", 1)
    return f"/repos/{owner}/{repo}{suffix}"


def _create_issue(title: str, body: str) -> dict[str, Any]:
    return _github("POST", _board_path("/issues"), {"title": title, "body": body})


def _get_issue(number: int) -> dict[str, Any]:
    return _github("GET", _board_path(f"/issues/{number}"))


def _get_comments(number: int) -> list[dict[str, Any]]:
    value = _github("GET", _board_path(f"/issues/{number}/comments?per_page=100"))
    if not isinstance(value, list):
        raise RuntimeError("GitHub comments response must be a list")
    return value


def _event_comment(event: dict[str, Any]) -> str:
    return "<!-- ai-bb:v1 -->\n\`\`\`json\n" + json.dumps(event, ensure_ascii=False, indent=2) + "\n\`\`\`"


def _persist_event(number: int, proposal: dict[str, Any]) -> dict[str, Any]:
    event = proposal.get("event")
    if not isinstance(event, dict):
        raise RuntimeError("event proposal missing event")
    return _github("POST", _board_path(f"/issues/{number}/comments"), {"body": _event_comment(event)})


def _compile_context(issue: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    return _service_post(
        "context",
        "/api/context/compile",
        {
            "issue": issue,
            "comments": comments,
            "source_repository": BOARD_REPOSITORY,
        },
    )


def _scheduler_inputs(context: dict[str, Any], issue_number: int) -> tuple[dict[str, Any], dict[str, Any]]:
    row = context["scheduler_row"]
    capsule = context["capsule"]
    generated_at = capsule["generated_at"]
    view = {
        "schema": "ai-os-scheduler-view:v1",
        "authoritative": False,
        "repository": BOARD_REPOSITORY,
        "generated_at": generated_at,
        "runnable": [row],
    }
    manifest = {
        "schema": "ai-os-projection-manifest:v1",
        "authoritative": False,
        "repository": BOARD_REPOSITORY,
        "generated_at": generated_at,
        "tasks": [
            {
                "task": row["task"],
                "capsule": f"capsules/issue-{issue_number}.json",
                "fingerprint": capsule["fingerprint"],
                "content_digest": capsule["content_digest"],
                "through_comment_id": capsule["source"].get("through_comment_id"),
            }
        ],
    }
    return view, manifest


def _build_boot(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("dispatch_count") != 1:
        raise RuntimeError(f"expected one dispatch, got {plan.get('dispatch_count')}")
    dispatch = plan["dispatches"][0]
    context = dispatch.get("context")
    if not isinstance(context, dict):
        raise RuntimeError("dispatch missing Context Capsule reference")
    return {
        "schema": "ai-os-worker-boot:v1",
        "authoritative": False,
        "persist_required": True,
        "source_plan_fingerprint": plan["fingerprint"],
        "dispatch_count": 1,
        "dispatch": dispatch,
        "capsule": {
            "path": context["capsule"],
            "fingerprint": context["fingerprint"],
            "content_digest": context["content_digest"],
            "through_comment_id": context.get("through_comment_id"),
        },
    }


def _bundle_secret() -> bytes:
    secret = os.getenv("AIOS_RUN_BUNDLE_SECRET")
    if not secret:
        raise RuntimeError("missing environment variable AIOS_RUN_BUNDLE_SECRET")
    return secret.encode("utf-8")


def sign_bundle(bundle: dict[str, Any]) -> str:
    return "sha256:" + hmac.new(_bundle_secret(), _json_bytes(bundle), hashlib.sha256).hexdigest()


def verify_bundle(bundle: dict[str, Any], signature: str) -> None:
    expected = sign_bundle(bundle)
    if not hmac.compare_digest(expected, signature):
        raise RuntimeError("invalid run bundle signature")


def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = payload.get("project_id")
    if not project_id:
        query = payload.get("project") or payload.get("query")
        if not query:
            raise ValueError("project_id or project/query is required")
        resolved = _service_post("projects", "/api/projects/resolve", {"query": str(query)})
        project_id = resolved["project_id"]

    objective = str(payload["objective"]).strip()
    if not objective:
        raise ValueError("objective must not be empty")
    worker_id = str(payload["worker_id"]).strip()
    if not worker_id:
        raise ValueError("worker_id must not be empty")

    project_task = _service_post(
        "projects",
        "/api/projects/task",
        {
            "project_id": str(project_id),
            "objective": objective,
            "priority": int(payload.get("priority", 50)),
            "acceptance": payload.get("acceptance", []),
        },
    )
    issue_spec = project_task["issue"]
    issue = _create_issue(issue_spec["title"], issue_spec["body"])
    issue_number = int(issue["number"])
    comments: list[dict[str, Any]] = []

    context = _compile_context(issue, comments)
    view, manifest = _scheduler_inputs(context, issue_number)
    plan = _service_post(
        "scheduler",
        "/api/scheduler/plan",
        {"view": view, "manifest": manifest, "limit": 1, "process": context["scheduler_row"]["process"]},
    )
    validation = _service_post("kernel", "/api/kernel/validate-dispatch", {"plan": plan})
    if validation.get("valid") is not True:
        raise RuntimeError(f"kernel rejected dispatch: {validation.get('errors')}")

    boot = _build_boot(plan)
    capsule = context["capsule"]
    preflight = _service_post(
        "runtime",
        "/api/runtime/preflight",
        {"boot": boot, "capsule": capsule, "fresh": context["replay"], "worker_id": worker_id},
    )
    if preflight.get("status") == "CLAIM_REQUIRED":
        _persist_event(issue_number, preflight["claim_proposal"])
        issue = _get_issue(issue_number)
        comments = _get_comments(issue_number)
        fresh_context = _compile_context(issue, comments)
        preflight = _service_post(
            "runtime",
            "/api/runtime/preflight",
            {"boot": boot, "capsule": capsule, "fresh": fresh_context["replay"], "worker_id": worker_id},
        )
    if preflight.get("status") != "READY":
        raise RuntimeError(f"runtime not ready: {preflight}")

    invocation = _service_post(
        "runtime",
        "/api/runtime/prepare",
        {"boot": boot, "capsule": capsule, "preflight": preflight, "driver": payload.get("driver", "external")},
    )
    bundle = {
        "schema": "ai-os-run-bundle:v1",
        "board_repository": BOARD_REPOSITORY,
        "issue_number": issue_number,
        "worker_id": worker_id,
        "boot": boot,
        "capsule": capsule,
        "invocation": invocation,
    }
    return {
        "schema": "ai-os-run-start:v1",
        "issue_number": issue_number,
        "issue_url": issue.get("html_url"),
        "project": project_task["project"],
        "invocation": invocation,
        "run_bundle": bundle,
        "run_signature": sign_bundle(bundle),
    }


def finish_run(payload: dict[str, Any]) -> dict[str, Any]:
    bundle = payload["run_bundle"]
    signature = str(payload["run_signature"])
    result = payload["result"]
    if not isinstance(bundle, dict) or not isinstance(result, dict):
        raise ValueError("run_bundle and result must be objects")
    verify_bundle(bundle, signature)

    issue_number = int(bundle["issue_number"])
    invocation = bundle["invocation"]
    outcome = _service_post(
        "runtime",
        "/api/runtime/normalize",
        {"invocation": invocation, "result": result},
    )

    issue = _get_issue(issue_number)
    comments = _get_comments(issue_number)
    fresh_context = _compile_context(issue, comments)
    gated = _service_post(
        "runtime",
        "/api/runtime/gate",
        {"boot": bundle["boot"], "fresh": fresh_context["replay"], "outcome": outcome},
    )
    if gated.get("eligible_for_persistence") is not True:
        raise RuntimeError(f"runtime gate rejected result: {gated}")
    _persist_event(issue_number, gated["event_proposal"])

    final_issue = _get_issue(issue_number)
    final_comments = _get_comments(issue_number)
    final_context = _compile_context(final_issue, final_comments)
    return {
        "schema": "ai-os-run-finish:v1",
        "issue_number": issue_number,
        "issue_url": final_issue.get("html_url"),
        "outcome": outcome,
        "gate": gated,
        "replay": final_context["replay"],
    }
