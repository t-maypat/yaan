// link_in_area_pr.cypher — (PR)-[:IN_AREA]->(Area): the work item a pull request belongs to, or the component its fixed issue is in
UNWIND $rows AS r
MATCH (pr:Artifact {id: r.pr_id})
MATCH (a:Area {id: r.area_id})
MERGE (pr)-[e:IN_AREA]->(a)
SET e.run = r.run
