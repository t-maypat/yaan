// health_isolated.cypher — nodes with no edge at all, per label set
MATCH (n)
WHERE NOT (n)--()
RETURN labels(n) AS labels, count(n) AS n
ORDER BY n DESC
LIMIT 100
