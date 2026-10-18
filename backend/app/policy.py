"""Authority policy, Lore's "Bailiff". Built in Phase 8.

A pure, unit-tested function deciding who may change which topic kind, from facts
returned by queries/authority_facts.cypher. The LLM never decides authority.
Spec: docs/CONTEXT.md §9.4.
"""
