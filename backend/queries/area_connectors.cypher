// area_connectors.cypher — people who collaborate (COLLAB) with anyone who knows the given areas, ranked by their PageRank: who to ask when no expert is left (10.7)
UNWIND $ids AS id
MATCH (a:Area {id: id})<-[:KNOWS]-(:Person)-[c:COLLAB]->(p:Person)
WITH p, sum(c.w) AS ties
RETURN p.id AS id, p.name AS name, p.role AS role, p.pagerank AS pagerank,
       p.pagerank_rank AS pagerank_rank, ties
ORDER BY pagerank DESC
LIMIT $limit
