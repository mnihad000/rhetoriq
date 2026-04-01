# RhetoriQ — Data Sources

> This document covers every data source RhetoriQ ingests. For each source: what it provides, how to get credentials, rate limits, data quirks, and exactly what fields we extract.

---

## Overview

RhetoriQ ingests from 5 data sources. Each source covers a different slice of the political information ecosystem:

| Source | What It Covers | Volume | Cost |
|---|---|---|---|
| Reddit API (PRAW) | Fringe to mainstream political discussion | ~50k posts/day | Free |
| GDELT Project | Global news events and narratives | ~100k events/day | Free |
| NewsAPI | English-language news articles | ~10k articles/day | Free tier available |
| C-SPAN API | Official political speech transcripts | ~20 transcripts/day | Free |
| RSS Feeds | Direct feeds from specific outlets | ~5k articles/day | Free |

The combination of these five sources is intentional. GDELT and NewsAPI cover mainstream narrative. Reddit covers fringe-to-mainstream pipeline. C-SPAN covers official political adoption. RSS feeds give us raw outlet-level data without API abstraction.

---

## 1. Reddit API (PRAW)

### What It Provides
Reddit is the most important source for detecting narratives early. Fringe political narratives almost always appear on Reddit before they reach mainstream media or politicians. PRAW (Python Reddit API Wrapper) gives us clean programmatic access.

### Subreddits We Monitor

#### High Volume Political
- r/politics
- r/worldnews
- r/news
- r/PoliticalDiscussion

#### Fringe / Early Signal
- r/conspiracy
- r/conservatives
- r/progressive
- r/Libertarian
- r/WayOfTheBern
- r/TheDonald (banned, use Pushshift historical data)

#### Meta / Media Criticism
- r/media_criticism
- r/propaganda

### How To Get Credentials
1. Go to https://www.reddit.com/prefs/apps
2. Click "create another app"
3. Select "script"
4. Fill in name and redirect URI (use `http://localhost:8080`)
5. Copy your `client_id` (under the app name) and `client_secret`

```env
REDDIT_CLIENT_ID=your_client_id
REDDIT_CLIENT_SECRET=your_client_secret
REDDIT_USER_AGENT=rhetoriq:v1.0 (by /u/yourusername)
```

### Rate Limits
- 60 requests per minute on the free tier
- PRAW handles rate limiting automatically — do not implement your own
- Use `prawcore.exceptions.RateLimitExceeded` handler as a safety net

### PRAW Setup
```python
import praw

reddit = praw.Reddit(
    client_id=os.getenv("REDDIT_CLIENT_ID"),
    client_secret=os.getenv("REDDIT_CLIENT_SECRET"),
    user_agent=os.getenv("REDDIT_USER_AGENT")
)

# Stream new posts from a subreddit
for submission in reddit.subreddit("politics").stream.submissions():
    # publish to Kafka raw.reddit topic
```

### Fields We Extract
```json
{
  "id": "submission.id",
  "source": "reddit",
  "source_id": "submission.id",
  "url": "submission.url",
  "title": "submission.title",
  "body": "submission.selftext",
  "author": "submission.author.name",
  "published_at": "datetime.fromtimestamp(submission.created_utc)",
  "metadata": {
    "subreddit": "submission.subreddit.display_name",
    "upvotes": "submission.score",
    "num_comments": "submission.num_comments",
    "upvote_ratio": "submission.upvote_ratio"
  }
}
```

### Data Quirks
- `selftext` is empty for link posts — use `title` only in that case
- Deleted posts return `[deleted]` or `[removed]` — filter these out
- Bot accounts are common — consider filtering authors with karma < 100
- Submissions stream may occasionally duplicate — use Redis bloom filter for deduplication

---

## 2. GDELT Project

### What It Provides
GDELT (Global Database of Events, Language, and Tone) is a free, real-time dataset that monitors the world's news media across 100+ languages. It is updated every 15 minutes and is specifically designed for tracking narrative and event propagation globally. This is RhetoriQ's most powerful data source for detecting cross-outlet narrative spread.

GDELT provides two datasets we use:
- **GDELT Event Database** — structured events extracted from news (who did what to whom)
- **GDELT Global Knowledge Graph (GKG)** — themes, emotions, and narrative threads extracted from articles

### How To Get Access
GDELT is completely free and requires no API key. Data is served as CSV files updated every 15 minutes on Google Cloud Storage.

```
# Latest 15-minute update
http://data.gdeltproject.org/gdeltv2/lastupdate.txt

# This file contains URLs to the latest GKG, Events, and Mentions CSV files
```

### Fetching GDELT Data
```python
import requests
import pandas as pd
from io import StringIO

def fetch_latest_gdelt():
    # Get the latest update manifest
    manifest = requests.get("http://data.gdeltproject.org/gdeltv2/lastupdate.txt").text
    
    # Parse the GKG file URL from the manifest
    gkg_url = [line.split()[-1] for line in manifest.strip().split('\n') 
               if 'gkg' in line][0]
    
    # Download and parse
    response = requests.get(gkg_url)
    # GDELT GKG is tab-separated
    df = pd.read_csv(StringIO(response.text), sep='\t', 
                     on_bad_lines='skip', header=None)
    return df
```

### Rate Limits
- No API key required, no rate limits
- Poll every 15 minutes maximum — files only update every 15 minutes anyway
- Be respectful — cache the manifest URL response

### Fields We Extract from GKG
```json
{
  "id": "generated uuid",
  "source": "gdelt",
  "source_id": "GKGRECORDID column",
  "url": "DOCUMENTIDENTIFIER column",
  "title": null,
  "body": "extracted from DOCUMENTIDENTIFIER fetch",
  "author": null,
  "published_at": "DATE column (parse YYYYMMDDHHMMSS format)",
  "metadata": {
    "themes": "THEMES column (semicolon separated)",
    "persons": "PERSONS column",
    "organizations": "ORGANIZATIONS column",
    "tone": "TONE column (comma separated scores)",
    "locations": "LOCATIONS column"
  }
}
```

### Data Quirks
- GDELT timestamps are in `YYYYMMDDHHMMSS` format — always parse explicitly
- The `THEMES` column uses GDELT's own taxonomy (e.g. `TAX_FNCACT_POLITICIAN`) — map these to plain English
- Tone score is a comma-separated string of 7 values — first value is overall tone (-100 to +100)
- Some URLs in GDELT are dead links — implement a timeout of 5 seconds on any URL fetch
- GDELT covers global news — filter by `SOURCECOUNTRY` or language if you want US-focused data only

---

## 3. NewsAPI

### What It Provides
NewsAPI aggregates articles from 150,000+ news sources worldwide. It gives us clean, structured article metadata without needing to scrape individual outlets. We use it primarily for mainstream US political news.

### How To Get Credentials
1. Go to https://newsapi.org/register
2. Create a free account
3. Copy your API key from the dashboard

```env
NEWS_API_KEY=your_api_key
```

### Free Tier Limits
- 100 requests per day on free tier
- 1 month historical data on free tier
- Developer plan ($449/month) for production volume — consider using GDELT as primary and NewsAPI as supplement

### Endpoints We Use

#### Everything endpoint — search all articles
```python
import requests

response = requests.get(
    "https://newsapi.org/v2/everything",
    params={
        "q": "politics OR congress OR senate OR president",
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 100,
        "apiKey": os.getenv("NEWS_API_KEY")
    }
)
```

#### Top headlines — breaking political news
```python
response = requests.get(
    "https://newsapi.org/v2/top-headlines",
    params={
        "category": "politics",
        "country": "us",
        "pageSize": 100,
        "apiKey": os.getenv("NEWS_API_KEY")
    }
)
```

### Fields We Extract
```json
{
  "id": "generated uuid",
  "source": "newsapi",
  "source_id": "article.url hashed",
  "url": "article.url",
  "title": "article.title",
  "body": "article.content",
  "author": "article.author",
  "published_at": "article.publishedAt",
  "metadata": {
    "outlet": "article.source.name",
    "outlet_id": "article.source.id",
    "description": "article.description"
  }
}
```

### Data Quirks
- `content` field is truncated to 200 characters on the free tier — use `description` as fallback
- `author` is frequently null — treat as optional
- Duplicate articles appear across sources — deduplicate by URL in Redis
- Some articles return `[Removed]` content — filter these

---

## 4. C-SPAN API

### What It Provides
C-SPAN provides transcripts and metadata for congressional hearings, floor speeches, press conferences, and campaign events. This is our ground truth for when a narrative officially enters formal political speech. A talking point appearing in a C-SPAN transcript is the final stage of the fringe-to-mainstream pipeline.

### How To Get Access
C-SPAN has a public API available at https://www.c-span.org/about/api/

```
Base URL: https://api.c-span.org/v1/
```

No API key is required for basic access. Register at https://www.c-span.org/about/api/ for higher rate limits.

```env
CSPAN_API_KEY=your_api_key  # optional but recommended
```

### Endpoints We Use

#### Recent programs
```python
response = requests.get(
    "https://api.c-span.org/v1/programs",
    params={
        "type": "speech,hearing,pressconference",
        "limit": 20,
        "apikey": os.getenv("CSPAN_API_KEY", "")
    }
)
```

#### Program transcript
```python
response = requests.get(
    f"https://api.c-span.org/v1/programs/{program_id}/transcript",
    params={"apikey": os.getenv("CSPAN_API_KEY", "")}
)
```

### Fields We Extract
```json
{
  "id": "generated uuid",
  "source": "cspan",
  "source_id": "program.id",
  "url": "program.url",
  "title": "program.title",
  "body": "full transcript text",
  "author": "primary speaker name",
  "published_at": "program.date",
  "metadata": {
    "program_type": "speech | hearing | pressconference",
    "speakers": ["list of all speakers"],
    "committee": "committee name if hearing",
    "duration_seconds": "program.duration"
  }
}
```

### Data Quirks
- Transcripts are not always available immediately — poll with a 1 hour delay after program air time
- Speaker attribution in transcripts uses `>>` prefix — parse accordingly
- Some transcripts are auto-generated captions and contain errors — treat NER results with lower confidence
- Long hearings can produce transcripts over 100,000 words — chunk these before embedding

---

## 5. RSS Feeds

### What It Provides
Direct RSS feeds from specific outlets give us raw article data without any API abstraction or aggregator filtering. We monitor a politically diverse set of outlets intentionally — cross-spectrum coverage is essential for detecting when a narrative jumps from partisan to mainstream.

### Outlets We Monitor

| Outlet | RSS URL | Lean |
|---|---|---|
| New York Times | https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml | Center-Left |
| Washington Post | https://feeds.washingtonpost.com/rss/politics | Center-Left |
| Fox News | https://moxie.foxnews.com/google-publisher/politics.xml | Right |
| Reuters | https://feeds.reuters.com/reuters/politicsNews | Center |
| BBC News | http://feeds.bbci.co.uk/news/politics/rss.xml | Center |
| Breitbart | https://feeds.feedburner.com/breitbart | Far-Right |
| The Hill | https://thehill.com/rss/syndicator/19110 | Center |
| Politico | https://www.politico.com/rss/politicopicks.xml | Center |

### Parsing RSS Feeds
```python
import feedparser
import hashlib

def parse_feed(url: str, outlet_name: str):
    feed = feedparser.parse(url)
    
    for entry in feed.entries:
        yield {
            "id": str(uuid.uuid4()),
            "source": "rss",
            "source_id": hashlib.md5(entry.link.encode()).hexdigest(),
            "url": entry.link,
            "title": entry.title,
            "body": entry.get("summary", ""),
            "author": entry.get("author", None),
            "published_at": entry.get("published", datetime.utcnow().isoformat()),
            "metadata": {
                "outlet": outlet_name,
                "tags": [tag.term for tag in entry.get("tags", [])]
            }
        }
```

### Data Quirks
- RSS `summary` fields are often truncated — full article text requires fetching the URL directly
- `published` date format varies by outlet — use `dateutil.parser.parse()` for robust parsing
- Some outlets (Breitbart) frequently change RSS URLs — monitor for 404s and update accordingly
- Paywalled articles (NYT, WaPo) will not return full body text — use summary only and flag in metadata

---

## Data Source Priority

When the agent is tracing a narrative's origin, sources are weighted by their position in the fringe-to-mainstream pipeline:

```
EARLIEST (most likely origin)          LATEST (mainstream adoption)
        │                                          │
        ▼                                          ▼
   Reddit fringe → Reddit mainstream → RSS fringe → GDELT → NewsAPI → C-SPAN
```

A narrative appearing first in C-SPAN and then in Reddit would be unusual and flagged as top-down rather than bottom-up — which is itself a significant finding worth highlighting in the investigation report.

---

## Adding A New Data Source

To add a new data source to RhetoriQ:

1. Create a new scraper in `backend/scrapers/your_source_scraper.py`
2. Follow the standard message schema defined in ARCHITECTURE.md
3. Publish to an existing `raw.*` topic or create a new one (update KAFKA.md)
4. Add credentials to `.env.example`
5. Add a Kubernetes deployment manifest in `k8s/manifests/`
6. Document the source in this file following the same format
7. Update the data source priority pipeline above if relevant
