// clear_uses.cypher — remove the derived Company-USES-Product edges before they are rebuilt (10.6)
MATCH (:Company)-[u:USES]->(:Product)
DELETE u
