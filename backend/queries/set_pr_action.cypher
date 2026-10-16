// set_pr_action.cypher — materialize a PR's current stated action (to_merge / to_close / on_hold / reverted) and its reason from the `pr:<id>/action` chain
UNWIND $rows AS r
MATCH (a:Artifact {id: r.pr_id})
SET a.current_action = r.action, a.action_reason = r.reason, a.action_since_ms = r.since_ms
