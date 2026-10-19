# Third-party notices

## LiteLLM model prices

`Sources/QuiverCore/Resources/pricing.json` is a trimmed copy of
[`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json)
from LiteLLM, made with `script/trim-litellm-pricing.py`. The daily refresh
downloads the same file. It is used under the MIT License:

```
MIT License

Copyright (c) 2023 Berri AI

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## OpenUsage

The ledger's Claude and Codex parsing rules (ccusage-style dedup with the
sidechain pass, advisor iterations, the Codex fork replay gate, Codex service
tiers) and the fast-mode multipliers in `Resources/pricing-supplement.json`
follow OpenUsage, re-implemented here. OpenUsage is used under the MIT License:

```
MIT License

Copyright (c) 2026 Robin Ebers

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## References without copied code

AIUsage (Apache 2.0, the per-file cursor with inode, size, mtime, offset and
fingerprints) and TokenTracker (MIT, its notes on Claude sub-agent
over-counting, Codex cached input, and Devin's `request_id` identity) informed
the design. No code was copied from either.

## Harness logos

`Sources/QuiverCore/Resources/harness-*.svg` are the logos of the coding
harnesses and providers Quiver lists, imported by
`script/import-harness-icons.py`: Claude, Codex, Gemini, OpenCode, Qwen,
Mistral, Antigravity, Command Code, Qoder CN and Ark keep their brand colours
(`harness-<id>-color.svg`), the rest are single-colour marks. Every logo is a trademark of its owner
and is used only to name that harness; none of them implies endorsement.
Where each came from is in `Resources/harness-icons.tsv`:

- Claude, Codex, Gemini, Copilot, Cursor, Grok, Kiro, Devin, OpenCode, Kilo,
  Pi, Antigravity, Kimi, ZCode, Command Code, Qoder, Qoder CN and Volcano
  Engine Ark come from TokenTracker (`dashboard/public/brand-logos` and
  `ProviderIcon.jsx`), most of them extracted there from LobeHub icons.
- Claude's colour mark, Qwen, Mistral, goose, Cline and the API key
  provider marks (OpenAI, DeepSeek, Z.ai, Yi, MiniMax, Xiaomi MiMo,
  StepFun, Groq, Upstage, Cerebras, OpenRouter, Together AI, Fireworks
  AI, Vercel, Nebius, NVIDIA and Cloudflare) and the model-lab marks
  for Meta and Ant Group come from LobeHub icons
  (`@lobehub/icons-static-svg`).
- The inference plan and API provider marks for Tencent Cloud, Baidu
  Cloud, Huawei Cloud, iFlytek, SenseNova, KwaiKAT, Ollama, Poe, ZenMux,
  ElevenLabs and Atlas Cloud come from LobeHub icons
  (`@lobehub/icons-static-svg`). Synthetic, CheapestInference, Standard
  Compute, TaoToken, JD Cloud, State Cloud (China Telecom), SCNet, Charm,
  LLM Gateway and UCloud come from each company's own site or brand kit;
  China Unicom's from its logo file on English Wikipedia; Moore Threads'
  is traced from its site's PNG, as no vector version is published. These
  are trademarks of their owners, used only to name them; where each came
  from is in `harness-icons.tsv`.
- npm, Homebrew, Nix and uv come from Simple Icons (simple-icons,
  CC0-1.0); Routing.Run's
  planet mark from Phosphor Icons (`@phosphor-icons/core`, MIT).
- Continue comes from its repository (continuedev/continue,
  `docs/logo/light.svg`); aider from its repository (Aider-AI/aider,
  Apache-2.0, the site's pinned-tab icon); Crush from the
  charmbracelet/crush repository (FSL-1.1-MIT); Droid (Factory), Forge,
  Augment, Blackbox and Freebuff from each product's own site.
- Featherless's current mark is composed locally: the rebranded F from
  the inline SVG on featherless.ai, on its favicon's yellow tile; the
  site states no licence, so the mark is trademark-only like the rest.

TokenTracker is used under the MIT License (Copyright (c) 2026
xiufengsun; full text above, under OpenUsage, as the same notice). LobeHub
icons are used under the MIT License:

```
MIT License

Copyright (c) 2023 LobeHub

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Phosphor Icons (Routing.Run's planet mark) are used under the MIT License:

```
MIT License

Copyright (c) 2023 Phosphor Icons

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
