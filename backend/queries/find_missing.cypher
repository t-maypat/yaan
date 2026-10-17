// find_missing.cypher — absence of a path, by kind: resolved issues no PR FIXES, open issues no PR FIXES, issues never ASSIGNED_TO anyone, own PRs nobody REVIEWED, areas only one author holds (no backup); each row with an artifact to cite (tool 8 find_missing, R2)
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)
WHERE $kind = 'resolved_without_fix' AND i.current_status = 'resolved' AND NOT (i)<-[:FIXES]-(:PR)
WITH i
OPTIONAL MATCH (i)-[:REPORTED_IN]->(m:Artifact)
WITH i, collect(m.id)[0] AS artifact_id
RETURN i.id AS id, i.title AS name, i.current_status AS status, i.component AS detail, artifact_id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)
WHERE $kind = 'open_without_fix' AND i.current_status IN ['reported', 'reopened']
  AND NOT (i)<-[:FIXES]-(:PR)
WITH i
OPTIONAL MATCH (i)-[:REPORTED_IN]->(m:Artifact)
WITH i, collect(m.id)[0] AS artifact_id
RETURN i.id AS id, i.title AS name, i.current_status AS status, i.component AS detail, artifact_id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(i:Issue)
WHERE $kind = 'issue_without_assignee' AND NOT (i)-[:ASSIGNED_TO]->(:Person)
WITH i
OPTIONAL MATCH (i)-[:REPORTED_IN]->(m:Artifact)
WITH i, collect(m.id)[0] AS artifact_id
RETURN i.id AS id, i.title AS name, i.current_status AS status, i.component AS detail, artifact_id
LIMIT $limit
UNION ALL
MATCH (:Product {id: $product_id})<-[:IN_PRODUCT]-(pr:PR)
WHERE $kind = 'pr_without_review' AND coalesce(pr.external, false) = false
  AND NOT (pr)<-[:REVIEWED]-(:Person)
RETURN pr.id AS id, pr.title AS name, pr.current_status AS status, pr.url AS detail, pr.id AS artifact_id
LIMIT $limit
UNION ALL
MATCH (a:Area)-[:IN_PRODUCT]->(:Product {id: $product_id})
WHERE $kind = 'area_single_expert' AND size(coalesce(a.authors, [])) = 1
RETURN a.id AS id, a.name AS name, a.kind AS status, a.authors[0] AS detail, null AS artifact_id
LIMIT $limit
