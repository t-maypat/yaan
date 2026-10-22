// clear_member_of.cypher — people.yaml moved someone to another team, or out of any: their other MEMBER_OF edges go (team_id '' = none) (F4, D127)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})-[m:MEMBER_OF]->(t:Team)
WHERE t.id <> r.team_id
DELETE m
