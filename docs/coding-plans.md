# Coding plans

Subscription plans that model providers sell with an API key: coding plans,
token plans, agent plans and the like. Settings › Providers › Inference lists
each one in `InferenceCatalog.all`
(`app/mac/Sources/QuiverCore/Providers/InferencePlans.swift`). This page is where
those values came from. Researched 2026-10-19 from each vendor's own docs;
plans change monthly, so recheck before relying on a price or a model id.

## What SWE does with a plan

SWE stores the plan key in `~/.swe/secrets/providers/.api_keys` under its own variable name
(`GLM_CODING_PLAN_KEY`, not `ZAI_API_KEY`). Every vendor below either uses a
separate plan key or a separate plan endpoint, and a plan key sent to the
pay-as-you-go endpoint bills the account balance instead of the plan (Z.ai's
error 1113, Alibaba's 403, Baidu's 401). Some harnesses pick up a
pay-as-you-go variable on their own, so a plan key under that name would be
used against the wrong endpoint. Where the plan key and endpoint are the
vendor's ordinary ones (Ollama, Synthetic, Poe), the variable is the vendor's
own name.

SWE does not point any harness at a plan yet. How harnesses should pick
plan keys up (some autodetect variables) is still open.

## Key checks

Each plan's check is an empty `POST {base}/v1/messages`. Every plan with a
check answered a fake key with 401 or 403 before reading the body (tested
2026-10-19), so a real key gets a request error back (400 or 422), which the
check reads as valid, and nothing runs. Plans whose endpoint reads the body
first, so an empty request can't tell keys apart, have no check: DevPass,
Qianfan Token Plan, Vercel Token Pass, Poe, Unicom Cloud, Ctyun and SCNet.
TaoToken answers a fake key with a 400, which would read as valid, so it has
no check either.

## Plans in the catalog

The base URL is what Claude Code takes as `ANTHROPIC_BASE_URL`. "Key var" is
the variable the vendor's Claude Code guide puts the key in.

| Plan | Vendor | Regions and base URL | Key var | Default model | Source |
|---|---|---|---|---|---|
| Ark Coding Plan | Volcengine | China `https://ark.cn-beijing.volces.com/api/coding`; intl (BytePlus) `https://ark.ap-southeast.bytepluses.com/api/coding` | `ANTHROPIC_AUTH_TOKEN` | `ark-code-latest` | [Claude Code](https://ark.volcengine.com/region:cn-beijing/docs/ark/coding-plan-personal-ai-claude-code?lang=zh), [BytePlus](https://www.byteplus.com/en/activity/codingplan) |
| Ark Agent Plan | Volcengine | China `https://ark.cn-beijing.volces.com/api/plan` | `ANTHROPIC_AUTH_TOKEN` | `ark-code-latest` | [get started](https://ark.volcengine.com/region:cn-beijing/docs/ark/agent-plan-personal-get-started?lang=zh) |
| GLM Coding Plan | Z.ai / Zhipu | intl `https://api.z.ai/api/anthropic`; China `https://open.bigmodel.cn/api/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `glm-5.3` | [Z.ai](https://docs.z.ai/devpack/tool/claude), [Zhipu](https://docs.bigmodel.cn/cn/coding-plan/tool/claude) |
| Kimi Code | Moonshot AI | intl `https://api.kimi.ai/coding/`; China `https://api.kimi.com/coding/` | `ANTHROPIC_API_KEY` | `k3` | [Claude Code](https://www.kimi.com/code/docs/en/third-party-tools/claude-code), [hosts](https://www.kimi.com/en/help/kimi-code/membership-guide) |
| MiniMax M Plan | MiniMax | intl `https://api.minimax.io/anthropic`; China `https://api.minimax.cn/anthropic` | `ANTHROPIC_AUTH_TOKEN` (the quickstart says `ANTHROPIC_API_KEY`) | `MiniMax-M3.1-Flash-Preview` | [intl](https://platform.minimax.io/docs/m-plan/claude-code), [China](https://platform.minimax.cn/docs/token-plan/claude-code) |
| Qwen Coding Plan | Alibaba Cloud | China `https://coding.dashscope.aliyuncs.com/apps/anthropic`; intl `https://coding-intl.dashscope.aliyuncs.com/apps/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `qwen3.7-plus` | [China](https://help.aliyun.com/en/model-studio/claude-code), [intl](https://www.alibabacloud.com/help/en/model-studio/claude-code) |
| Qwen Token Plan | Alibaba Cloud | China `https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic`; Singapore `https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `auto` | [overview](https://www.alibabacloud.com/help/en/model-studio/token-plan-overview) |
| Step Plan | StepFun | China `https://api.stepfun.com/step_plan`; intl `https://api.stepfun.ai/step_plan` | `ANTHROPIC_AUTH_TOKEN` | `step-5-preview` | [Claude Code](https://platform.stepfun.ai/docs/en/step-plan/integrations/claude-code) |
| MiMo Token Plan | Xiaomi | China, Singapore, Europe `https://token-plan-{cn,sgp,ams}.xiaomimimo.com/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `mimo-v2.6-pro` | [Claude Code](https://mimo.mi.com/docs/en-US/tokenplan/integration/claudecode) |
| Tencent Coding Plan | Tencent Cloud | China `https://api.lkeap.cloud.tencent.com/coding/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `tc-code-latest` | [Claude Code](https://cloud.tencent.com/document/product/1823/130097) |
| Tencent Token Plan | Tencent Cloud | China `https://api.lkeap.cloud.tencent.com/plan/anthropic` | not stated | `tc-code-latest` | [quickstart](https://cloud.tencent.com/document/product/1823/130119) |
| Qianfan Token Plan | Baidu | China `https://qianfan.baidubce.com/anthropic/tokenplan/personal` | `ANTHROPIC_AUTH_TOKEN` | `qianfan-code-latest` | [Token Plan](https://cloud.baidu.com/doc/qianfan/s/Dmrabu8b6) |
| JD Cloud Token Plan | JD Cloud | China `https://modelservice.jdcloud.com/tokenPlan/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `maas-token-latest` | [tool setup](https://docs.jdcloud.com/cn/jdaip/tool-setup) |
| Astron Coding Plan | iFlytek | China `https://maas-coding-api.cn-huabei-1.xf-yun.com/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `astron-code-latest` | [doc](https://www.xfyun.cn/doc/spark/CodingPlan.html) |
| Astron Token Plan | iFlytek | China `https://maas-token-api.cn-huabei-1.xf-yun.com/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `xopglm52` | [doc](https://www.xfyun.cn/doc/spark/TokenPlan.html) |
| Unicom Cloud Coding Plan | China Unicom | China `https://aigw-gzgy2.cucloud.cn:8443` (Guiyang) | `ANTHROPIC_AUTH_TOKEN` | `aisp-auto-route` | [Claude Code](https://support.cucloud.cn/document/127/591/2357.html?arcid=7019&id=2357) |
| Ctyun Coding Token Plan | China Telecom | China `https://ai.ctaigw.cn/coding` | `ANTHROPIC_AUTH_TOKEN` | `DeepSeek-V4-Pro` | [Claude Code](https://www.ctyun.cn/document/11061839/11092971) |
| Huawei Cloud Token Plan | Huawei Cloud | China `https://api.modelarts-maas.com/plan/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `glm-5.3` | [Claude Code](https://support.huaweicloud.com/Token-plan-maas/tokenplan-maas-0003.html) |
| SCNet Token Plan | SCNet | China `https://api.scnet.cn/api/llm/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `GLM-5.3` | [Claude Code](https://www.scnet.cn/ac/openapi/doc/2.0/moduleapi/tools/claude-code.html) |
| Compshare Coding Plan | UCloud | China `https://cp.compshare.cn` | `ANTHROPIC_AUTH_TOKEN` | `glm-5.1` | [Claude Code](https://www.compshare.cn/docs/modelverse/best_practice/claudecode) |
| KwaiKAT Coding Plan | Kuaishou StreamLake | intl `https://vanchin.streamlake.ai/api/gateway/coding/kat-coder-pro-v2.5/claude-code-proxy` | `ANTHROPIC_AUTH_TOKEN` | `kat-coder-pro-v2.5` | [doc](https://streamlake.ai/document/DOC/mg6k6nlp8j6qxicx4c9) |
| KUAE AI Coding Plan | Moore Threads | China `https://coding-plan-endpoint.kuaecloud.net` | `ANTHROPIC_AUTH_TOKEN` | `GLM-4.7` | [tools](https://docs.mthreads.com/kuaecloud/kuaecloud-doc-online/coding_plan/tools_config) |
| TaoToken Coding Plan | TaoToken | China `https://api.taotoken.net/coding` | `ANTHROPIC_AUTH_TOKEN` | `glm-5.3-flash` | [Claude Code](https://taotoken.net/doc/API%E6%8E%A5%E5%85%A5/ClaudeCodeAnthropic) |
| TaoToken Token Plan | TaoToken | China `https://api.taotoken.net/token` | `ANTHROPIC_AUTH_TOKEN` | `glm-5.3` | [Token Plan](https://taotoken.net/token-plan) |
| SenseNova Token Plan | SenseTime | China `https://token.sensenova.cn` | `ANTHROPIC_AUTH_TOKEN` | `sensenova-6.8-flash-lite` | [plan](https://www.sensenova.cn/token-plan) |
| Synthetic | Synthetic | `https://api.synthetic.new/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `syn:large:vision` | [Claude Code](https://dev.synthetic.new/docs/guides/claude-code) |
| Ollama Cloud | Ollama | `https://ollama.com` | `ANTHROPIC_AUTH_TOKEN` | `glm-5.3-flash` | [Claude Code](https://docs.ollama.com/integrations/claude-code) |
| DevPass | LLM Gateway | `https://api.llmgateway.io` | `ANTHROPIC_AUTH_TOKEN` | the gateway picks | [Claude Code](https://docs.llmgateway.io/guides/claude-code) |
| Token Pass | Vercel | `https://ai-gateway.vercel.sh/claude-code` | `ANTHROPIC_AUTH_TOKEN` | `zai/glm-5.3` | [coding agents](https://vercel.com/docs/ai-gateway/token-pass/coding-agents) |
| Charm Hyper | Charm | `https://hyper.charm.land` | `ANTHROPIC_API_KEY` | `deepseek-v4-pro` | [Claude Code](https://hyper.charm.land/docs/coding-agents/claude-code.html) |
| ZenMux Builder Plan | ZenMux | `https://zenmux.ai/api/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `claude-sonnet-4-6` | [Claude Code](https://zenmux.ai/docs/best-practices/claude-code.html) |
| Poe subscription | Poe | `https://api.poe.com` | `ANTHROPIC_AUTH_TOKEN` | `claude-sonnet-4.6` | [Claude Code](https://creator.poe.com/docs/external-applications/claude-code) |
| Standard Compute | Standard Compute | `https://api.stdcmpt.com` | `ANTHROPIC_AUTH_TOKEN` | `standardcompute` | [Claude Code](https://standardcompute.com/integrations/claude-code) |
| Atlas Cloud Coding Plan | Atlas Cloud | `https://api.atlascloud.ai` (from Atlas's blog, not its docs) | `ANTHROPIC_AUTH_TOKEN` | `deepseek-ai/deepseek-v4-pro` | [quick start](https://www.atlascloud.ai/docs/en/coding-plan/quick-start), [blog](https://www.atlascloud.ai/blog/tips/coding-plan-best-ai-coding-subscription-under-20) |
| CheapestInference | CheapestInference | `https://api.cheapestinference.com/anthropic` | `ANTHROPIC_AUTH_TOKEN` | `kimi-k3` | [Claude Code](https://cheapestinference.com/docs/guides/claude-code/) |

## Left out, and why

- **Closed or retiring:** Fireworks Fire Pass (closed to new subscribers),
  Cerebras Code (sold out), Baidu Qianfan Coding Plan (replaced by its Token
  Plan), JD Cloud Coding Plan (sales stopped 2026-07-29), Infini-AI
  (individuals ended 2026-06-26), iFlow (shut down 2026-04-17), Alaya Token
  (every tier sold out), Roo Code Cloud (shut down).
- **No documented Claude Code endpoint:** Chutes, OpenCode Go, Command Code,
  ClinePass, Neuralwatt, Yolo-Auto (OpenAI format only, or inferred URLs).
- **Unconfirmed on the vendor's own page:** Wafer Pass (docs 404), Novita
  Coding Plan (only the general API endpoint is documented), Agnes AI Token
  Plan, NanoGPT (no Anthropic route under the subscription path), GMI Coding
  Plan (waitlist, no URL), China Mobile Cloud, China Unicom Token Plan,
  Abacus RouteLLM, CrofAI, PGS AI.
- **Not a quota plan, or no key to take out:** Kilo Pass, Venice, Featherless,
  GitHub Copilot, Grok Build, Mistral Vibe Pro, Meta Muse Code, Unity Codely,
  GitCode AtomCode, and IDE subscriptions (Cursor, Windsurf, Kiro, Qoder,
  Trae, Factory, Warp, Amp, Augment).
- **Pay-as-you-go only:** DeepSeek, xAI, Groq, Together, Nebius, Hyperbolic,
  DeepInfra, Baseten, Parasail, Requesty, OpenRouter, SiliconFlow, Meituan
  LongCat (token packs, not a subscription), ModelScope and others.
