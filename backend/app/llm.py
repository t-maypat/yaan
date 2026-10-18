"""The single gateway for LLM calls. Built in Phase 4; Batch API path in Phase 7.

Anthropic SDK 1.x. Disk cache keyed by hash(model, prompt, schema version); token and
cost accounting with a daily spend cap; structured outputs validated against Pydantic
models; a clear error when credentials are missing. Load the claude-api skill first.
Spec: docs/CONTEXT.md §8; facts in docs/decisions.md (API facts).
"""
