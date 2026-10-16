// link_reported_by.cypher — (Issue)-[:REPORTED_BY]->(Customer): the customer a report names (CUST id)
UNWIND $rows AS r
MATCH (i:Issue {id: r.issue_id})
MATCH (c:Customer {id: r.customer_id})
MERGE (i)-[:REPORTED_BY]->(c)
