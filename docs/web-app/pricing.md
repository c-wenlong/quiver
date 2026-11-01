# Pricing

Port of the Mac app's Pricing page (`PricingPage.swift`, `PricingModel.swift`):
every model the hub's ledger saw in the period, grouped by provider, with the
price its name resolves to, where that price came from, and its tokens. A
row opens the app's price form, which saves your own price or removes it.

Files: `src/swe/webapp/pages/pricing.py`, `src/swe/webapp/pages/_brands.py`
(the brand table, shared with Models), `static/pages/pricing.js` and `pricing.css`.

## What it shows

- One card per provider (the brand `ModelBrands` reads from the model name),
  largest first, titled with the lab's logo and "N models · X tokens".
- Per model: logo, name, up to three harness icons, the charged tiers
  ("fast ×2" when the catalog knows the multiplier), then Input, Output,
  Cache read and Cache write per 1M tokens, the Source mark and the tokens.
  Each card shows four rows and folds the rest behind "Show N more".
- Source: the biller's logo for a harness-billed row (every event logged its
  own charge; Grok Bot's charges are Cursor's), LiteLLM's mark, SWE's icon
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
      "entry": "claude-opus-5-5", "key": "claude-opus-5-5",
      "source": "litellm", "spend_source": "litellm",
      "rates": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0},
      "tiers": [{"name": "fast", "multiplier": 2.0}],
      "is_free": false, "unpriced_tokens": 0, "brand": {"...": "..."}
    }]
  }],
  "unpriced": [{"model": "swe-2-max", "unpriced_tokens": 317100000,
                "harnesses": ["devin"], "brand": {"...": "..."}}],
  "notes": [],
  "machine": "home-server"
}
```

`source` is the catalog entry's origin (`yours`, `quiver`, `litellm`, or null
when unpriced); `spend_source` is what the Source column shows (`billed`,
`free`, `yours`, `quiver`, `litellm`, `unpriced`). Rates are USD per 1M tokens.
`ledger/pricing.py` gained `PricingCatalog.resolve_entry` (the resolution with
the entry key) and `source(key)` for this, so the page and the ledger resolve
a name the same way.

`key` is the price form's "Price applies to": the saved entry when the row is
priced by you, else `base_name(model)` (`PricingCatalog.baseName`), which
covers every dated and effort form of the name.

### The price form

Clicking a row (or Enter on it) opens a dialog, the app's `PricingEditSheet`:
the model, "Price applies to" (monospaced, pre-filled with `key`), Input,
Output, Cache read and Cache write pre-filled with the row's rates, the
note "USD per 1M tokens. Blank cache fields count as 0. Saves to
~/.swe/config/pricing.json on <machine>.", and Cancel / Save. Save stays
off until the name is set, Input and Output are numbers of 0 or more, and
the cache fields are blank or such numbers. A row priced by you also gets
"Remove your price". The dialog lives outside the page panel, so a repaint
behind it never loses what is typed; it closes once the hub answers and the
page repaints from that answer.

`POST /v1/app/pricing/save` with `{"model", "key", "input", "output",
"cache_read"?, "cache_write"?, "replacing"?, "period"?, "tz"?, "harness"?}`
writes `key`'s price with `ledger/pricing.py`'s `save_override`, the app's
`PricingOverrides.set`: `{"schema": 1, "models": {...}}`, sorted, written
atomically, other entries and top-level keys kept. `replacing` (the row's
saved entry, accepted only if it is yours now) leaves in the same write. A
rate must be a finite number from 0 to 1,000,000 (strings parse; blank cache
rates are 0): 422 `bad_rate`, `bad_key`, `bad_model` otherwise.
`POST /v1/app/pricing/remove` with `{"model", …}` drops every saved key that
prices the model (`PricingCatalog.user_keys`, the app's `userKeys(matching:)`)
in one write (`remove_overrides`); 404 `no_saved_price` when there is none.
A `pricing.json` that isn't JSON is never overwritten (409
`pricing_unwritable`). Each write drops the hub's cached price list
(`WebApp.prices_changed`), and the answer is `{"action", "page"}`, the page
read again with the period, zone and filter sent.

## Differences from the Mac app

- **Remove, not Reset.** The app titles the button "Reset to list price" when
  a downloaded price sits under yours and shows that list price in the form.
  The web ledger keeps one entry per key, so the form doesn't know the list
  price, and the button reads "Remove your price" either way; the row falls
  back to the downloaded price when there is one.
- Prices are today's: the web ledger keeps no price history (see
  `ledger/pricing.py`), so usage before a price change is priced at the new
  rate, where the app prices it at the old one.
- A tier the catalog prices at explicit rates (`gpt-4.1` priority) shows its
  name alone; the app labels it with the default multiplier, which the
  ledger does not apply to it.
- Hover help is the browser's title tooltip; the cache rates are on the rate
  cells' tooltip at every width, not only when the columns are dropped.
- A Rescan button, like the other web pages.
