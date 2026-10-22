// collab_messages_org.cypher — org mode's conversation part of COLLAB: who wrote each message, where, when and in which thread, to group into conversations (threads, else sessions) (F4, D127)
MATCH (p:Person)-[:AUTHORED]->(m:Message)
WHERE coalesce(m.system, false) = false
RETURN m.id AS message_id, m.channel_id AS channel_id, p.id AS person_id, m.ts_ms AS ts_ms, m.thread_id AS thread_id
LIMIT $limit
