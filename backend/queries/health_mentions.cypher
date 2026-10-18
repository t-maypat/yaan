// health_mentions.cypher — MENTIONS edges per target label (S10)
MATCH (:Artifact)-[:MENTIONS]->(e:Entity)
RETURN labels(e) AS labels, count(*) AS n
ORDER BY n DESC
LIMIT 20
