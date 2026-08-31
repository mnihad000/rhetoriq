from __future__ import annotations

from datetime import datetime, timezone
import ipaddress
import os
import socket
from urllib.parse import urlparse

from fastapi import FastAPI, Header, HTTPException
from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

app = FastAPI(title="RhetoriQ isolated browser renderer", docs_url=None, redoc_url=None)


class RenderRequest(BaseModel):
    url: str
    timeout_ms: int = Field(default=20_000, ge=1_000, le=30_000)
    max_response_bytes: int = Field(default=2_000_000, ge=10_000, le=5_000_000)


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs without credentials are permitted.")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local"):
        raise ValueError("Local destinations are forbidden.")
    addresses = {item[4][0] for item in socket.getaddrinfo(hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise ValueError("Private or non-global destinations are forbidden.")


def _authorize(token: str | None) -> None:
    expected = os.environ.get("BROWSER_SERVICE_TOKEN", "")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="Invalid browser service token.")


@app.get("/health")
async def health(x_rhetoriq_browser_token: str | None = Header(default=None)) -> dict:
    _authorize(x_rhetoriq_browser_token)
    return {"status": "ready", "browser": "chromium", "isolation": "container"}


@app.post("/render")
async def render(request: RenderRequest, x_rhetoriq_browser_token: str | None = Header(default=None)) -> dict:
    _authorize(x_rhetoriq_browser_token)
    try:
        _validate_public_url(request.url)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(
            accept_downloads=False,
            java_script_enabled=True,
            service_workers="block",
            user_agent="RhetoriQ/0.2 (+isolated public evidence renderer)",
        )
        page = await context.new_page()

        async def guard_route(route) -> None:
            try:
                _validate_public_url(route.request.url)
                if route.request.resource_type in {"media", "font", "image"}:
                    await route.abort()
                else:
                    await route.continue_()
            except Exception:
                await route.abort()

        async def close_popup(popup) -> None:
            await popup.close()

        await page.route("**/*", guard_route)
        page.on("popup", close_popup)
        response = await page.goto(request.url, wait_until="domcontentloaded", timeout=request.timeout_ms)
        final_url = page.url
        _validate_public_url(final_url)
        html = await page.content()
        encoded = html.encode("utf-8")
        if len(encoded) > request.max_response_bytes:
            await browser.close()
            raise HTTPException(status_code=413, detail="Rendered page exceeded the configured size limit.")
        result = {
            "url": request.url,
            "final_url": final_url,
            "status_code": response.status if response else 200,
            "content_type": (await response.header_value("content-type")) if response else "text/html",
            "html": html,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        await context.close()
        await browser.close()
        return result
