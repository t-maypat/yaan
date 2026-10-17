// person_pagerank.cypher — FalkorDB's native PageRank over the COLLAB projection: well-connected people, the "connectors" who can route a question when an area has no expert left (10.4)
CALL algo.pageRank('Person', 'COLLAB') YIELD node, score
RETURN node.id AS id, score
ORDER BY score DESC
LIMIT $limit
