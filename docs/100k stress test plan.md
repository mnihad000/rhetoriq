  A good test has two parts:

  1. Corpus realism: real article text, timestamps, publishers,
     URLs/IDs, and political-news-relevant content. Hugging Face
     datasets are a reasonable source, provided the license
     allows your intended use and you retain provenance.

  2. Traffic realism: replay those documents at controlled rates
     through your actual API or connector boundary. Do not insert
     rows directly into PostgreSQL or publish only a simplified
     Kafka payload, since that skips the parts you are claiming
     to have tested.

  For the résumé claim, distinguish sustained capacity from burst
  capacity:

  - 100K documents/day = about 1.16 documents/second sustained.
  - Test a sustained run at slightly above that, such as 2–5
    docs/sec.

  - Also run bursts, such as 25–100 docs/sec, to show Kafka
    buffering and Flink catch-up behavior.

  - Use enough unique documents to avoid cache effects; if you
    reuse the corpus, vary document IDs and timestamps while
    preserving source text/provenance.

  Record: accepted documents, end-to-end completion rate, p50/
  p95/p99 latency, Kafka consumer lag, Flink throughput/
  checkpoint health, failure count, and final persisted/
  retrievable document count. Then you have evidence for
  “processed 100K+ daily documents”; you should only call it “low
  latency” if the recorded percentiles support that wording.
