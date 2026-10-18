// doc_discussion.cypher — the Slack discussion of a document: messages in the channel where it was first shared, until a newer version is shared there (or $window_ms)
MATCH (d:Artifact {id: $doc_id})-[:IN_PRODUCT]->(:Product)<-[:IN_PRODUCT]-(v:Doc)
WHERE v.base_id = d.base_id
MATCH (s:Message)-[:SHARES]->(v)
WITH s ORDER BY s.ts_ms
WITH collect(s) AS shares
WITH shares[0] AS first, shares
MATCH (first)-[:IN_CHANNEL]->(ch:Channel)
WITH ch, first.ts_ms AS start,
     [x IN shares WHERE x.ts_ms > first.ts_ms AND x.channel_id = first.channel_id | x.ts_ms] AS later
WITH ch, start, CASE WHEN size(later) > 0 THEN later[0] ELSE start + $window_ms END AS stop
MATCH (m:Message)-[:IN_CHANNEL]->(ch)
WHERE m.ts_ms >= start AND m.ts_ms <= stop AND coalesce(m.system, false) = false
OPTIONAL MATCH (p:Person)-[:AUTHORED]->(m)
RETURN ch.name AS channel, m.id AS id, m.ts_iso AS ts_iso, m.text AS text,
       p.id AS person_id, p.name AS name, p.role AS role
ORDER BY m.ts_ms
LIMIT $limit
