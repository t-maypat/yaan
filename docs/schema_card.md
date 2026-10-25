# Yaan graph schema card

The compact schema the guarded `graph_query` tool (agent tool 14) shows the model, and a one-page map of the graph for people. `backend/app/agent/graph_query.py` reads the block between the markers below into the tool's description, so this file is the single source. Counts are from the 3 dev products (17 Oct). Graph model in full: CONTEXT §9.2.

<!-- card -->
Nodes (label: properties). Umbrella labels: Entity (people, products, customers, companies, teams, issues, areas), Artifact (everything with text).
- Product:Entity {id, name, aliases[], truck_factor, truck_people[]}
- Person:Entity {id 'eid_…', name, role, location, pagerank_rank, betweenness_rank}
- Team:Entity {id, name}; Org {id, name}; Community {id, size, top_team, label}
- Customer:Entity {id 'CUST-…', name, role}; Company:Entity {id, name}
- Artifact:Message {id 'msg:…', text, ts_ms, ts_iso, product_id, channel_id}; Artifact:Meeting {id 'mtg:…', title, text}; Artifact:MeetingChat {id 'chat:…'}; Artifact:Doc {id 'doc:…', title, doc_type, version, base_id, url}; Artifact:PR {id 'pr:…', number, title, text, url, repo, current_status, current_action, external}; Artifact:Link {id 'url:…', url, text}
- Channel {id, name, stage}
- Issue:Entity {id 'iss:…', title, component, current_status ('reported'|'resolved'|'dismissed'|'reopened'), reopen_count, reported_ms, reported_iso, product_id}
- Area:Entity {id 'area:…', name, kind ('component'|'work_item'), authors[] (person ids), product_id}
- Decision {id 'dec:…', topic, kind ('issue_status'|'pr_status'|'pr_action'|…), value, statement, valid_from_ms, valid_to_ms (null = in force), status ('active'|'superseded'), origin}
- Claim {id 'clm:…', topic, value, quote, ts_ms, ts_iso, outcome, authority}
- Alert {id, kind, severity, status, summary, product_id, ts_iso, quote, artifact_id}; Action {id, kind, status, draft}

Relationships (from)-[TYPE {properties}]->(to):
- (Person)-[AUTHORED]->(Artifact); (Person)-[REVIEWED {state, comment, ts_iso}]->(PR); (Person)-[ATTENDED]->(Meeting); (Person)-[SPOKE_IN {turns}]->(Meeting)
- (Artifact)-[IN_PRODUCT]->(Product); (Message)-[IN_CHANNEL]->(Channel); (Artifact)-[MENTIONS]->(Person|Customer|Product); (Message)-[SHARES {url}]->(Artifact); (PR)-[REVERTS]->(PR); (Artifact)-[HAS_CHUNK]->(Chunk)
- (Person)-[MEMBER_OF]->(Team); (Person)-[REPORTS_TO]->(Person); (Team)-[PART_OF]->(Org); (Team)-[WORKS_ON {stage, from_ms, to_ms}]->(Product); (Customer)-[WORKS_AT]->(Company); (Company)-[USES {issues}]->(Product)
- (Issue)-[IN_PRODUCT]->(Product); (Issue)-[REPORTED_BY]->(Customer); (Issue)-[REPORTED_IN]->(Message); (Issue)-[ASSIGNED_TO {from_ms, to_ms}]->(Person); (PR)-[FIXES]->(Issue); (Issue|PR)-[IN_AREA]->(Area); (Area)-[IN_PRODUCT]->(Product)
- (Person)-[KNOWS {author, score, deliveries}]->(Area); (Person)-[COLLAB {w, reviews, meetings, chats}]->(Person); (Person)-[IN_COMMUNITY]->(Community)
- (Decision)-[AFFECTS]->(Issue|PR); (Decision)-[SUPERSEDES]->(Decision) newer to older; (Decision)-[DECIDED_BY]->(Person); (Decision)-[EVIDENCED_BY {quote}]->(Artifact); (Decision)-[CAUSED]->(Decision); (Decision)-[PRECEDENT_FOR {similarity}]->(Decision)
- (Claim)-[SUPPORTS]->(Decision); (Claim)-[CONFLICTS_WITH {reason}]->(Decision); (Claim)-[FROM {quote}]->(Artifact); (Claim)-[ASSERTED_BY]->(Person); (Claim)-[ABOUT]->(Issue|PR)
- (Alert)-[FLAGS]->(Decision); (Alert)-[NOTIFIES {reasons}]->(Person); (Action)-[FOR]->(Alert)

FalkorDB rules: compare ids, never nodes (a.id = b.id); one label per node pattern, no (n:A|B); no regex; times are integer epoch ms (*_ms) plus *_iso strings; bounded paths only ([*..4]); shortestPath() only inside WITH or RETURN; no EXISTS {} subqueries (use WHERE (a)-[:R]->() patterns, then WITH before the next MATCH); group aggregates by variables or ids. Scope to a product with (:Product {id: $product_id}) or n.product_id. Always end with LIMIT.
<!-- /card -->

Example (answers "which reviewer sent most PRs back in VizForce?"):

```cypher
MATCH (:Product {id: 'VizForce'})<-[:IN_PRODUCT]-(pr:PR)<-[r:REVIEWED]-(p:Person)
WHERE r.state = 'CHANGES_REQUESTED'
WITH p, count(DISTINCT pr) AS n
RETURN p.id AS id, p.name AS name, n ORDER BY n DESC LIMIT 5
```
