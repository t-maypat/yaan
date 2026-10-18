// list_indexes.cypher — every index in the graph with its kind, options and build status (lore schema checks these against 00_schema.cypher)
CALL db.indexes() YIELD label, properties, types, options, entitytype, status
RETURN label, properties, types, options, entitytype, status
ORDER BY label
LIMIT 500
