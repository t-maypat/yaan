// set_event_resolutions.cypher — store each issue event's resolution (the issue it names and how) for the next pass to reuse; a derived cache on an immutable event, never its words (D129)
UNWIND $rows AS r
MATCH (e:Event {id: r.id})
SET e.res_issue = r.issue, e.res_how = r.how, e.res_v = r.v
