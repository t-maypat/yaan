"""Citation validator (deterministic). Built in Phase 9.

Cited artifact ids must come from this turn's tool results; quotes must be normalized
substrings of the artifact text; every factual sentence needs a citation. One repair
round, then drop the sentence or abstain. Spec: docs/CONTEXT.md §9.6.
"""
