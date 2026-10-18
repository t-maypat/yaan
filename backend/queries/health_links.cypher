// health_links.cypher — linking totals: mentions found, unresolved ones, artifacts with notes (S10)
MATCH (a:Artifact)
WHERE a.mention_count IS NOT NULL
RETURN toInteger(sum(a.mention_count)) AS mentions, toInteger(sum(size(a.unresolved))) AS unresolved, count(a) AS artifacts
LIMIT 1
