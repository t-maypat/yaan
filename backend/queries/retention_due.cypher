// retention_due.cypher — items a tool's retention has passed: ids under its prefixes, older than the cutoff, oldest first (22.5, D138)
MATCH (a:Artifact)
WHERE a.ts_ms < $before_ms
WITH a
WHERE any(p IN $prefixes WHERE a.id STARTS WITH p)
RETURN a.id AS id, a.ts_ms AS ts_ms
ORDER BY a.ts_ms, a.id
LIMIT $limit
