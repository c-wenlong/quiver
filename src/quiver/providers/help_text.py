"""Per-topic help text for ``swe providers``."""

from quiver.console import c


def print_providers_help() -> None:
    print(
        f"""
  {c('bold', 'swe providers')} — Manage AI provider API keys and metadata

  {c('cyan', 'swe providers list [-d] [--api-keys-dir=DIR] [<filter>]')}
      List registered providers + masked key status (``-`` = no key file)

  {c('cyan', 'swe providers info <name|alias> [--api-keys-dir=DIR]')}
      Show full details for one provider, including key status + path

  {c('cyan', 'swe providers add <name> [desc] [--url URL] [--env ENV] [--file NAME]')}
      Register a provider in {c('bold', '~/.config/swe/providers.json')}.
      Does not create or touch any key file.

  {c('cyan', 'swe providers remove <name>')}
      Remove a provider from the registry. Does not delete your key file.

{c('bold', 'Key storage')}
  By default, keys live as plain-text files in {c('bold', '~/.api_keys/')}
  (override per-invocation with {c('cyan', '--api-keys-dir=DIR')}).
  One file per provider, filename matches the canonical slug —
  e.g. {c('cyan', '~/.api_keys/openai')} holds the OpenAI key as a single line.

  {c('bold', 'quiver NEVER stores the key string')} — only the filename pointer
  and provider metadata. Mask format:
    {c('dim', 'long key  (>12 chars):')} {c('bold', 'first8')} + {c('dim', '***')} + {c('bold', 'last4')} + {c('dim', '(len=N)')}
    {c('dim', 'short key (<=12):')}     {c('bold', 'first3')} + {c('dim', '***')} + {c('dim', '(len=N)')}
    {c('dim', 'missing key:')}          {c('bold', '-')}

{c('bold', 'Examples')}
  swe providers list
  swe providers info openai
  swe providers add anthropic "Anthropic Claude" \
      --env ANTHROPIC_API_KEY \
      --url https://console.anthropic.com/settings/keys \
      --aliases claude-key
  swe providers add myprov --env MY_API_KEY --url https://example.com
  swe providers remove myprov
"""
    )
