-- A trimmed Devin CLI sessions.db: only the columns the ledger reads.
CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT NOT NULL, model TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, node_id INTEGER NOT NULL, parent_node_id INTEGER, chat_message TEXT NOT NULL, created_at INTEGER NOT NULL);
INSERT INTO sessions VALUES ('dev-1', '/tmp/dv', 'swe-2-medium', 1791100000);
INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES ('dev-1', 1,
  '{"role":"user","content":"synthetic"}', 1791100000);
INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES ('dev-1', 2,
  '{"role":"assistant","metadata":{"request_id":"r1","generation_model":"swe-2-medium","created_at":"2026-10-04T09:00:10.123456Z","metrics":{"input_tokens":100,"output_tokens":50,"cache_read_tokens":1000,"cache_creation_tokens":10}}}', 1791100010);
-- A compaction copy of r1: same request, same metrics.
INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES ('dev-1', 3,
  '{"role":"assistant","metadata":{"request_id":"r1","generation_model":"swe-2-medium","created_at":"2026-10-04T09:00:10.123456Z","metrics":{"input_tokens":100,"output_tokens":50,"cache_read_tokens":1000,"cache_creation_tokens":10}}}', 1791100500);
INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES ('dev-1', 4,
  '{"role":"assistant","metadata":{"request_id":"r2","generation_model":"claude-fable-5-1-high","created_at":"2026-10-04T09:01:00Z","metrics":{"input_tokens":20,"output_tokens":30,"cache_read_tokens":400}}}', 1791100060);
-- Still generating: no metrics yet.
INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES ('dev-1', 5,
  '{"role":"assistant","metadata":{"request_id":"r3","generation_model":"swe-2-medium","created_at":"2026-10-04T09:02:00Z"}}', 1791100120);
