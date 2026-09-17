from services.source_references import extract_source_references


def test_extracts_article_links_and_excludes_navigation() -> None:
    html = """
    <html><body>
      <nav><a href="https://nav.example/menu">Menu</a></nav>
      <article><p>Read <a href="/sources/report#section">the report</a> for details.</p></article>
      <footer><a href="https://footer.example/about">About</a></footer>
    </body></html>
    """
    references, limitations = extract_source_references(
        html, "https://example.com/story", "Read the report for details."
    )
    assert [item["target_url"] for item in references] == ["https://example.com/sources/report"]
    assert references[0]["start"] == 5
    assert references[0]["end"] == 15
    assert references[0]["reference_kind"] == "hyperlink"
    assert not any("not found" in item for item in limitations)


def test_unicode_and_duplicate_links_are_deterministic() -> None:
    html = """<main>Über <a href="https://example.com/source">café résumé</a>.
    Again <a href="https://example.com/source">café résumé</a>.</main>"""
    references, limitations = extract_source_references(
        html, "https://example.com/story", "Über café résumé. Again café résumé."
    )
    assert len(references) == 1
    assert references[0]["start"] is None
    assert references[0]["end"] is None
    assert "ambiguous anchor text offset" in limitations


def test_unsafe_schemes_and_internal_host_literals_are_skipped() -> None:
    html = """<article>
      <a href="javascript:alert(1)">script</a>
      <a href="http://127.0.0.1/private">private</a>
      <a href="http://metadata.google.internal/metadata">metadata</a>
      <a href="https://public.example/item">public</a>
    </article>"""
    references, limitations = extract_source_references(
        html, "https://example.com/story", "script private metadata public"
    )
    assert [item["target_url"] for item in references] == ["https://public.example/item"]
    assert "unsafe or non-public reference URL skipped" in limitations


def test_structured_jsonld_reference_and_limit() -> None:
    html = """<article><p>Story text.</p></article>
    <script type="application/ld+json">{"reference":["https://example.org/source"]}</script>"""
    references, _ = extract_source_references(html, "https://example.com/story", "Story text.", limit=1)
    assert references[0]["reference_kind"] == "structured"
    assert references[0]["start"] is None
    assert references[0]["limitations"] == ["anchor text not found in normalized text"]


def test_truncation_is_reported() -> None:
    html = "<article>" + "".join(
        f'<a href="https://example.com/{index}">source {index}</a> ' for index in range(3)
    ) + "</article>"
    references, limitations = extract_source_references(html, "https://example.com/story", "source 0 source 1 source 2", limit=2)
    assert len(references) == 2
    assert "reference extraction truncated at limit" in limitations
