// fold_people.cypher — every person's role, for the authority policy (the fold's context, read from the graph, D101)
MATCH (p:Person)
RETURN p.id AS id, p.role AS role
LIMIT $limit
