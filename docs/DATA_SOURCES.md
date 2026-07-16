# RhetoriQ Data Sources

This document defines how RhetoriQ selects and retrieves source material. It separates an **evidence source** from the **transport** used to acquire it and records which connectors are implemented versus planned.

## Source policy

A publisher, government record, public speech, forum post, or social post is an evidence source. GDELT, a search API, RSS, or an HTML client is an acquisition transport.

Use transports in this order:

1. first-party APIs and official bulk datasets;
2. RSS, Atom, JSON Feed, webhooks, and public event streams;
3. terms-compatible search APIs for discovery;
4. direct canonical-page retrieval for evidence enrichment;
5. browser automation only for important sources that have no usable structured or HTTP interface.

The system must never imply that its earliest retrieved record is the true origin. The approved phrase is **first observed in the available dataset**.

## Status summary

| Provider or source class | Status | Transport | Role |
|---|---|---|---|
| GDELT DOC 2.0 | Implemented | Public JSON API | News discovery, metadata, and trend signals. |
| Hacker News | Implemented | Public Algolia API | Forum/community signals and linked-story discovery. |
| Canonical public pages | Implemented | Direct HTTP retrieval | Evidence enrichment after a URL is discovered. |
| Broad web search | Interface only | Search API | Discovery, corroboration, contradiction, provenance, official, and community lanes. |
| RSS/Atom | Planned | Publisher feeds | Incremental publisher monitoring. |
| Congress.gov | Planned | First-party public API | Bills, hearings, records, votes, members, and official legislative material. |
| Federal Register | Planned | First-party public API | Rules, notices, proposed rules, and presidential documents. |
| Bluesky | Planned | Jetstream/public AT Protocol stream | Public social activity and early narrative signals. |
| Reddit | Conditional | Official Data API | Community signals only after access and terms review. |
| YouTube | Conditional | First-party Data API | Video/channel discovery and metadata; transcript access requires separate validation. |
| NewsAPI | Optional supplement | Commercial API | Discovery only under a suitable production license. |
| C-SPAN | No assumed connector | No verified general public transcript API | Link or ingest only through an approved first-party or licensed interface. |

## Implemented connectors

### GDELT DOC 2.0

Implementation: `backend/services/gdelt.py`

RhetoriQ queries the DOC 2.0 API in article-list JSON mode. The connector currently records:

- canonical article URL;
- title;
- publisher domain;
- GDELT seen date;
- language and source country when available;
- query and dataset metadata;
- a heuristic RhetoriQ source classification.

GDELT article-list results do not provide a reliable full article body. The current document therefore uses the title as text/snippet and retains the canonical URL for later retrieval. This makes GDELT a strong discovery and trend source, not sufficient evidence for every claim by itself.

Operational requirements:

- handle HTTP 429 responses with bounded backoff;
- preserve the original GDELT timestamp and collection timestamp;
- deduplicate by canonical URL rather than query;
- expect duplicate and syndicated coverage;
- expose query coverage and retrieval failures in investigation limitations.

No API key is currently required.

### Hacker News via Algolia

Implementation: `backend/services/hn_ingestion.py`

The connector searches stories by query and date range. It records the HN object ID, title, author, points, comment count, publication timestamp, and either the linked story URL or the HN item URL.

HN is one community source, not a proxy for the whole public conversation. Its technical audience should be reflected in coverage limitations. Linked pages require canonical retrieval when their contents matter to a report.

No API key is currently required.

### Canonical-page retrieval

Implementation: `backend/services/page_fetcher.py` and `backend/services/document_normalizer.py`

The HTTP fetcher follows redirects, applies a timeout, validates HTML/XML content types, and can cache successful pages. The normalizer extracts a title, visible text, selected metadata, publication time, language, entities, and phrases.

Canonical retrieval is not a broad crawler. It is invoked for URLs already discovered through an authorized provider or supplied by the user.

Production additions should include:

- robots and source-policy checks;
- per-domain rate limits and circuit breakers;
- response-size limits;
- stronger article extraction and structured-data parsing;
- content hashes and parser versions;
- explicit paywall/access-control refusal;
- retention and revalidation policies.

## Next connectors

### Broad web search

The existing `SearchProvider` interface is the highest-priority gap. A production implementation should support query, date window, source-type hints, result limit, provider rank, snippet, canonical URL, and provider metadata.

Brave Search is the initial recommended candidate because it exposes a first-party independent web index and fits the current interface. Before implementation, choose a plan that permits the required caching or storage. Search results should normally be treated as discovery receipts; canonical pages remain the preferred evidence record.

Provider selection must remain configurable so RhetoriQ can add or replace discovery and enrichment providers without changing investigation logic.

### RSS and Atom

Feeds are preferred for monitored publishers because they offer stable item identifiers and incremental updates without crawling index pages.

The connector should:

- use `ETag` and `Last-Modified` when supported;
- checkpoint feed item IDs and publication timestamps;
- preserve feed URL, item GUID, and canonical article URL;
- tolerate malformed XML and date formats;
- fetch the article only when feed content is insufficient;
- monitor repeated failures and retired feed URLs.

Do not hard-code an outlet list as authoritative. Feed coverage should be configurable and reviewed for geographic, institutional, and ideological concentration.

### Official public records

Official material should come from first-party sources whenever possible.

Initial targets:

- Congress.gov API for legislative records, hearings, members, votes, and Congressional Record material;
- Federal Register API for rules, notices, proposed rules, and presidential documents;
- agency APIs, official newsroom feeds, and official document repositories;
- state and local first-party APIs where investigations require them.

Official records are high-value evidence but do not replace independent reporting or community coverage. The retrieval planner should use them as a distinct source class.

### Bluesky

Jetstream provides JSON-encoded public AT Protocol events and supports collection/repository filtering. A production connector should checkpoint the event cursor, filter to the required record collections, resolve identities carefully, and retain stable URI/CID identifiers.

Public availability does not remove privacy, retention, or responsible-use obligations.

### Reddit

Use only approved official Reddit API access. Do not implement page scraping as a substitute for API authorization.

Before enabling the connector:

- confirm the intended commercial or research use is permitted;
- obtain the required OAuth credentials or agreement;
- document rate limits and allowed storage/display;
- implement deletion/removal synchronization;
- isolate user-generated content from model-training uses not explicitly permitted;
- preserve post/comment IDs and subreddit context without overstating demographic representativeness.

Reddit is a useful community signal, not “the most important source” and not proof that a narrative originated there.

### Video and speech sources

Use first-party metadata APIs and official transcripts where available. YouTube’s Data API can discover videos, channels, playlists, and caption-track metadata, but transcript download availability and authorization must be validated separately.

Do not assume a general public C-SPAN transcript API. C-SPAN content may be linked as evidence or integrated later through a documented, authorized interface. For official speech text, prefer government repositories, Congressional Record material, agency transcripts, and official newsroom feeds.

### NewsAPI

NewsAPI is optional and supplemental. Its development plan is not a production entitlement, and its terms restrict republishing copyrighted material and building a competing news database. If adopted, use it for licensed discovery and retain only what the selected plan permits.

GDELT plus a broad search provider and publisher feeds should be evaluated before adding this dependency.

## Provider evaluation checklist

Every proposed connector must document:

| Area | Required answer |
|---|---|
| Authority | Is this first-party, licensed, public-domain, or an aggregator? |
| Coverage | Which geography, languages, source classes, and time ranges are represented? |
| Freshness | Poll, stream, webhook, or batch cadence? |
| History | Is historical retrieval supported and complete? |
| Content | Metadata, snippet, full text, transcript, or media only? |
| Identity | Are stable source-native IDs available? |
| Terms | Is production/commercial use allowed? |
| Storage | What may be cached, retained, displayed, or redistributed? |
| Deletion | How are removals or corrections synchronized? |
| Limits | What quotas, concurrency, and retry rules apply? |
| Cost | Expected cost at development and production volume? |
| Evidence quality | Can the result support a claim, or is it discovery-only? |

## Adding a connector

1. Complete the provider evaluation checklist and record the approval decision.
2. Implement the source-specific client behind a connector or `SearchProvider` boundary.
3. Map records into the shared `Document` contract without encoding transport names as source types.
4. Preserve provider, query/cursor, source-native ID, canonical URL, timestamps, and collection metadata.
5. Add pagination, checkpoint, retry, rate-limit, and partial-failure behavior.
6. Add fixture-based tests for malformed records, duplicates, pagination, timestamps, and provider errors.
7. Define retention, deletion, and revalidation behavior.
8. Add health metrics and update this status table.
9. If Kafka is enabled, emit the versioned raw-document event described in [KAFKA.md](KAFKA.md).

## External references

- [GDELT DOC 2.0 API](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/)
- [Hacker News API documentation](https://hn.algolia.com/api)
- [Brave Search API](https://brave.com/search/api/)
- [Congress.gov API](https://api.congress.gov/)
- [Federal Register API](https://www.federalregister.gov/developers/documentation/api/v1)
- [Bluesky Jetstream](https://docs.bsky.app/blog/jetstream)
- [Reddit Data API terms](https://redditinc.com/policies/data-api-terms)
- [YouTube Data API](https://developers.google.com/youtube/v3/docs)
- [NewsAPI terms](https://newsapi.org/terms)

