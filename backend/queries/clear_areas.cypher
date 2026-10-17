// clear_areas.cypher — remove the derived expertise layer (Area nodes with their IN_AREA and KNOWS edges) before it is rebuilt; nothing else is touched
MATCH (a:Area)
DETACH DELETE a
