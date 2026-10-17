// agent_company_r2.cypher — added on top of agent_company.cypher for the R2 tools: the issue's status chain as decisions (reported -> resolved, who decided, evidence, an alert), a second open issue nobody is assigned on the same component, two areas (one with no expert), KNOWS, COLLAB ties Ann - Bo - Cy, a team and a manager
MATCH (widget:Product {id: 'Widget'}), (bo:Person {id: 'eid_b2'}), (cy:Person {id: 'eid_c3'}), (ann:Person {id: 'eid_a1'}),
      (iss:Issue {id: 'iss:Widget:abc'}), (r1:Artifact {id: 'msg:r1'}), (r2:Artifact {id: 'msg:r2'}),
      (c1:Claim {id: 'clm:1'}), (c2:Claim {id: 'clm:2'}), (dee:Customer {id: 'CUST-0001'})
CREATE
  (i1:Decision {id: 'dec:iss:Widget:abc/status:8000', topic: 'iss:Widget:abc/status', kind: 'issue_status', value: 'reported', statement: 'Issue \'Slow export\' in Widget is reported', valid_from_ms: 8000, valid_to_ms: 8100, status: 'superseded', origin: 'extracted', confidence: 0.9, ts_inferred: false}),
  (i2:Decision {id: 'dec:iss:Widget:abc/status:8100', topic: 'iss:Widget:abc/status', kind: 'issue_status', value: 'resolved', statement: 'Issue \'Slow export\' in Widget is resolved', valid_from_ms: 8100, status: 'active', origin: 'extracted', confidence: 0.9, ts_inferred: false}),
  (i1)-[:AFFECTS]->(iss), (i2)-[:AFFECTS]->(iss), (i2)-[:SUPERSEDES]->(i1),
  (i1)-[:DECIDED_BY]->(bo), (i2)-[:DECIDED_BY]->(cy),
  (i1)-[:EVIDENCED_BY {quote: 'in the Export Module hurts CUST-0001'}]->(r1), (i2)-[:EVIDENCED_BY {quote: 'on it'}]->(r2),
  (c1)-[:SUPPORTS]->(i1), (c2)-[:SUPPORTS]->(i2),
  (iss2:Issue:Entity {id: 'iss:Widget:def', name: 'Broken chart', title: 'Broken chart', product_id: 'Widget', component: 'Export Module', current_status: 'reported', reopen_count: 0, reported_ms: 9500, reported_iso: '1970-01-01T00:00:09Z'}),
  (iss2)-[:IN_PRODUCT]->(widget), (iss2)-[:REPORTED_BY]->(dee),
  (j1:Decision {id: 'dec:iss:Widget:def/status:9500', topic: 'iss:Widget:def/status', kind: 'issue_status', value: 'reported', statement: 'Issue \'Broken chart\' in Widget is reported', valid_from_ms: 9500, status: 'active', origin: 'extracted', confidence: 0.9, ts_inferred: false}),
  (j1)-[:AFFECTS]->(iss2),
  (al:Alert {id: 'alert:x', kind: 'regression', severity: 'high', status: 'open', summary: 'Regression: Slow export', product_id: 'Widget', ts_ms: 9600, ts_iso: '1970-01-01T00:00:09Z', quote: 'on it', artifact_id: 'msg:r2', decision_id: 'dec:iss:Widget:abc/status:8100', subject_title: 'Slow export'}),
  (al)-[:FLAGS]->(i2), (al)-[:NOTIFIES {reasons: ['assigned']}]->(cy),
  (ar:Area:Entity {id: 'area:Widget:c:export-module', name: 'Export Module', kind: 'component', authors: ['eid_c3'], product_id: 'Widget', deliveries: 1}),
  (ar2:Area:Entity {id: 'area:Widget:c:charts', name: 'Charts', kind: 'component', authors: [], product_id: 'Widget', deliveries: 0}),
  (ar)-[:IN_PRODUCT]->(widget), (ar2)-[:IN_PRODUCT]->(widget),
  (cy)-[:KNOWS {author: true, score: 0.9, deliveries: 1}]->(ar), (bo)-[:KNOWS {author: false, score: 0.3, deliveries: 0}]->(ar),
  (bo)-[:COLLAB {w: 0.8, reviews: 1, meetings: 1, chats: 2}]->(cy), (cy)-[:COLLAB {w: 0.8, reviews: 1, meetings: 1, chats: 2}]->(bo),
  (ann)-[:COLLAB {w: 0.5, reviews: 0, meetings: 1, chats: 1}]->(bo), (bo)-[:COLLAB {w: 0.5, reviews: 0, meetings: 1, chats: 1}]->(ann),
  (t:Team:Entity {id: 'team:w', name: 'Widget Team'}), (bo)-[:MEMBER_OF]->(t), (cy)-[:MEMBER_OF]->(t), (bo)-[:REPORTS_TO]->(cy)
