"""S9 Derived layers. WORKS_ON built in Phase 8.0 (R1-2); the rest in Phase 10.

WORKS_ON is computed inside FalkorDB in one statement: for every product, team and stage,
the first and last time a member of the team authored one of the product's artifacts. The
stage is the channel's (planning / dev / support), else the artifact kind (pr, doc,
meeting). The authority policy and the Sentinel's blast radius read it.
Still to come (Phase 10): KNOWS (degree-of-authorship + recency), COLLAB,
Company-USES-Product, PRECEDENT_FOR, truck factor.
Spec: docs/CONTEXT.md §9.3 (S9), §9.7.
"""

from app import db

MIN_ARTIFACTS = 3  # a team with one stray message isn't working on the product


def works_on(graph: str | None = None, min_artifacts: int = MIN_ARTIFACTS) -> dict:
    result = db.run(
        "derive_works_on", {"min_artifacts": min_artifacts}, graph=graph, readonly=False
    )
    return {"rows": 0, **result.stats}
