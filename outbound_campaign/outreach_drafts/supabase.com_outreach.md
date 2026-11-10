---
Subject: AI Agent & LLM Readability Audit for Supabase (supabase.com)
Target: Founders / DevRel / Engineering Leads at Supabase
Actor Used: https://apify.com/fragthom/llms-txt-agent-packager
---

Hey Supabase Team,

I ran an automated AI-agent readiness audit on supabase.com using our Apify Standby Auditor.

Here is what we discovered:
• Existing /llms.txt: ❌ Missing (AI search engines like Perplexity & ChatGPT Search scrape bloated HTML instead)
• AI Crawlers Policy: 0 allowed, 0 blocked in robots.txt (None blocked)
• Homepage Context Budget: ~6,010 tokens (1.06% text-to-code density)
• Top Remediation: Implement structured schema markup

We automatically generated a clean, ready-to-deploy `/llms.txt` for Supabase mapped to your documentation endpoints.
You can view your generated file here:
https://github.com/frag70/llms-txt-agent-packager/blob/main/outbound_campaign/generated_llms_txt/supabase.com_llms.txt

If you'd like to automate your AI crawler monitoring or run batch audits on other domains:
👉 Run our Standby Actor on Apify: https://apify.com/fragthom/llms-txt-agent-packager

Best,
Thomas Fragrante
