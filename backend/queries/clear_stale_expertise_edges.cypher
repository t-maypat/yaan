// clear_stale_expertise_edges.cypher — the IN_AREA and KNOWS edges into a kept Area that this rebuild (run $run) didn't write: a person who no longer knows it, an issue moved to another area (D112)
MATCH ()-[e:IN_AREA|KNOWS]->(:Area)
WHERE coalesce(e.run, 0) <> $run
DELETE e
