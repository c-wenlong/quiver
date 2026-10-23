# Pricing

Port of the Mac app's Pricing page (`PricingPage.swift`, `PricingModel.swift`):
every model the hub's ledger saw in the period, grouped by provider, with the
price its name resolves to, where that price came from, and its tokens.

Files: `src/quiver/webapp/pages/pricing.py`, `src/quiver/webapp/pages/_brands.py`
(the brand table, shared with Models), `static/pages/pricing.js` and `pricing.css`.

## What it shows

- One card per provider (the brand `ModelBrands` reads from the model name),
  largest first, titled with the lab's logo and "N models · X tokens".
- Per model: logo, name, up to three harness icons, the charged tiers
  ("fast ×2" when the catalog knows the multiplier), then Input, Output,
  Cache read and Cache write per 1M tokens, the Source mark and the tokens.
  Each card shows four rows and folds the rest behind "Show N more".
- Source: the biller's logo for a harness-billed row (every event logged its
  own charge; Grok Bot's charges are Cursor's), LiteLLM's mark, Quiver's icon
  for the supplement, a person for your own price, a gift for a free model,
  and a warning for no price. Hovering it gives the app's sentence on whose
  price the spend uses. A harness-billed row's rates are dimmed.
- Narrow cards drop the cache columns first (below 640px), then the Source
  column (below 520px), which a caption under the name replaces.
- The warning button lists models with unpriced tokens, largest first; a row
  scrolls to that model.
- Filters: a harness multi-select (harnesses with usage in the period, most
  tokens first) and the Overview's periods (default This month). A pick whose
  usage left the period drops out, as in the app.

## Payload

`GET /v1/app/pricing?period=<id>&tz=<IANA zone>&harness=<a,b>&rescan=1`

```json
{
  "period": "month",
  "harness_options": ["claude", "devin", "codex"],
  "harness_filter": [],
  "groups": [{
    "id": "anthropic", "name": "Anthropic", "tokens": 302600000,
    "brand": {"id": "anthropic", "name": "Anthropic", "color": "#d97757",
              "logo": "harness-claude-color.svg", "mono": false},
    "models": [{
      "model": "claude-opus-5-5", "tokens": 285400000,
      "harnesses": ["claude"], "harness_billed": false, "billed_by": [],
      "entry": "claude-opus-5-5", "source": "litellm", "spend_source": "litellm",
      "rates": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0},
      "tiers": [{"name": "fast", "multiplier": 2.0}],
      "is_free": false, "unpriced_tokens": 0, "brand": {"...": "..."}
    }]
  }],
  "unpriced": [{"model": "swe-2-max", "unpriced_tokens": 317100000,
                "harnesses": ["devin"], "brand": {"...": "..."}}],
  "notes": [],
  "machine": "kai-home-server"
}
```

`source` is the catalog entry's origin (`yours`, `quiver`, `litellm`, or null
when unpriced); `spend_source` is what the Source column shows (`billed`,
`free`, `yours`, `quiver`, `litellm`, `unpriced`). Rates are USD per 1M tokens.
`ledger/pricing.py` gained `PricingCatalog.resolve_entry` (the resolution with
the entry key) and `source(key)` for this, so the page and the ledger resolve
a name the same way.

## Differences from the Mac app

- **No price form.** Clicking a row in the app opens "Set a price", which saves
  or resets a price in `~/.quiver/config/pricing.json` and reprices past usage.
  That is a write and is left out; rows are not clickable, and the unpriced
  help says to set a price in the Mac app or in that file. The hub's ledger
  reads the same file, so a price saved there shows here as yours. To port it:
  a `post(ctx, "save"|"reset", body)` writing the file (the app's
  `PricingOverrides` rules, including removing every saved key that matches
  the model) and dropping the cached catalog.
- The subtitle drops "saving reprices past usage", since nothing here saves.
- Prices are today's: the web ledger keeps no price history (see
  `ledger/pricing.py`), so usage before a price change is priced at the new
  rate, where the app prices it at the old one.
- A tier the catalog prices at explicit rates (`gpt-4.1` priority) shows its
  name alone; the app labels it with the default multiplier, which the
  ledger does not apply to it.
- Hover help is the browser's title tooltip; the cache rates are on the rate
  cells' tooltip at every width, not only when the columns are dropped.
- A Rescan button, like the other web pages.
