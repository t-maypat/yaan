"""FalkorDB access. Built in Phase 1 (docs/PLAN.md).

Two clients: a writer for ingestion, and a read-only ACL user whose queries run through
GRAPH.RO_QUERY (agent tools and the guarded graph_query fallback). Queries are loaded
from backend/queries/<name>.cypher, always parameterized, always with a timeout.
Spec: docs/CONTEXT.md §3.6, §9.11.
"""
