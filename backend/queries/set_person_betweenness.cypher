// set_person_betweenness.cypher — store each person's COLLAB betweenness score and rank (10.4)
UNWIND $rows AS r
MATCH (p:Person {id: r.id})
SET p.betweenness = r.score, p.betweenness_rank = r.rank
