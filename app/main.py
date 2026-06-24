import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from app.models import StoredRequest
from app.storage import store_request, get_requests, clear_requests, get_request_by_id
from app.config import load_config, reload_config, match_rule, get_rules
from app.auth import evaluate_auth

HERE = Path(__file__).parent
TEMPLATE_PATH = HERE / "templates" / "index.html"
MANAGEMENT_PATHS = {"/"}
MANAGEMENT_PREFIXES = ("/__",)

app = FastAPI(title="Request Catcher")

load_config()


@app.middleware("http")
async def capture_and_intercept(request: Request, call_next):
    path = request.url.path
    is_management = (
        path in MANAGEMENT_PATHS
        or any(path.startswith(p) for p in MANAGEMENT_PREFIXES)
    )

    if is_management:
        return await call_next(request)

    body_bytes = await request.body()
    body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else None

    rule = match_rule(
        request.method, path, dict(request.query_params), body_str
    )
    auth_status, auth_header, auth_values_count = evaluate_auth(
        rule, dict(request.headers)
    )

    sr = StoredRequest(
        id=0,
        timestamp=datetime.now(),
        method=request.method,
        path=path,
        query_params=dict(request.query_params),
        headers=dict(request.headers),
        body=body_str,
        client_host=request.client.host if request.client else None,
        auth_status=auth_status,
        auth_header=auth_header,
        auth_values_count=auth_values_count,
    )
    store_request(sr)

    if rule is None:
        return JSONResponse(
            {
                "message": "Request captured but no rule matched. Add a rule to responses.yaml.",
                "path": path,
                "method": request.method,
            }
        )

    if auth_status in ("missing", "invalid"):
        if rule.on_auth_failure is not None:
            return Response(
                content=rule.on_auth_failure.body,
                status_code=rule.on_auth_failure.status_code,
                headers=rule.on_auth_failure.headers,
            )
        # defaults
        return Response(
            content='{"error":"unauthorized"}',
            status_code=401,
            headers={"Content-Type": "application/json"},
        )

    if rule.delay_ms > 0:
        await asyncio.sleep(rule.delay_ms / 1000)
    return Response(
        content=rule.body,
        status_code=rule.status_code,
        headers=rule.headers,
    )


@app.get("/__requests")
async def list_requests():
    return JSONResponse(
        [r.model_dump(mode="json") for r in get_requests()]
    )


@app.get("/__requests/{request_id}")
async def get_request(request_id: int):
    r = get_request_by_id(request_id)
    if not r:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(r.model_dump(mode="json"))


@app.delete("/__requests")
async def delete_requests():
    clear_requests()
    return JSONResponse({"ok": True})


@app.get("/__config")
async def get_config():
    return JSONResponse(
        {
            "rules": [
                {
                    "path": r.path,
                    "path_pattern": r.path_pattern,
                    "method": r.method,
                    "status_code": r.status_code,
                    "headers": r.headers,
                    "body": r.body,
                    "delay_ms": r.delay_ms,
                    "auth": r.auth.model_dump() if r.auth else None,
                    "on_auth_failure": (
                        r.on_auth_failure.model_dump()
                        if r.on_auth_failure else None
                    ),
                    "match": r.match,
                }
                for r in get_rules()
            ]
        }
    )


@app.post("/__config/reload")
async def reload_config_endpoint():
    try:
        reload_config()
    except ValidationError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True, "rules_count": len(get_rules())})


@app.get("/", response_class=HTMLResponse)
async def index():
    template = TEMPLATE_PATH.read_text()
    return HTMLResponse(template)
