# Service status

The Mac app's Service status page (`ServiceStatusPage.swift`) in the web
app, sidebar id `status`, including its "Add service" catalogue sheet:
pages added here are tracked by the hub and shared by every browser it
serves.

## What it shows

- Title "Service status", subtitle "Live status from the official status
  pages of the services your harnesses use.", and in the header "Checked
  … ago" (the newest answer from any page) beside "Add service" and a
  Refresh button.
- Eight groups, matching the app's `StatusService.Group`: Model
  providers (Claude, OpenAI / Codex, Gemini), Agent platforms (GitHub /
  Copilot, Cursor, Devin), Cloud & network (Vercel, Cloudflare), Data,
  Dev tools & quality, Registries (npm), Observability, and Services &
  productivity, in the app's catalog order, plus any pages tracked from
  the catalogue after them. Each card has the service's logo, name, a link to its
  status page, and its health as a coloured dot and label (Operational,
  Under maintenance, Minor incident, Major outage, Critical outage;
  "Checking…" while the first check runs, "Unknown" with no reading in
  the last hour). Gemini carries the app's note that it comes from Google
  Cloud's feed.
- Cards start folded. A "Show details" row at the bottom, with a note
  like "1 incident · 2 components · summary", opens the page's own
  summary (when not operational), up to five affected components worst
  first ("And N more"), and up to three open incidents or maintenances
  under way, each with its stage, start, last update time and latest
  update text ("N more on the status page" links out).
- A problem line when a check failed: "Couldn't reach …", "… answered
  500", "… asked us to wait until 3:45 PM", and "Showing the reading from
  … ago" when an older reading is still shown.
- A card the user added carries a ··· button where the Mac's context menu
  is; it unfolds a "Stop tracking <name>?" confirm in the card, and
  confirming untracks the page. Built-in cards have no remove, as the
  app's guard on `isCustom` does.
- While the page is open it checks again every five minutes and repaints
  its relative times every minute, as the app's timer does.

## The catalogue sheet

"Add service" opens the app's `StatusCatalogueSheet` (the same 104 entries
as `StatusCatalogue.swift`, in `catalogue.py`): a "Service catalogue"
sheet with a search field, the All chip plus one per group,
and two-column rows — logo, name, the feed's host, and either an "Added"
mark or an Add button. Search and chips filter as the app's
`StatusCatalogue.filter` does: the query matches the name and the feed's
host ("docker" finds Docker Hub by name, "notion-status" by its page's
host). A footer counts "N of 104".

A row is marked Added when its feed's host is already on the page, built-in
or custom, so the same page can never be tracked twice under different
names. Add sends `track`; the answer is the page's fresh payload, so the
card appears behind the sheet and the row reads Added.

## Data

`src/quiver/status/` is a stdlib port of the app's `QuiverCore/Status`:

| Module | Swift | What |
|---|---|---|
| `catalog.py` | `ServiceStatusModels.swift` | `SERVICES` (same ids, names, page and feed URLs, groups), `Health`, `ComponentState`, the logo each card draws, `StatusService.custom` (`isCustom`) |
| `catalogue.py` | `StatusCatalogue.swift` | `CATALOGUE` — the 104 one-click entries — and `filter_` (`StatusCatalogue.filter`) |
| `tracked.py` | `CustomStatusServices.swift` | the tracked extras, in `~/.quiver/data/web-status-services.json` |
| `parsers.py` | `StatusParser.swift` | Statuspage / incident.io `summary.json` and Google Cloud `incidents.json` into a `Report` |
| `poller.py` | `ServiceStatusService.swift` | polling floors, conditional requests, `Retry-After` cooldowns, the cache file, `forget` (`ServiceStatusService.forget`) |

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

Tracked pages live in the hub's own store,
`~/.quiver/data/web-status-services.json` (`{"version": 1, "services":
[…]}`, one record per page: id, name, page and feed URLs, group, logo;
written atomically under an flock beside the file). Where the Mac app
gives every added page a fresh custom id in its preferences, the web
store keeps the catalogue id, so tracking is idempotent by construction
and every browser the hub serves sees the same set. The store feeds the
same poller as `SERVICES`: a tracked page is polled, cached and grouped
exactly like a built-in.

## `GET /v1/app/status`

| Query | Effect |
|---|---|
| `view=catalogue` | The add sheet's rows: `{entries: [{id, name, host, group, logo, added}]}`, all 104 in catalogue order. `added` is true when the entry's feed host is already tracked. |
| `cached=1` | The last readings, no request to any status page. The page paints this first. |
| `force=1` | Refresh: the one-minute floor instead of five. |

`POST /v1/app/status/track` with `{"id": "<catalogue id>"}` tracks a
catalogue entry: 404 `unknown_service` for an id the catalogue does not
have, 422 `bad_id` for a missing or non-string id, 409 `already_tracked`
when its feed host is already on the page. `POST /v1/app/status/untrack`
removes a tracked extra; its poll-cache entry becomes a tombstone
(`removed_at`, as the app's `service.forget(id:)` clears state), so a
reading that lands late from another hub sharing the cache cannot bring
the card back: 422 `built_in` for a built-in page, 404
`unknown_service` for an id nothing tracks. Both answer with the normal
payload, freshly checked as floors allow.

Each request to a status page times out after 8 s per socket wait and
16 s for the whole download, and a load waits at most 10 s in all; a page still answering after that finishes in the background
and shows on the next load.

```json
{
  "machine": "home-server",
  "checked_at": 1791720000.0,
  "cached": false,
  "server_time": 1791720030.0,
  "groups": [{
    "id": "modelProviders", "title": "Model providers",
    "services": [{
      "id": "claude", "name": "Claude", "page_url": "https://status.claude.com",
      "group": "modelProviders", "note": null, "logo": "claude", "custom": false,
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

`health` is null with no reading in the last hour. `server_time` is the
hub's clock: cards kept on screen while the hub can't be reached age by it
plus the time the browser has waited since, and go to "Unknown" past the
hour as well. `problem_until` is set
with "asked us to wait until", so the browser writes the time in its own
zone. Incident `url` is passed only when it is an `http(s)` address.
`custom` marks a page the user added; it is what puts ··· on the card.

## Differences from the Mac app

- Tracking lives on the hub, not in the app's preferences: the web app's
  customs are its own `web-status-services.json`, shared by every
  browser, and the app's `UserDefaults` list is not read. A page added on
  the Mac and one added on the web do not see each other's stores.
- The app gives every added page a fresh custom id; the web store keeps
  the catalogue id, so it can never hold a page twice, and a repeat track
  is refused (409 `already_tracked`) rather than adding a duplicate.
- The cache is the hub's own file (`web-service-status.json`), apart from
  the app's, and times in it are Unix epoch seconds.
- Requests follow redirects (urllib's default); the app's transport
  follows none, so its catalog URLs are already the final ones.
- A catalogue row or card with no shipped mark draws the service's
  initial on a tile (the app's `CatalogueRow` leaves the square empty);
  all 104 entries currently ship a mark, so this only covers future ones.
- The menu bar line (`ServiceStatusSummary`) is Mac only and not ported.
