// link_in_area_issue.cypher — (Issue)-[:IN_AREA]->(Area): the component area an issue was reported in
UNWIND $rows AS r
MATCH (i:Issue {id: r.issue_id})
MATCH (a:Area {id: r.area_id})
MERGE (i)-[e:IN_AREA]->(a)
SET e.run = r.run
