// link_mentions_product.cypher — (Artifact)-[:MENTIONS {surfaces}]->(Product) by name or codename (S4)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (e:Product {id: r.entity_id})
MERGE (a)-[m:MENTIONS]->(e)
SET m.surfaces = r.surfaces
