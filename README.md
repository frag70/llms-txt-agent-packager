# LLMs.txt & Agentic Readability Packager (Standby API)

> **Zero-proxy Standby API auditing website readiness for AI crawlers (GPTBot, ClaudeBot, Perplexity) and generating production-ready `llms.txt` and `llms-full.txt` files.**

## Overview

AI search engines (Perplexity, ChatGPT Search, Gemini Answers) and autonomous coding agents (Claude, Codex, Antigravity) increasingly rely on standard `/llms.txt` files (per [llmstxt.org](https://llmstxt.org)) to consume documentation without crawling megabytes of bloated HTML.

This Apify Actor performs a comprehensive reverse audit of any domain and generates machine-ready documentation packs.

### Key Capabilities

- **LLMs.txt & LLMs-full.txt Detection & Validation:** Checks existing files for RFC compliance, Markdown header structure, and broken URLs.
- **AI Crawler Robots.txt Audit:** Simulates policy for 16 AI crawlers (`GPTBot`, `ClaudeBot`, `PerplexityBot`, `Google-Extended`, `CCBot`, `Applebot-Extended`, etc.).
- **Automatic `llms.txt` Generation:** Crawls sitemaps and key site sections to construct a compliant, ready-to-deploy `llms.txt`.
- **Token Economy & Context Budget:** Calculates token estimates and text-to-byte compression ratios.
- **Standby Pay-Per-Event (PPE):** Micro-priced persistence. Ready for instant MCP invocation with sub-second response times.

---

## Standby API Usage

When running in Standby Mode (`usesStandbyMode: true`):

```bash
# Health check probe
curl https://<ACTOR_URL>/healthz

# Audit domain via query string
curl "https://<ACTOR_URL>/?domain=stripe.com"

# Batch query
curl "https://<ACTOR_URL>/?domains=stripe.com,supabase.com"
```

## Batch Run Input

```json
{
  "runMode": "BATCH",
  "domain": "stripe.com",
  "maxPages": 4
}
```

## Pricing (Pay-Per-Event)

- **`actor-start`**: $0.0001 (Base HTTP invocation)
- **`site-audited`**: $0.005 (Full audit, crawler analysis & generated `llms.txt` per domain)
