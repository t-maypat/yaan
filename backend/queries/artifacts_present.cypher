// artifacts_present.cypher — which of these ids are artifacts in the graph: the live path's "already ingested?" and the demo's injected flags (D101)
UNWIND $ids AS id
MATCH (a:Artifact {id: id})
RETURN a.id AS id
LIMIT $limit
