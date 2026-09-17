"""Deterministic extraction of source references from collected HTML.

The extractor is intentionally conservative.  It only considers links in an
article/main/body reading region and a small set of structured reference
fields.  It never follows a URL, resolves DNS, or invents an offset when the
anchor text is absent or ambiguous in the normalized document text.
"""

from __future__ import annotations

from html.parser import HTMLParser
import html as html_module
import ipaddress
import json
import re
import unicodedata
from urllib.parse import urldefrag, urljoin, urlparse

from services.url_policy import has_embedded_credentials


EXTRACTION_VERSION = "b5-links-v1"
_EXCLUDED_TAGS = {"nav", "header", "footer", "script", "style", "noscript", "template", "aside", "form"}
_STRUCTURED_NAMES = {
    "citation",
    "citation_reference",
    "citation_url",
    "reference",
    "reference_url",
    "related_link",
    "related_url",
    "sameas",
}
_WHITESPACE = re.compile(r"\s+")


def _clean_text(value: str) -> str:
    return _WHITESPACE.sub(" ", unicodedata.normalize("NFC", html_module.unescape(value or ""))).strip()


def _safe_public_url(value: str, canonical_url: str) -> str | None:
    candidate = (value or "").strip()
    if not candidate:
        return None
    try:
        target, _fragment = urldefrag(urljoin(canonical_url, candidate))
        parsed = urlparse(target)
    except (ValueError, TypeError):
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or has_embedded_credentials(target):
        return None
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {
        "localhost",
        "localhost.localdomain",
        "host.docker.internal",
        "metadata.google.internal",
        "intranet",
        "internal",
    } or hostname.endswith((".local", ".internal")):
        return None
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None and (not ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved):
        return None
    return target


class _ReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[str] = []
        self._blocked = 0
        self._reading_depth = 0
        self._has_reading_region = False
        self._body_seen = False
        self._anchors: list[tuple[str, str, str]] = []
        self._structured: list[tuple[str, str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []
        self._visible_text: list[str] = []

    @property
    def anchors(self) -> list[tuple[str, str, str]]:
        return self._anchors

    @property
    def structured(self) -> list[tuple[str, str, str]]:
        return self._structured

    @property
    def visible_text(self) -> str:
        return _clean_text(" ".join(self._visible_text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = {key.lower(): value or "" for key, value in attrs}
        self._stack.append(tag)
        if tag in _EXCLUDED_TAGS:
            self._blocked += 1
        if tag == "body":
            self._body_seen = True
        if tag in {"article", "main"} and not self._blocked:
            self._reading_depth += 1
            self._has_reading_region = True
        if tag == "a" and self._allowed_content:
            self._anchor_href = attrs_map.get("href", "")
            self._anchor_text = []
        if tag in {"meta", "link"}:
            name = (attrs_map.get("name") or attrs_map.get("property") or attrs_map.get("rel") or "").lower().replace("-", "_")
            value = attrs_map.get("content") or attrs_map.get("href")
            if name in _STRUCTURED_NAMES and value:
                self._structured.append((value, _clean_text(value), "structured metadata"))
        if tag == "script" and attrs_map.get("type", "").lower() == "application/ld+json":
            # Capture JSON-LD in handle_data while the script is blocked.
            self._anchor_href = "__jsonld__"
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._anchor_href is not None:
            text = _clean_text(" ".join(self._anchor_text))
            if self._allowed_content and self._anchor_href:
                self._anchors.append((self._anchor_href, text, "hyperlink"))
            self._anchor_href = None
            self._anchor_text = []
        if tag == "script" and self._anchor_href == "__jsonld__":
            payload = _clean_text(" ".join(self._anchor_text))
            self._structured.extend(_jsonld_references(payload))
            self._anchor_href = None
            self._anchor_text = []
        if tag in {"article", "main"} and self._reading_depth:
            self._reading_depth -= 1
        if tag in _EXCLUDED_TAGS and self._blocked:
            self._blocked -= 1
        if self._stack:
            # HTMLParser is forgiving; close the nearest matching frame.
            if self._stack[-1] == tag:
                self._stack.pop()
            elif tag in self._stack:
                self._stack = self._stack[: len(self._stack) - 1 - self._stack[::-1].index(tag)]

    def handle_data(self, data: str) -> None:
        if self._anchor_href is not None:
            self._anchor_text.append(data)
        if self._allowed_content and data.strip():
            self._visible_text.append(data)

    @property
    def _allowed_content(self) -> bool:
        if self._blocked:
            return False
        return self._reading_depth > 0 or (self._body_seen and not self._has_reading_region)


def _jsonld_references(payload: str) -> list[tuple[str, str, str]]:
    if not payload:
        return []
    try:
        value = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    found: list[tuple[str, str, str]] = []

    def walk(item: object, field: str = "") -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                normalized = str(key).lower().replace("-", "_")
                if normalized in _STRUCTURED_NAMES:
                    walk(child, normalized)
                elif isinstance(child, (dict, list)):
                    walk(child, normalized)
        elif isinstance(item, list):
            for child in item:
                walk(child, field)
        elif isinstance(item, str) and field in _STRUCTURED_NAMES:
            found.append((item, _clean_text(item), f"JSON-LD {field}"))

    walk(value)
    return found


def _offsets(text: str, anchor_text: str) -> tuple[int | None, int | None, str | None]:
    candidate = _clean_text(anchor_text)
    if not candidate:
        return None, None, "empty anchor text"
    starts: list[int] = []
    cursor = text.find(candidate)
    while cursor >= 0:
        starts.append(cursor)
        cursor = text.find(candidate, cursor + 1)
    if len(starts) != 1:
        return None, None, "ambiguous anchor text offset" if starts else "anchor text not found in normalized text"
    start = starts[0]
    return start, start + len(candidate), None


def extract_source_references(
    html_text: str,
    canonical_url: str,
    normalized_text: str,
    limit: int = 100,
) -> tuple[list[dict], list[str]]:
    """Return stable source references and extraction limitations."""
    if limit < 1:
        return [], ["reference extraction disabled by limit"]
    parser = _ReferenceParser()
    # Decide body fallback before collecting links, rather than accepting
    # generic body links appearing before an article/main element.
    parser._has_reading_region = bool(re.search(r"<(?:article|main)(?:\s|>)", html_text or "", re.IGNORECASE))
    try:
        parser.feed(html_text or "")
        parser.close()
    except (ValueError, AssertionError):
        # HTMLParser normally recovers, but malformed input must not abort normalization.
        pass

    limitations: list[str] = []
    entries: list[dict] = []
    seen: set[str] = set()
    candidates = [*parser.anchors, *parser.structured]
    for raw_url, anchor_text, kind in candidates:
        target = _safe_public_url(raw_url, canonical_url)
        if target is None:
            if raw_url and raw_url != "__jsonld__":
                limitation = "unsafe or non-public reference URL skipped"
                if limitation not in limitations:
                    limitations.append(limitation)
            continue
        if target in seen:
            continue
        seen.add(target)
        start, end, reason = _offsets(normalized_text or "", anchor_text)
        if reason and reason not in limitations:
            limitations.append(reason)
        context = _clean_text(anchor_text)
        if start is not None and end is not None:
            left = max(0, start - 120)
            right = min(len(normalized_text), end + 120)
            context = _clean_text(normalized_text[left:right])
        entries.append({
            "target_url": target,
            "anchor_text": _clean_text(anchor_text),
            "context": context,
            "start": start,
            "end": end,
            "extraction_version": EXTRACTION_VERSION,
            "reference_kind": "hyperlink" if kind == "hyperlink" else "structured",
            "limitations": [reason] if reason else [],
        })
        if len(entries) >= max(0, limit):
            if len(candidates) > len(entries):
                limitations.append("reference extraction truncated at limit")
            break
    return entries, list(dict.fromkeys(limitations))
