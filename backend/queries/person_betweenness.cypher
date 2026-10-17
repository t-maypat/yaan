// person_betweenness.cypher — FalkorDB's native betweenness centrality over COLLAB: the people who bridge otherwise separate groups (10.4, D064)
CALL algo.betweenness({nodeLabels: ['Person'], relationshipTypes: ['COLLAB']}) YIELD node, score
RETURN node.id AS id, score
ORDER BY score DESC
LIMIT $limit
