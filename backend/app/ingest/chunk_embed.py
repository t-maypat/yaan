"""S3 Chunk + embed. Built in Phase 4.

A message or thread = 1 chunk; meetings split by speaker turn (<= ~800 chars); docs
by heading/paragraph. Embeddings on Chunk nodes only (384-d local model); full-text
and vector indexes inside FalkorDB. Spec: docs/CONTEXT.md §9.3 (S3), §3.6 gotchas 7-8.
"""
