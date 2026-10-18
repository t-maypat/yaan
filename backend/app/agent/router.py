"""Intent router. Built in Phase 9.

Cheap rules plus a small-model fallback -> lookup | people | aggregate | status/as-of |
why/lineage | expertise | impact | open-ended. Simple lookups go to search only.
Spec: docs/CONTEXT.md §9.6.
"""
