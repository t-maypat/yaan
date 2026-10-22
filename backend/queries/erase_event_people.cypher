// erase_event_people.cypher — events that name an erased person as speaker or subject drop the id (F2, D125)
MATCH (e:Event)
WHERE e.person = $id OR e.speaker = $id
SET e.person = CASE WHEN e.person = $id THEN null ELSE e.person END,
    e.speaker = CASE WHEN e.speaker = $id THEN null ELSE e.speaker END
RETURN collect(DISTINCT e.product_id) AS products, count(e) AS events
