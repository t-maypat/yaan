// set_channel_visibility.cypher — who may read a channel in org mode: 'public' (everyone signed in) or 'private' (its MEMBER_OF people); from Slack, or declared in connectors.yaml (H1, D109)
UNWIND $rows AS r
MATCH (c:Channel {id: r.id})
SET c.visibility = r.visibility, c.access_synced_ms = r.now_ms
RETURN count(c) AS n
