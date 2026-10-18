// link_part_of.cypher — (Team)-[:PART_OF]->(Org)
UNWIND $rows AS r
MATCH (t:Team {id: r.team_id})
MATCH (o:Org {id: r.org_id})
MERGE (t)-[:PART_OF]->(o)
