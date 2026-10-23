# Service status

The Mac app's Service status page (`ServiceStatusPage.swift`) in the web
app, sidebar id `status`. Read only.

## What it shows

- Title "Service status", subtitle "Live status from the official status
  pages of the services your harnesses use.", and in the header "Checked
  … ago" (the newest answer from any page) beside a Refresh button.
- Two groups, AI providers (Claude, OpenAI / Codex, GitHub / Copilot,
  Cursor, Gemini, Devin) and Infrastructure (npm, Vercel, Cloudflare), in
  the app's catalog order. Each card has the service's logo, name, a link
  to its status page, and its health as a coloured dot and label
  (Operational, Under maintenance, Minor incident, Major outage, Critical
  outage; "Checking…" while the first check runs, "Unknown" with no
  reading in the last hour). Gemini carries the app's note that it comes
  from Google Cloud's feed.
- Cards start folded. A "Show details" row at the bottom, with a note like
  "1 incident · 2 components · summary", opens the page's own summary
  (when not operational), up to five affected components worst first
  ("And N more"), and up to three open incidents or maintenances under
  way, each with its stage, start, last update time and latest update text
  ("N more on the status page" links out).
- A problem line when a check failed: "Couldn't reach …", "… answered
  500", "… asked us to wait until 3:45 PM", and "Showing the reading from
  … ago" when an older reading is still shown.
- While the page is open it checks again every five minutes and repaints
  its relative times every minute, as the app's timer does.

## Data

`src/quiver/status/` is a stdlib port of the app's `QuiverCore/Status`:

| Module | Swift | What |
|---|---|---|
| `catalog.py` | `ServiceStatusModels.swift` | `SERVICES` (same ids, names, page and feed URLs, groups), `Health`, `ComponentState`, the logo each card draws |
| `parsers.py` | `StatusParser.swift` | Statuspage / incident.io `summary.json` and Google Cloud `incidents.json` into a `Report` |
| `poller.py` | `ServiceStatusService.swift` | polling floors, conditional requests, `Retry-After` cooldowns, the cache file |

The poller asks a page at most every five minutes (one minute for
Refresh), never during a cooldown (429 or 503: `Retry-After`, else 15
minutes, at most six hours), sends `If-None-Match` / `If-Modified-Since`
when it has a reading to fall back on, and never shows a reading older
than an hour. Readings live in `~/.quiver/cache/web-service-status.json`
(written atomically under an flock), so a hub restart does not poll
again. One poller per home serves every request, so overlapping loads
share one request per page. TLS uses the fallback in
`harness/rate_limits.py`: a failed verification is retried once with the
OS trust store, never unverified.

## `GET /v1/app/status`

| Query | Effect |
|---|---|
| `cached=1` | The last readings, no request to any status page. The page paints this first. |
| `force=1` | Refresh: the one-minute floor instead of five. |

Each request to a status page times out after 8 s and a load waits at most
10 s in all; a page still answering after that finishes in the background
and shows on the next load.

```json
{
  "machine": "kai-home-server",
  "checked_at": 1791720000.0,
  "cached": false,
  "groups": [{
    "id": "providers", "title": "AI providers",
    "services": [{
      "id": "claude", "name": "Claude", "page_url": "https://status.claude.com",
      "group": "providers", "note": null, "logo": "claude",
      "health": "minor", "health_title": "Minor incident",
      "summary": "Partially Degraded Service",
      "components": [{"name": "Claude Console (platform.claude.com)", "state": "degraded",
                      "state_title": "Degraded performance"}],
      "incidents": [{"id": "…", "name": "Elevated errors on platform.claude.com", "impact": "major",
                     "stage": "Monitoring", "started_at": 1791379504.155, "updated_at": 1791409481.466,
                     "latest_update": "We've mitigated …", "url": "https://stspg.io/sjkw7njwf0mt",
                     "maintenance": false}],
      "checked_at": 1791720000.0, "problem": null, "problem_until": null, "stale": false
    }]
  }]
}
```

`health` is null with no reading in the last hour. `problem_until` is set
with "asked us to wait until", so the browser writes the time in its own
zone. Incident `url` is passed only when it is an `http(s)` address.

## Differences from the Mac app

- No "Add service" sheet, remove menu or custom pages: the app keeps the
  pages a user added in its own preferences, which the web app does not
  read, and this page does not write. The footer drops "Pages you add are
  checked the same way."
- The cache is the hub's own file (`web-service-status.json`), apart from
  the app's, and times in it are Unix epoch seconds.
- Requests follow redirects (urllib's default); the app's transport
  follows none, so its catalog URLs are already the final ones.
- The menu bar line (`ServiceStatusSummary`) is Mac only and not ported.
