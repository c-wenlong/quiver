-- A trimmed OpenCode opencode.db: only the columns the ledger reads.
CREATE TABLE `session` (`id` text PRIMARY KEY, `directory` text NOT NULL, `time_updated` integer NOT NULL);
CREATE TABLE `message` (`id` text PRIMARY KEY, `session_id` text NOT NULL, `time_created` integer NOT NULL, `time_updated` integer NOT NULL, `data` text NOT NULL);
INSERT INTO session VALUES ('ses_1', '/tmp/oc', 1791100000000);
INSERT INTO message VALUES ('msg_u1', 'ses_1', 1791100000000, 1791100000000,
  '{"role":"user","time":{"created":1791100000000}}');
-- Current shape: the five kinds are disjoint and cost is recorded.
INSERT INTO message VALUES ('msg_a1', 'ses_1', 1791100001000, 1791100002000,
  '{"role":"assistant","providerID":"anthropic","modelID":"claude-sonnet-4-5","cost":0.01,"time":{"created":1791100001000},"tokens":{"total":1600,"input":100,"output":300,"reasoning":200,"cache":{"read":1000,"write":0}}}');
-- Older shape: reasoning is inside output (total = input + output), and a zero cost means none was recorded.
INSERT INTO message VALUES ('msg_a2', 'ses_1', 1791100003000, 1791100004000,
  '{"role":"assistant","providerID":"openai","modelID":"gpt-5.4","cost":0,"time":{"created":1791100003000},"tokens":{"total":8695,"input":8611,"output":84,"reasoning":51,"cache":{"read":0,"write":0}}}');
-- Still streaming: no tokens yet.
INSERT INTO message VALUES ('msg_a3', 'ses_1', 1791100005000, 1791100005000,
  '{"role":"assistant","providerID":"openai","modelID":"gpt-5.4","cost":0,"time":{"created":1791100005000},"tokens":{"input":0,"output":0,"reasoning":0,"cache":{"read":0,"write":0}}}');
