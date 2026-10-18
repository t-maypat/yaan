"""S5 LLM event extraction. Built in Phase 7.

Closed event schema (CONTEXT App. B) per unit (thread or channel-day, meeting segment,
doc section) with verbatim quotes, on the fast model via app.llm: structured output,
cached, Batch API for bulk runs. Spec: docs/CONTEXT.md §9.3 (S5), App. B-C.
"""
