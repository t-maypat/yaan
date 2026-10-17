// entity_card.cypher — one person, customer, company, product, issue or area by id: its labels, properties and relationship types (counted in the API), for the UI's entity panel
MATCH (n:Person {id: $id})
OPTIONAL MATCH (n)-[r]-()
WITH n, collect(type(r)) AS rels
RETURN labels(n) AS labels, properties(n) AS props, rels
LIMIT 1
UNION
MATCH (n:Customer {id: $id})
OPTIONAL MATCH (n)-[r]-()
WITH n, collect(type(r)) AS rels
RETURN labels(n) AS labels, properties(n) AS props, rels
LIMIT 1
UNION
MATCH (n:Company {id: $id})
OPTIONAL MATCH (n)-[r]-()
WITH n, collect(type(r)) AS rels
RETURN labels(n) AS labels, properties(n) AS props, rels
LIMIT 1
UNION
MATCH (n:Product {id: $id})
OPTIONAL MATCH (n)-[r]-()
WITH n, collect(type(r)) AS rels
RETURN labels(n) AS labels, properties(n) AS props, rels
LIMIT 1
UNION
MATCH (n:Issue {id: $id})
OPTIONAL MATCH (n)-[r]-()
WITH n, collect(type(r)) AS rels
RETURN labels(n) AS labels, properties(n) AS props, rels
LIMIT 1
UNION
MATCH (n:Area {id: $id})
OPTIONAL MATCH (n)-[r]-()
WITH n, collect(type(r)) AS rels
RETURN labels(n) AS labels, properties(n) AS props, rels
LIMIT 1
