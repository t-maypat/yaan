"""S6 Quote verification. Built in Phase 7.

Keep an event only if its quote is a normalized (whitespace/case) substring of the
unit text; store char offsets; log the pass rate (target >= 95%).
Spec: docs/CONTEXT.md §9.3 (S6).
"""
