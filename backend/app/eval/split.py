"""Dev/test product split and the held-out guard. Built in Phase 2.

Writes eval/split.json (seed, dev, test). Loading questions for a test product raises
unless the logged final held-out run is active. Spec: docs/CONTEXT.md §6.5.
"""
