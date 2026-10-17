// set_person_pagerank.cypher — store each person's COLLAB PageRank score and rank (10.4)
UNWIND $rows AS r
MATCH (p:Person {id: r.id})
SET p.pagerank = r.score, p.pagerank_rank = r.rank
