// fold_customers.cypher — every customer id: a report's customer links only to a known one (the fold's context, D101)
MATCH (c:Customer)
RETURN c.id AS id
LIMIT $limit
