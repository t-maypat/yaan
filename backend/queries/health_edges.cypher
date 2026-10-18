// health_edges.cypher — edge count per relationship type
MATCH ()-[e]->()
RETURN type(e) AS type, count(e) AS n
ORDER BY n DESC
LIMIT 100
