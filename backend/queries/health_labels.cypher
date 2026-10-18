// health_labels.cypher — node count per label set
MATCH (n)
RETURN labels(n) AS labels, count(n) AS n
ORDER BY n DESC
LIMIT 100
