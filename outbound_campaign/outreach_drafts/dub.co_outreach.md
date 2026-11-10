---
Subject: AI Agent & LLM Readability Audit for Dub (dub.co)
Target: Founders / DevRel / Engineering Leads at Dub
Actor Used: https://apify.com/fragthom/llms-txt-agent-packager
---

Hey Dub Team,

I ran an automated AI-agent readiness audit on dub.co using our Apify Standby Auditor.

Here is what we discovered:
• Existing /llms.txt: ❌ Missing (AI search engines like Perplexity & ChatGPT Search scrape bloated HTML instead)
• AI Crawlers Policy: 0 allowed, 0 blocked in robots.txt (None blocked)
• Homepage Context Budget: ~5,896 tokens (3.26% text-to-code density)
• Top Remediation: Fix llms.txt spec violations

We automatically generated a clean, ready-to-deploy `/llms.txt` for Dub mapped to your documentation endpoints.
You can view your generated file here:
https://github.com/frag70/llms-txt-agent-packager/blob/main/outbound_campaign/generated_llms_txt/dub.co_llms.txt

If you'd like to automate your AI crawler monitoring or run batch audits on other domains:
👉 Run our Standby Actor on Apify: https://apify.com/fragthom/llms-txt-agent-packager

Best,
Thomas Fragrante
