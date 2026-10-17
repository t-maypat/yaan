// upsert_collab.cypher — (Person)-[:COLLAB {w, reviews, meetings, chats}]->(Person), both directions: how much two people worked together (10.1, D060)
UNWIND $rows AS r
MATCH (a:Person {id: r.a})
MATCH (b:Person {id: r.b})
MERGE (a)-[c:COLLAB]->(b)
SET c.w = r.w, c.reviews = r.reviews, c.meetings = r.meetings, c.chats = r.chats
