import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from app.models import StoredRequest
from app.storage import store_request, get_requests, clear_requests, get_request_by_id
from app.config import load_config, reload_config, match_rule, get_rules

HERE = Path(__file__).parent
TEMPLATE_PATH = HERE / "templates" / "index.html"
MANAGEMENT_PREFIXES = ("/__",)

app = FastAPI(title="Request Catcher")

load_config()


@app.middleware("http")
async def capture_and_intercept(request: Request, call_next):
    path = request.url.path
    is_management = any(path.startswith(p) for p in MANAGEMENT_PREFIXES)

    if not is_management:
        body_bytes = await request.body()
        body_str = body_bytes.decode("utf-8", errors="replace") if body_bytes else None

        sr = StoredRequest(
            id=0,
            timestamp=datetime.now(),
            method=request.method,
            path=path,
            query_params=dict(request.query_params),
            headers=dict(request.headers),
            body=body_str,
            client_host=request.client.host if request.client else None,
        )
        store_request(sr)

        rule = match_rule(request.method, path)
        if rule:
            if rule.delay_ms > 0:
                await asyncio.sleep(rule.delay_ms / 1000)
            return Response(
                content=rule.body,
                status_code=rule.status_code,
                headers=rule.headers,
            )

    return await call_next(request)


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
                }
                for r in get_rules()
            ]
        }
    )


@app.post("/__config/reload")
async def reload_config_endpoint():
    reload_config()
    return JSONResponse({"ok": True, "rules_count": len(get_rules())})


@app.get("/", response_class=HTMLResponse)
async def index():
    template = TEMPLATE_PATH.read_text()
    return HTMLResponse(template)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def catch_all(request: Request, path: str):
    """This handler is reached only when no matching rule intercepts and no static route matches."""
    body_bytes = await request.body()
    return JSONResponse(
        {
            "message": "Request captured but no rule matched. Add a rule to responses.yaml.",
            "path": f"/{path}",
            "method": request.method,
        }
    )
