---
Subject: AI Agent & LLM Readability Audit for Linear (linear.app)
Target: Founders / DevRel / Engineering Leads at Linear
Actor Used: https://apify.com/fragthom/llms-txt-agent-packager
---

Hey Linear Team,

I ran an automated AI-agent readiness audit on linear.app using our Apify Standby Auditor.

Here is what we discovered:
• Existing /llms.txt: ❌ Missing (AI search engines like Perplexity & ChatGPT Search scrape bloated HTML instead)
• AI Crawlers Policy: 0 allowed, 0 blocked in robots.txt (None blocked)
• Homepage Context Budget: ~4,640 tokens (1.3% text-to-code density)
• Top Remediation: Publish an OpenAPI description; Add Organization JSON-LD

We automatically generated a clean, ready-to-deploy `/llms.txt` for Linear mapped to your documentation endpoints.
You can view your generated file here:
https://github.com/frag70/llms-txt-agent-packager/blob/main/outbound_campaign/generated_llms_txt/linear.app_llms.txt

If you'd like to automate your AI crawler monitoring or run batch audits on other domains:
👉 Run our Standby Actor on Apify: https://apify.com/fragthom/llms-txt-agent-packager

Best,
Thomas Fragrante
