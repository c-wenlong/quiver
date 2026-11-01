"""Coding plans as launch providers for Claude Code.

A coding plan (``docs/coding-plans.md``) is an Anthropic-compatible
endpoint sold with its own key. Quiver keeps that key in the keys file
under the plan's own variable (``GLM_CODING_PLAN_KEY``), never the
vendor's pay-as-you-go name, so a plan key can't reach a pay-as-you-go
endpoint. A launch with a plan's slug gives Claude Code
``ANTHROPIC_BASE_URL`` (the plan's endpoint for that region), the key
under the variable the vendor's Claude Code guide names
(``ANTHROPIC_AUTH_TOKEN``, or ``ANTHROPIC_API_KEY`` for Kimi Code and
Charm Hyper), and ``ANTHROPIC_MODEL`` set to the vendor's recommended
model, which a model picked at launch still overrides. The other
Anthropic credential variable is blanked so an inherited key never
reaches the plan's endpoint.

``PLANS`` is a port of ``InferenceCatalog.all``
(``app/mac/Sources/QuiverCore/Providers/InferencePlans.swift``):
``(id, name, key variable, Claude Code key variable, model or None,
((region, base URL), ...))``. ``tests/test_remote_plan_providers.py``
fails when the two drift. A plan sold in several regions gives one slug
per region (``glm-coding-plan-china``), since each region has its own
accounts and keys; a one-region plan's slug is its id.
"""

from __future__ import annotations

import re

PLANS: tuple = (
    ('ark-coding-plan', 'Ark Coding Plan', 'ARK_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ark-code-latest',
     (('China', 'https://ark.cn-beijing.volces.com/api/coding'),
      ('International', 'https://ark.ap-southeast.bytepluses.com/api/coding'))),
    ('ark-agent-plan', 'Ark Agent Plan', 'ARK_AGENT_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ark-code-latest',
     (('China', 'https://ark.cn-beijing.volces.com/api/plan'),)),
    ('glm-coding-plan', 'GLM Coding Plan', 'GLM_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'glm-5.3',
     (('International', 'https://api.z.ai/api/anthropic'),
      ('China', 'https://open.bigmodel.cn/api/anthropic'))),
    ('kimi-code', 'Kimi Code', 'KIMI_CODE_KEY', 'ANTHROPIC_API_KEY', 'k3',
     (('International', 'https://api.kimi.ai/coding/'),
      ('China', 'https://api.kimi.com/coding/'))),
    ('minimax-m-plan', 'MiniMax M Plan', 'MINIMAX_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'MiniMax-M3.1-Flash-Preview',
     (('International', 'https://api.minimax.io/anthropic'),
      ('China', 'https://api.minimax.cn/anthropic'))),
    ('qwen-coding-plan', 'Qwen Coding Plan', 'QWEN_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'qwen3.7-plus',
     (('China', 'https://coding.dashscope.aliyuncs.com/apps/anthropic'),
      ('International', 'https://coding-intl.dashscope.aliyuncs.com/apps/anthropic'))),
    ('qwen-token-plan', 'Qwen Token Plan', 'QWEN_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'auto',
     (('China', 'https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic'),
      ('Singapore', 'https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic'))),
    ('step-plan', 'Step Plan', 'STEP_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'step-5-preview',
     (('China', 'https://api.stepfun.com/step_plan'),
      ('International', 'https://api.stepfun.ai/step_plan'))),
    ('mimo-token-plan', 'MiMo Token Plan', 'MIMO_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'mimo-v2.6-pro',
     (('China', 'https://token-plan-cn.xiaomimimo.com/anthropic'),
      ('Singapore', 'https://token-plan-sgp.xiaomimimo.com/anthropic'),
      ('Europe', 'https://token-plan-ams.xiaomimimo.com/anthropic'))),
    ('tencent-coding-plan', 'Tencent Coding Plan', 'TENCENT_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'tc-code-latest',
     (('China', 'https://api.lkeap.cloud.tencent.com/coding/anthropic'),)),
    ('tencent-token-plan', 'Tencent Token Plan', 'TENCENT_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'tc-code-latest',
     (('China', 'https://api.lkeap.cloud.tencent.com/plan/anthropic'),)),
    ('qianfan-token-plan', 'Qianfan Token Plan', 'QIANFAN_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'qianfan-code-latest',
     (('China', 'https://qianfan.baidubce.com/anthropic/tokenplan/personal'),)),
    ('synthetic', 'Synthetic', 'SYNTHETIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'syn:large:vision',
     (('Global', 'https://api.synthetic.new/anthropic'),)),
    ('ollama-cloud', 'Ollama Cloud', 'OLLAMA_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'glm-5.3-flash',
     (('Global', 'https://ollama.com'),)),
    ('devpass', 'DevPass', 'DEVPASS_KEY', 'ANTHROPIC_AUTH_TOKEN', None,
     (('Global', 'https://api.llmgateway.io'),)),
    ('vercel-token-pass', 'Token Pass', 'VERCEL_TOKEN_PASS_KEY', 'ANTHROPIC_AUTH_TOKEN', 'zai/glm-5.3',
     (('Global', 'https://ai-gateway.vercel.sh/claude-code'),)),
    ('charm-hyper', 'Charm Hyper', 'CHARM_HYPER_KEY', 'ANTHROPIC_API_KEY', 'deepseek-v4-pro',
     (('Global', 'https://hyper.charm.land'),)),
    ('zenmux-builder-plan', 'ZenMux Builder Plan', 'ZENMUX_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'claude-sonnet-4-6',
     (('Global', 'https://zenmux.ai/api/anthropic'),)),
    ('poe', 'Poe subscription', 'POE_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'claude-sonnet-4.6',
     (('Global', 'https://api.poe.com'),)),
    ('standard-compute', 'Standard Compute', 'STANDARD_COMPUTE_KEY', 'ANTHROPIC_AUTH_TOKEN', 'standardcompute',
     (('Global', 'https://api.stdcmpt.com'),)),
    ('atlas-coding-plan', 'Atlas Cloud Coding Plan', 'ATLAS_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'deepseek-ai/deepseek-v4-pro',
     (('Global', 'https://api.atlascloud.ai'),)),
    ('cheapestinference', 'CheapestInference', 'CHEAPESTINFERENCE_KEY', 'ANTHROPIC_AUTH_TOKEN', 'kimi-k3',
     (('Global', 'https://api.cheapestinference.com/anthropic'),)),
    ('jdcloud-token-plan', 'JD Cloud Token Plan', 'JDCLOUD_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'maas-token-latest',
     (('China', 'https://modelservice.jdcloud.com/tokenPlan/anthropic'),)),
    ('astron-coding-plan', 'Astron Coding Plan', 'ASTRON_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'astron-code-latest',
     (('China', 'https://maas-coding-api.cn-huabei-1.xf-yun.com/anthropic'),)),
    ('astron-token-plan', 'Astron Token Plan', 'ASTRON_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'xopglm52',
     (('China', 'https://maas-token-api.cn-huabei-1.xf-yun.com/anthropic'),)),
    ('unicom-coding-plan', 'Unicom Cloud Coding Plan', 'UNICOM_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'aisp-auto-route',
     (('China', 'https://aigw-gzgy2.cucloud.cn:8443'),)),
    ('ctyun-coding-plan', 'Ctyun Coding Token Plan', 'CTYUN_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'DeepSeek-V4-Pro',
     (('China', 'https://ai.ctaigw.cn/coding'),)),
    ('huawei-token-plan', 'Huawei Cloud Token Plan', 'HUAWEI_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'glm-5.3',
     (('China', 'https://api.modelarts-maas.com/plan/anthropic'),)),
    ('scnet-token-plan', 'SCNet Token Plan', 'SCNET_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'GLM-5.3',
     (('China', 'https://api.scnet.cn/api/llm/anthropic'),)),
    ('compshare-coding-plan', 'Compshare Coding Plan', 'COMPSHARE_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'glm-5.1',
     (('China', 'https://cp.compshare.cn'),)),
    ('kwaikat-coding-plan', 'KwaiKAT Coding Plan', 'KWAIKAT_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'kat-coder-pro-v2.5',
     (('International', 'https://vanchin.streamlake.ai/api/gateway/coding/kat-coder-pro-v2.5/claude-code-proxy'),)),
    ('kuae-coding-plan', 'KUAE AI Coding Plan', 'KUAE_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'GLM-4.7',
     (('China', 'https://coding-plan-endpoint.kuaecloud.net'),)),
    ('taotoken-coding-plan', 'TaoToken Coding Plan', 'TAOTOKEN_CODING_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'glm-5.3-flash',
     (('China', 'https://api.taotoken.net/coding'),)),
    ('taotoken-token-plan', 'TaoToken Token Plan', 'TAOTOKEN_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'glm-5.3',
     (('China', 'https://api.taotoken.net/token'),)),
    ('sensenova-token-plan', 'SenseNova Token Plan', 'SENSENOVA_TOKEN_PLAN_KEY', 'ANTHROPIC_AUTH_TOKEN', 'sensenova-6.8-flash-lite',
     (('China', 'https://token.sensenova.cn'),)),
)


def _region_slug(region: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", region.lower()).strip("-")


def claude_entries() -> dict[str, dict]:
    """``{slug: provider entry}`` for Claude Code, one per plan region,
    in catalog order (the shape ``launch_provider`` declares)."""
    out: dict[str, dict] = {}
    for pid, name, key, key_var, model, endpoints in PLANS:
        other = ("ANTHROPIC_API_KEY" if key_var == "ANTHROPIC_AUTH_TOKEN"
                 else "ANTHROPIC_AUTH_TOKEN")
        for region, base in endpoints:
            slug = pid if len(endpoints) == 1 else \
                f"{pid}-{_region_slug(region)}"
            fixed = {"ANTHROPIC_BASE_URL": base, other: ""}
            if model:
                fixed["ANTHROPIC_MODEL"] = model
            out[slug] = {
                "keys": {key_var: [key]},
                "set": fixed,
                "label": name if len(endpoints) == 1 else f"{name} ({region})",
            }
    return out
