// erase_event_mentions.cypher — an erased person's @-mention in others' events becomes the placeholder (same length, so quote offsets still hold) (F2, D125)
MATCH (e:Event)
WHERE e.artifact_id IN $ids
SET e.quote = replace(e.quote, $mention, $placeholder)
RETURN collect(DISTINCT e.product_id) AS products, count(e) AS events
