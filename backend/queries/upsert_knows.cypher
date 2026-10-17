// upsert_knows.cypher — (Person)-[:KNOWS {doa_abs, doa_norm, author, deliveries, first, discussion, score, first_ms, last_ms, refs}]->(Area): degree-of-authorship expertise with recency (10.2, §9.7)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (a:Area {id: r.area_id})
MERGE (p)-[k:KNOWS]->(a)
SET k.doa_abs = r.doa_abs, k.doa_norm = r.doa_norm, k.author = r.author,
    k.deliveries = r.deliveries, k.first = r.first, k.discussion = r.discussion,
    k.score = r.score, k.first_ms = r.first_ms, k.last_ms = r.last_ms, k.refs = r.refs
