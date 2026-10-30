-- A made-up Cursor state.vscdb: both suites build it at
-- <user-data-dir>/User/globalStorage/state.vscdb and read it back.
CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB);
INSERT INTO cursorDiskKV VALUES
  ('bubbleId:composer-1:b1', '{"createdAt":"2026-08-10T08:37:16.298Z"}'),
  ('bubbleId:composer-1:b2', '{"createdAt":"2026-08-10T08:40:00.000Z"}'),
  ('bubbleId:composer-1:b3', '{"createdAt":"2026-08-10T09:30:00+00:00"}'),
  ('bubbleId:composer-2:b1', '{"createdAt":1786352400000}'),
  ('bubbleId:composer-2:b2', '{"createdAt":1786352460000.0}'),
  ('bubbleId:composer-2:b3', '{"createdAt":"1786352520000"}'),
  ('bubbleId:composer-2:b4', '{"createdAt":0}'),
  ('bubbleId:composer-2:b5', '{"createdAt":null}'),
  ('bubbleId:composer-2:b6', '{"createdAt":"soon"}'),
  ('bubbleId:composer-2:b7', '{}'),
  ('bubbleId:composer-2:b8', '{"createdAt":" 1786352580000"}'),
  ('bubbleId:composer-2:b9', '{"createdAt":"1_786_352_640_000"}'),
  ('bubbleId:composer-2:b10', '{"createdAt":"0x1p1024"}'),
  ('bubbleId:composer-2:b11', '{"createdAt":"1e400"}'),
  ('bubbleId:composer-3::b1', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('bubbleId:composer-4:', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('bubbleId::b1', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('bubbleId:nosuffix', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('bubbleId:task-call_1:b1', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('bubbleId:composer-6:b1', '{"createdAt":true}'),
  ('bubbleIdX:composer-5:b1', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('composerData:composer-1', '{"createdAt":"2026-08-10T15:00:00Z"}'),
  ('otherKey:x:y', '{"createdAt":"2026-08-10T15:00:00Z"}');
