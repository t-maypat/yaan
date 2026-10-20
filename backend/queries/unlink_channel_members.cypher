// unlink_channel_members.cypher — a person left a private channel (or was removed): they stop seeing it (H1, D109)
UNWIND $rows AS r
MATCH (:Person {id: r.person_id})-[m:MEMBER_OF]->(:Channel {id: r.channel_id})
DELETE m
RETURN count(*) AS n
