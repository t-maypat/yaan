// link_issue_in_product.cypher — (Issue)-[:IN_PRODUCT]->(Product)
UNWIND $rows AS r
MATCH (i:Issue {id: r.issue_id})
MATCH (p:Product {id: r.product_id})
MERGE (i)-[:IN_PRODUCT]->(p)
