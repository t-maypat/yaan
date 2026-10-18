"""Drafted actions and the approval write path. Built in Phase 11.

Ticket comment, doc correction or owner notification, each citing the new evidence
quote. Approval writes a decision trace (Decision{origin:'approved_action'},
DECIDED_BY, EVIDENCED_BY, CAUSED, PRECEDENT_FOR); dismissals are recorded too.
Spec: docs/CONTEXT.md §9.5.
"""
