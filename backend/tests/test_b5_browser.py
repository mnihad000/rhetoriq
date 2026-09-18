"""Opt-in keyboard-first B5 browser coverage against a disposable service.

The test deliberately has no route mocks: a passing run is evidence that the
real frontend and a seeded disposable backend can complete the dashboard ->
investigation -> phrase span -> graph path -> reload flow. Unit tests cover
fallback and pending states separately.

Required environment for an executable run:

* ``RQ_B5_BROWSER_URL``: local frontend URL (for example, Vite on 5173).
* ``RQ_B5_DISPOSABLE_CONFIRMED=1``: explicit confirmation that the service is
  disposable and contains no production data.
* ``RQ_B5_INVESTIGATION_ID``: persisted investigation id exposed in the
  dashboard's recent-investigations list by the acceptance fixture.

``RQ_B5_PHRASE`` can override the seeded phrase and defaults to the phrase
used by the acceptance fixture.
"""

from __future__ import annotations

import os
from urllib.parse import unquote

import pytest


def _find_investigation_link(page, investigation_id: str):
    """Return the dashboard link for a seeded investigation, if present."""

    links = page.locator("a[href*='/investigation/']")
    encoded_id = investigation_id.replace(":", "%3A")
    for index in range(links.count()):
        href = links.nth(index).get_attribute("href") or ""
        if investigation_id in href or encoded_id in href or investigation_id in unquote(href):
            return links.nth(index)
    return None


def test_b5_dashboard_phrase_span_graph_path_reload_keyboard() -> None:
    base_url = os.getenv("RQ_B5_BROWSER_URL")
    if not base_url:
        pytest.skip("Set RQ_B5_BROWSER_URL to run the disposable browser gate.")
    if os.getenv("RQ_B5_DISPOSABLE_CONFIRMED") != "1":
        pytest.skip("Set RQ_B5_DISPOSABLE_CONFIRMED=1 for the disposable browser gate.")

    investigation_id = os.getenv("RQ_B5_INVESTIGATION_ID")
    if not investigation_id:
        pytest.skip("Set RQ_B5_INVESTIGATION_ID to the acceptance fixture investigation.")
    phrase = os.getenv("RQ_B5_PHRASE", "public records policy")
    playwright = pytest.importorskip("playwright.sync_api")

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"{base_url.rstrip('/')}/dashboard", wait_until="networkidle")

        link = _find_investigation_link(page, investigation_id)
        if link is None:
            browser.close()
            pytest.fail(
                "The disposable acceptance fixture must expose the seeded "
                "investigation in the dashboard recent-investigations list."
            )
        link.focus()
        page.keyboard.press("Enter")
        page.wait_for_url("**/investigation/**")

        page.get_by_role("tab", name="Evidence").focus()
        page.keyboard.press("Enter")
        page.get_by_label("Query").fill(phrase)
        with page.expect_response(
            lambda response: "/search?" in response.url and "mode=phrase" in response.url
        ) as search_response:
            page.get_by_label("Mode").select_option("phrase")
        search_payload = search_response.value.json()
        assert search_payload.get("results")
        assert search_payload["results"][0].get("spans")

        open_source = page.get_by_role("button", name="Open source").first
        open_source.wait_for(state="visible")
        open_source.focus()
        page.keyboard.press("Enter")
        assert page.get_by_role("dialog").is_visible()
        mark = page.get_by_role("dialog").locator("mark").first
        mark.wait_for(state="visible")
        assert mark.inner_text().strip()
        page.keyboard.press("Escape")
        assert not page.get_by_role("dialog").is_visible()

        page.get_by_role("tab", name="Narrative").focus()
        page.keyboard.press("Enter")
        from_document = page.get_by_label("From document")
        to_document = page.get_by_label("To document")
        from_options = from_document.locator("option")
        to_options = to_document.locator("option")
        assert from_options.count() > 1 and to_options.count() > 1
        from_value = from_options.nth(1).get_attribute("value")
        to_index = 2 if to_options.count() > 2 else 1
        to_value = to_options.nth(to_index).get_attribute("value")
        assert from_value and to_value and from_value != to_value
        from_document.select_option(from_value)
        to_document.select_option(to_value)
        find_path = page.get_by_role("button", name="Find path")
        find_path.focus()
        with page.expect_response(
            lambda response: "/provenance-paths?" in response.url and response.request.method == "GET"
        ) as path_response:
            page.keyboard.press("Enter")
        response = path_response.value
        assert response.ok
        payload = response.json()
        assert payload.get("source") == "neo4j" and not payload.get("fallback_active")
        assert payload.get("paths"), "The seeded recorded-reference pair must have an observed path."
        assert all(step["evidence_class"] == "observed" for path in payload["paths"] for step in path["steps"])
        page.get_by_text("Inspect a provenance path", exact=False).wait_for(state="visible")

        page.reload(wait_until="networkidle")
        assert page.get_by_role("heading", name="Observed relationships in this investigation").is_visible()
        browser.close()
