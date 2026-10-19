// derived_sources.cypher — the source artifacts of derived nodes (issues, claims, decisions, alerts): a viewer may see a derived node only if they may see one of its sources (D100). A decision's source is its evidence; what it AFFECTS is its subject, and counts only for a structural decision read from that record. Each branch binds its node by id, then WITH, then traverses: without the WITH the planner folds the MATCHes into an edge scan per id (60x slower)
UNWIND $ids AS id
MATCH (i:Issue {id: id})
WITH id, i
MATCH (i)-[:REPORTED_IN]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (i:Issue {id: id})
WITH id, i
MATCH (c:Claim)-[:ABOUT]->(i)
MATCH (c)-[:FROM]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (c:Claim {id: id})
WITH id, c
MATCH (c)-[:FROM]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (d:Decision {id: id})
WITH id, d
MATCH (d)-[:EVIDENCED_BY]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (d:Decision {id: id})
WHERE d.origin = 'structural'
WITH id, d
MATCH (d)-[:AFFECTS]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (al:Alert {id: id})
WITH id, al
MATCH (al)-[:EVIDENCED_BY]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (al:Alert {id: id})
WITH id, al
MATCH (al)-[:FLAGS]->(:Decision)-[:EVIDENCED_BY]->(a:Artifact)
RETURN id, a.id AS source
LIMIT $limit
UNION ALL
UNWIND $ids AS id
MATCH (al:Alert {id: id})
WHERE al.kind = 'key_person' OR al.kind = 'bus_factor'
WITH id, al.subject_id AS person
MATCH (p:Person {id: person})
WITH id, p
MATCH (p)-[:AUTHORED]->(a:PR)
RETURN id, a.id AS source
LIMIT $limit
