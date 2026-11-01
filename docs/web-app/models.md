# Models

A partial port of the Mac app's Models page (`ModelsPage.swift`,
`ModelsModel.swift`). The Mac page is built on Artificial Analysis
benchmarks; this one shows only what the hub can show honestly from its
own data: the models in its usage logs, the lab behind each, the
harnesses that ran them, their list price and the tokens.

Files: `src/swe/webapp/pages/models.py`, `static/pages/models.js` and
`models.css`. The brand table (`pages/_brands.py`) and the usage slices
come from the Pricing page's modules.

## Needs the Artificial Analysis key: Mac only for now

The app downloads Artificial Analysis data with an API key saved in
Settings › Services, into a store only the app reads
(`~/.quiver/data/artificial-analysis/`). Nothing in Python reads that key or
store, and this port adds no key flow, so these stay in the Mac app:

- the benchmark columns: Intelligence index, Terminal-Bench 4.0 (coding),
  Humanity's Last Exam (reasoning), output speed, time to first token;
- the Chart view and its metric picker;
- Artificial Analysis's blended price and its cost-rank bar (a Free key
  gets no blended price from AA's V2 API, so the app blends input and
  output 3:1 itself; Terminal-Bench 4.0 and Humanity's Last Exam are
  Pro-only and stay empty on a Free key);
- the scopes "Your models + top 10" and "Top 25" (the leaderboard);
- matching logged names to Artificial Analysis models, which merges
  `grok-4.7` and `grok-4.7-high-fast` into one row, the unmatched-model
  warning, and the "Link to a model" sheet (a write, to
  `config/model-links.json`);
- the hover model card.

The page says this in a card above the table.

## What it shows

- Toolbar: harness multi-select (shared with Pricing), the Overview's
  periods (default This month), and a search box.
- A table of the models the picked harnesses ran in the period, most tokens
  first: brand logo, the logged name with its qualifier ("High", "Thinking ·
  High · Fast", parsed as `ModelMatcher.parse` does), "Ran in" (up to three
  harness icons, then "+N"), "Cost / 1M" (input · output · cache read from the
  entry the ledger charges, with its key, source and cache-write rate on hover)
  and "Your tokens". Every logged name is its own row.
- Search: your models whose name holds the query, then LiteLLM price-list keys
  that hold it (bare-name exact hits first, then prefixes, then the rest,
  shorter keys first), 60 keys at most and 100 rows in all, with "+N more
  matches" below. Names compare by their letters and digits only, so "kimi k3"
  finds `us.moonshotai.kimi-k3`.

## Payload

`GET /v1/app/models?period=<id>&tz=<zone>&harness=<a,b>&rescan=1`

```json
{
  "period": "month",
  "harness_options": ["claude", "devin", "codex"],
  "harness_filter": [],
  "rows": [{
    "model": "swe-2-max", "qualifier": "Max", "tokens": 317100000,
    "harnesses": ["devin"],
    "brand": {"id": "cognition", "name": "Cognition", "color": "#9aa0aa",
              "logo": "harness-devin.svg", "mono": true},
    "list_price": null
  }],
  "notes": [],
  "machine": "home-server"
}
```

`list_price`, when present: `{"key", "source": "litellm"|"quiver"|"yours",
"input", "output", "cache_read", "cache_write"}` in USD per 1M tokens.

`GET /v1/app/models?q=<query>&exclude=<names, one per line>` searches the
price list instead: `{"query", "matches": [rows as above, with no tokens],
"total": <all hits>}`. `exclude` carries the used models the page already
lists for the query, so they neither repeat nor count toward "+N more".
Search reads its own copy of the price list rather than the ledger, so
typing never waits behind a usage scan: built from the same files (never
downloading) and rebuilt whenever one of them changes, so it prices a key
as the ledger does.
