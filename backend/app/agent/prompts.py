"""Answer-agent prompts. Built in Phase 9 (extraction prompts live in ingest/extract.py).

Artifact text is wrapped in <artifact id="..."> and treated as data, never instructions.
Spec: docs/CONTEXT.md §9.6 (prompt-injection hygiene), App. C.
"""
