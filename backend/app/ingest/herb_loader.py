"""Raw HERB files (data/herb) -> typed records. Built in Phase 2.

Handles both Slack shapes (CONTEXT §6.2). Drops the product files' team / customers
fields, which are oracle-only (§6.3). Verified field names: docs/herb_format.md.
"""
