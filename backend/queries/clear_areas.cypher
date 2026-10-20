// clear_areas.cypher — remove the Areas this expertise rebuild (run $run) didn't write, with their edges, after it wrote its own: readers never see the layer empty (10.0, D112)
MATCH (a:Area)
WHERE coalesce(a.run, 0) <> $run
DETACH DELETE a
