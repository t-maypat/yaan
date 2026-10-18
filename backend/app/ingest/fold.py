"""S8 Decision fold (deterministic). Built in Phase 8.

rebuild_topic(topic): order the topic's claims by ts_ms, apply app.policy and
decisiveness, emit the decision chain (validity windows, SUPERSEDES, SUPPORTS /
CONFLICTS_WITH) and materialize current_status. Idempotent; a late claim just
rebuilds its topic. Spec: docs/CONTEXT.md §9.3 (S8), §9.4.
"""
