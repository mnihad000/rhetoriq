# Legacy SaaS Removal

The hackathon implementation included integrations with Browserbase, Arize, Band, Redis Cloud, Gemini, Groq, and third-party search providers. Browserbase, Arize, Band, Tavily, and SerpAPI have now been removed from the active codebase.

The agent query-planning and search-provider boundaries remain so model-native web search can be implemented as a separate, deliberate phase. Redis and model-provider decisions remain independent architecture work.

This document intentionally records the transition without presenting any of those providers as a product capability or endorsement.
