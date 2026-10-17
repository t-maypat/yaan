// clear_collab.cypher — remove the COLLAB projection before it is rebuilt (10.1)
MATCH (:Person)-[c:COLLAB]->(:Person)
DELETE c
