// clear_communities.cypher — remove the Community nodes and their IN_COMMUNITY edges before they are rebuilt (10.4)
MATCH (c:Community)
DETACH DELETE c
