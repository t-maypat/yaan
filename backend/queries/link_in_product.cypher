// link_in_product.cypher — (Artifact)-[:IN_PRODUCT]->(Product)
UNWIND $rows AS r
MATCH (a:Artifact {id: r.artifact_id})
MATCH (p:Product {id: r.product_id})
MERGE (a)-[:IN_PRODUCT]->(p)
