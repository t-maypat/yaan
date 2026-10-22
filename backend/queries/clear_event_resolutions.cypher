// clear_event_resolutions.cypher — drop the stored resolution of events no longer in the pass (retracted since), so they stop counting as the last pass's (D129)
UNWIND $ids AS id
MATCH (e:Event {id: id})
SET e.res_issue = null, e.res_how = null, e.res_v = null
