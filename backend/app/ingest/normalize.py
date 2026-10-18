"""S1 Normalize. Built in Phase 2.

HERB records -> data/normalized/<product>/artifacts.jsonl, ordered by ts_ms. Row:
{id, kind, product_hint, channel_id, ts_ms, author_eid?, participants?, title?, text,
url?, raw_ref}. IDs per CONTEXT §9.2; times as UTC epoch ms plus ISO strings.
Spec: docs/CONTEXT.md §9.3 (S1).
"""
