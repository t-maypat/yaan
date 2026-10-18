"""S7 Entity resolution. Built in Phase 8.

ids -> dictionary -> fuzzy -> embedding similarity -> LLM tiebreak (cached) for
leftovers; feature/issue mentions clustered per product into canonical Feature/Issue
nodes with aliases. Spec: docs/CONTEXT.md §9.3 (S7).
"""
