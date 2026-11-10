---
Subject: AI Agent & LLM Readability Audit for Raycast (raycast.com)
Target: Founders / DevRel / Engineering Leads at Raycast
Actor Used: https://apify.com/fragthom/llms-txt-agent-packager
---

Hey Raycast Team,

I ran an automated AI-agent readiness audit on raycast.com using our Apify Standby Auditor.

Here is what we discovered:
• Existing /llms.txt: ❌ Missing (AI search engines like Perplexity & ChatGPT Search scrape bloated HTML instead)
• AI Crawlers Policy: 0 allowed, 0 blocked in robots.txt (None blocked)
• Homepage Context Budget: ~3,065 tokens (4.35% text-to-code density)
• Top Remediation: Publish /llms.txt; Publish an OpenAPI description

We automatically generated a clean, ready-to-deploy `/llms.txt` for Raycast mapped to your documentation endpoints.
You can view your generated file here:
https://github.com/frag70/llms-txt-agent-packager/blob/main/outbound_campaign/generated_llms_txt/raycast.com_llms.txt

If you'd like to automate your AI crawler monitoring or run batch audits on other domains:
👉 Run our Standby Actor on Apify: https://apify.com/fragthom/llms-txt-agent-packager

Best,
Thomas Fragrante
