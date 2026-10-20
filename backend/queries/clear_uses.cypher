// clear_uses.cypher — remove the derived Company-USES-Product edges this rebuild (run $run) didn't write, after it wrote its own: readers never see none (10.6, D112)
MATCH (:Company)-[u:USES]->(:Product)
WHERE coalesce(u.run, 0) <> $run
DELETE u
