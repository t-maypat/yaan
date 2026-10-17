// collab_paths.cypher — the shortest COLLAB path (at most 4 hops) between each given pair of people: how a listed owner reaches the real expert, or how close a backup already is to a key person (10.5, D065)
UNWIND $pairs AS pair
MATCH (a:Person {id: pair.a})
MATCH (b:Person {id: pair.b})
WITH a, b
RETURN a.id AS a, b.id AS b, [n IN nodes(shortestPath((a)-[:COLLAB*..4]->(b))) | n.name] AS names,
       [n IN nodes(shortestPath((a)-[:COLLAB*..4]->(b))) | n.id] AS ids
LIMIT $limit
