// link_supersedes.cypher — (Decision)-[:SUPERSEDES]->(Decision), newer to older in one topic
UNWIND $rows AS r
MATCH (n:Decision {id: r.new_id})
MATCH (o:Decision {id: r.old_id})
MERGE (n)-[:SUPERSEDES]->(o)
