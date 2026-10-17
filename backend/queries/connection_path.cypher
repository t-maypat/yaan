// connection_path.cypher — the shortest chain of people who work together (COLLAB, at most 6 hops) between two people, with each hop's tie: weight, shared reviews, meetings and chats (tool 13 connection_path, R2, D065)
MATCH (a:Person {id: $a})
MATCH (b:Person {id: $b})
WITH a, b
WITH shortestPath((a)-[:COLLAB*..6]->(b)) AS path
RETURN [n IN nodes(path) | {id: n.id, name: n.name, role: n.role}] AS people,
       [r IN relationships(path) | {w: r.w, reviews: r.reviews, meetings: r.meetings, chats: r.chats}] AS ties
LIMIT 1
