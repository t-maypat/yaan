// upsert_orgs.cypher — org nodes from the hierarchy's `org` field
UNWIND $rows AS r
MERGE (o:Org {id: r.id})
SET o.name = r.name
