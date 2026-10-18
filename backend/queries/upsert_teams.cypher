// upsert_teams.cypher — team nodes, one per VP group in the hierarchy (D026)
UNWIND $rows AS r
MERGE (t:Team:Entity {id: r.id})
SET t.name = r.name, t.org = r.org, t.lead_id = r.lead_id
