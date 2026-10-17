// link_in_community.cypher — (Person)-[:IN_COMMUNITY]->(Community): the working group a person belongs to (10.4)
UNWIND $rows AS r
MATCH (p:Person {id: r.person_id})
MATCH (c:Community {id: r.community_id})
MERGE (p)-[:IN_COMMUNITY]->(c)
