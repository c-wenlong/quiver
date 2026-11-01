import Foundation

/// A subscription plan on a model provider (a coding plan, token plan,
/// agent plan…) that you reach with one API key. Settings › Providers ›
/// Inference lists each one beside the pay-as-you-go API keys: paste the
/// key and Quiver keeps it in the keys file under `keyName`.
///
/// The defaults come from each vendor's own Claude Code setup page, as of
/// docs/research/coding-plans.md. Quiver shows them and checks the key
/// against them, and a launch can point Claude Code at one: the hub's
/// `plan_providers.PLANS` is a port of `all`, and a Python test fails
/// when the two drift.
public struct InferencePlan: Identifiable, Sendable, Equatable {
    /// One place the plan is served from. A plan sold in China and abroad
    /// has one per site, each with its own console and accounts.
    public struct Endpoint: Sendable, Equatable {
        /// "China", "International", "Singapore"…
        public var region: String
        /// What Claude Code takes as `ANTHROPIC_BASE_URL`.
        public var anthropicBaseURL: String
        /// Where the key is created or copied.
        public var console: URL

        /// The Messages endpoint under the base URL, for the key check.
        public var messagesURL: URL {
            let base = anthropicBaseURL.hasSuffix("/") ? String(anthropicBaseURL.dropLast()) : anthropicBaseURL
            return URL(string: base + "/v1/messages")!
        }
    }

    public var id: String
    public var name: String
    public var vendor: String
    /// The `HarnessIcons` id of the vendor's logo, if one is bundled.
    public var logo: String?
    /// The variable the key is saved under. Quiver's own name, never the
    /// vendor's pay-as-you-go one: a plan key on the pay-as-you-go endpoint
    /// bills the account instead of the plan (Z.ai's error 1113), and some
    /// harnesses pick a pay-as-you-go name up by themselves. Where the plan
    /// key and endpoint are the vendor's normal ones (Ollama, Synthetic),
    /// it is the vendor's own name.
    public var keyName: String
    public var endpoints: [Endpoint]
    /// The variable the vendor's Claude Code page puts the key in.
    public var claudeCodeKeyVar: String
    /// The vendor's recommended model for Claude Code; nil when the plan
    /// routes on its own.
    public var model: String?
    /// How the key check sends the key, or nil when the endpoint reads the
    /// request before the key, so an empty request can't tell a good key
    /// from a bad one.
    public var checkAuth: APIKeyProbe.Auth?
}

public enum InferenceCatalog {
    /// The heading a new plan key is filed under in the keys file. One
    /// heading for every plan, since the file only takes short words as a
    /// heading and some plan names are one long word ("CheapestInference").
    /// A key you filed under a heading of your own keeps it.
    public static let heading = "Coding plans"

    private static func endpoint(_ region: String, _ base: String, _ console: String) -> InferencePlan.Endpoint {
        InferencePlan.Endpoint(region: region, anthropicBaseURL: base, console: URL(string: console)!)
    }

    private static let token = "ANTHROPIC_AUTH_TOKEN"

    public static let all: [InferencePlan] = [
        InferencePlan(
            id: "ark-coding-plan", name: "Ark Coding Plan", vendor: "Volcengine", logo: "ark",
            keyName: "ARK_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://ark.cn-beijing.volces.com/api/coding",
                                 "https://ark.volcengine.com/region:cn-beijing/apikey"),
                        endpoint("International", "https://ark.ap-southeast.bytepluses.com/api/coding",
                                 "https://www.byteplus.com/en/activity/codingplan")],
            claudeCodeKeyVar: token, model: "ark-code-latest", checkAuth: .bearer),
        InferencePlan(
            id: "ark-agent-plan", name: "Ark Agent Plan", vendor: "Volcengine", logo: "ark",
            keyName: "ARK_AGENT_PLAN_KEY",
            endpoints: [endpoint("China", "https://ark.cn-beijing.volces.com/api/plan",
                                 "https://ark.volcengine.com/region:cn-beijing/openManagement?LLM=%7B%7D&advancedActiveKey=agentPlan")],
            claudeCodeKeyVar: token, model: "ark-code-latest", checkAuth: .bearer),
        InferencePlan(
            id: "glm-coding-plan", name: "GLM Coding Plan", vendor: "Z.ai", logo: "zai",
            keyName: "GLM_CODING_PLAN_KEY",
            endpoints: [endpoint("International", "https://api.z.ai/api/anthropic",
                                 "https://z.ai/manage-apikey/apikey-list"),
                        endpoint("China", "https://open.bigmodel.cn/api/anthropic",
                                 "https://bigmodel.cn/apikey/coding-plan-per")],
            claudeCodeKeyVar: token, model: "glm-5.3", checkAuth: .bearer),
        InferencePlan(
            id: "kimi-code", name: "Kimi Code", vendor: "Moonshot AI", logo: "kimi",
            keyName: "KIMI_CODE_KEY",
            endpoints: [endpoint("International", "https://api.kimi.ai/coding/",
                                 "https://www.kimi.com/code/console"),
                        endpoint("China", "https://api.kimi.com/coding/",
                                 "https://www.kimi.com/code/console")],
            claudeCodeKeyVar: "ANTHROPIC_API_KEY", model: "k3", checkAuth: .xApiKey),
        InferencePlan(
            id: "minimax-m-plan", name: "MiniMax M Plan", vendor: "MiniMax", logo: "minimax",
            keyName: "MINIMAX_PLAN_KEY",
            endpoints: [endpoint("International", "https://api.minimax.io/anthropic",
                                 "https://platform.minimax.io/console/plan"),
                        endpoint("China", "https://api.minimax.cn/anthropic",
                                 "https://platform.minimax.cn/console/plan")],
            claudeCodeKeyVar: token, model: "MiniMax-M3.1-Flash-Preview", checkAuth: .xApiKey),
        InferencePlan(
            id: "qwen-coding-plan", name: "Qwen Coding Plan", vendor: "Alibaba Cloud", logo: "qwen-code",
            keyName: "QWEN_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://coding.dashscope.aliyuncs.com/apps/anthropic",
                                 "https://bailian.console.aliyun.com/cn-beijing/subscription/coding-plan"),
                        endpoint("International", "https://coding-intl.dashscope.aliyuncs.com/apps/anthropic",
                                 "https://modelstudio.console.alibabacloud.com/ap-southeast-1/subscription/coding-plan")],
            claudeCodeKeyVar: token, model: "qwen3.7-plus", checkAuth: .bearer),
        InferencePlan(
            id: "qwen-token-plan", name: "Qwen Token Plan", vendor: "Alibaba Cloud", logo: "qwen-code",
            keyName: "QWEN_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://token-plan.cn-beijing.maas.aliyuncs.com/apps/anthropic",
                                 "https://bailian.console.aliyun.com/cn-beijing/subscription/overview"),
                        endpoint("Singapore", "https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic",
                                 "https://modelstudio.console.alibabacloud.com/ap-southeast-1/subscription/token-plan")],
            claudeCodeKeyVar: token, model: "auto", checkAuth: .bearer),
        InferencePlan(
            id: "step-plan", name: "Step Plan", vendor: "StepFun", logo: "stepfun",
            keyName: "STEP_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.stepfun.com/step_plan",
                                 "https://platform.stepfun.com/interface-key"),
                        endpoint("International", "https://api.stepfun.ai/step_plan",
                                 "https://platform.stepfun.ai/step-plan")],
            claudeCodeKeyVar: token, model: "step-5-preview", checkAuth: .bearer),
        InferencePlan(
            id: "mimo-token-plan", name: "MiMo Token Plan", vendor: "Xiaomi", logo: "xiaomimimo",
            keyName: "MIMO_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://token-plan-cn.xiaomimimo.com/anthropic",
                                 "https://platform.xiaomimimo.com/#/console/plan-manage"),
                        endpoint("Singapore", "https://token-plan-sgp.xiaomimimo.com/anthropic",
                                 "https://platform.xiaomimimo.com/#/console/plan-manage"),
                        endpoint("Europe", "https://token-plan-ams.xiaomimimo.com/anthropic",
                                 "https://platform.xiaomimimo.com/#/console/plan-manage")],
            claudeCodeKeyVar: token, model: "mimo-v2.6-pro", checkAuth: .bearer),
        InferencePlan(
            id: "tencent-coding-plan", name: "Tencent Coding Plan", vendor: "Tencent Cloud", logo: "tencentcloud",
            keyName: "TENCENT_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.lkeap.cloud.tencent.com/coding/anthropic",
                                 "https://console.cloud.tencent.com/tokenhub/codingplan")],
            claudeCodeKeyVar: token, model: "tc-code-latest", checkAuth: .bearer),
        InferencePlan(
            id: "tencent-token-plan", name: "Tencent Token Plan", vendor: "Tencent Cloud", logo: "tencentcloud",
            keyName: "TENCENT_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.lkeap.cloud.tencent.com/plan/anthropic",
                                 "https://console.cloud.tencent.com/tokenhub/tokenplan")],
            claudeCodeKeyVar: token, model: "tc-code-latest", checkAuth: .bearer),
        InferencePlan(
            id: "qianfan-token-plan", name: "Qianfan Token Plan", vendor: "Baidu", logo: "baiducloud",
            keyName: "QIANFAN_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://qianfan.baidubce.com/anthropic/tokenplan/personal",
                                 "https://console.bce.baidu.com/qianfan/resource/token-plan")],
            claudeCodeKeyVar: token, model: "qianfan-code-latest", checkAuth: nil),
        InferencePlan(
            id: "synthetic", name: "Synthetic", vendor: "Synthetic", logo: "synthetic",
            keyName: "SYNTHETIC_API_KEY",
            endpoints: [endpoint("Global", "https://api.synthetic.new/anthropic", "https://synthetic.new")],
            claudeCodeKeyVar: token, model: "syn:large:vision", checkAuth: .bearer),
        InferencePlan(
            id: "ollama-cloud", name: "Ollama Cloud", vendor: "Ollama", logo: "ollama",
            keyName: "OLLAMA_API_KEY",
            endpoints: [endpoint("Global", "https://ollama.com", "https://ollama.com/settings/keys")],
            claudeCodeKeyVar: token, model: "glm-5.3-flash", checkAuth: .bearer),
        InferencePlan(
            id: "devpass", name: "DevPass", vendor: "LLM Gateway", logo: "llmgateway",
            keyName: "DEVPASS_KEY",
            endpoints: [endpoint("Global", "https://api.llmgateway.io", "https://devpass.llmgateway.io/dashboard")],
            claudeCodeKeyVar: token, model: nil, checkAuth: nil),
        InferencePlan(
            id: "vercel-token-pass", name: "Token Pass", vendor: "Vercel", logo: "vercel",
            keyName: "VERCEL_TOKEN_PASS_KEY",
            endpoints: [endpoint("Global", "https://ai-gateway.vercel.sh/claude-code",
                                 "https://vercel.com/docs/ai-gateway/token-pass")],
            claudeCodeKeyVar: token, model: "zai/glm-5.3", checkAuth: nil),
        InferencePlan(
            id: "charm-hyper", name: "Charm Hyper", vendor: "Charm", logo: "charm",
            keyName: "CHARM_HYPER_KEY",
            endpoints: [endpoint("Global", "https://hyper.charm.land", "https://hyper.charm.land")],
            claudeCodeKeyVar: "ANTHROPIC_API_KEY", model: "deepseek-v4-pro", checkAuth: .xApiKey),
        InferencePlan(
            id: "zenmux-builder-plan", name: "ZenMux Builder Plan", vendor: "ZenMux", logo: "zenmux",
            keyName: "ZENMUX_PLAN_KEY",
            endpoints: [endpoint("Global", "https://zenmux.ai/api/anthropic",
                                 "https://zenmux.ai/platform/subscription")],
            claudeCodeKeyVar: token, model: "claude-sonnet-4-6", checkAuth: .bearer),
        InferencePlan(
            id: "poe", name: "Poe subscription", vendor: "Poe", logo: "poe",
            keyName: "POE_API_KEY",
            endpoints: [endpoint("Global", "https://api.poe.com", "https://poe.com/api/keys")],
            claudeCodeKeyVar: token, model: "claude-sonnet-4.6", checkAuth: nil),
        InferencePlan(
            id: "standard-compute", name: "Standard Compute", vendor: "Standard Compute", logo: "standardcompute",
            keyName: "STANDARD_COMPUTE_KEY",
            endpoints: [endpoint("Global", "https://api.stdcmpt.com", "https://standardcompute.com/dashboard")],
            claudeCodeKeyVar: token, model: "standardcompute", checkAuth: .bearer),
        InferencePlan(
            id: "atlas-coding-plan", name: "Atlas Cloud Coding Plan", vendor: "Atlas Cloud", logo: "atlascloud",
            keyName: "ATLAS_CODING_PLAN_KEY",
            endpoints: [endpoint("Global", "https://api.atlascloud.ai",
                                 "https://www.atlascloud.ai/console/coding-plan")],
            claudeCodeKeyVar: token, model: "deepseek-ai/deepseek-v4-pro", checkAuth: .bearer),
        InferencePlan(
            id: "cheapestinference", name: "CheapestInference", vendor: "CheapestInference", logo: "cheapestinference",
            keyName: "CHEAPESTINFERENCE_KEY",
            endpoints: [endpoint("Global", "https://api.cheapestinference.com/anthropic",
                                 "https://cheapestinference.com/docs/pricing/plans/")],
            claudeCodeKeyVar: token, model: "kimi-k3", checkAuth: .bearer),
        InferencePlan(
            id: "jdcloud-token-plan", name: "JD Cloud Token Plan", vendor: "JD Cloud", logo: "jdcloud",
            keyName: "JDCLOUD_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://modelservice.jdcloud.com/tokenPlan/anthropic",
                                 "https://console.jdcloud.com")],
            claudeCodeKeyVar: token, model: "maas-token-latest", checkAuth: .bearer),
        InferencePlan(
            id: "astron-coding-plan", name: "Astron Coding Plan", vendor: "iFlytek", logo: "iflytek",
            keyName: "ASTRON_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://maas-coding-api.cn-huabei-1.xf-yun.com/anthropic",
                                 "https://maas.xfyun.cn/packageSubscription")],
            claudeCodeKeyVar: token, model: "astron-code-latest", checkAuth: .bearer),
        InferencePlan(
            id: "astron-token-plan", name: "Astron Token Plan", vendor: "iFlytek", logo: "iflytek",
            keyName: "ASTRON_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://maas-token-api.cn-huabei-1.xf-yun.com/anthropic",
                                 "https://maas.xfyun.cn/tokenPlan/subscription")],
            claudeCodeKeyVar: token, model: "xopglm52", checkAuth: .bearer),
        InferencePlan(
            id: "unicom-coding-plan", name: "Unicom Cloud Coding Plan", vendor: "China Unicom", logo: "unicom",
            keyName: "UNICOM_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://aigw-gzgy2.cucloud.cn:8443",
                                 "https://support.cucloud.cn/document/127/591/2357.html?arcid=7015&id=2357")],
            claudeCodeKeyVar: token, model: "aisp-auto-route", checkAuth: nil),
        InferencePlan(
            id: "ctyun-coding-plan", name: "Ctyun Coding Token Plan", vendor: "China Telecom", logo: "ctyun",
            keyName: "CTYUN_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://ai.ctaigw.cn/coding",
                                 "https://ctxirang.ctyun.cn/maas/codingPlan")],
            claudeCodeKeyVar: token, model: "DeepSeek-V4-Pro", checkAuth: nil),
        InferencePlan(
            id: "huawei-token-plan", name: "Huawei Cloud Token Plan", vendor: "Huawei Cloud", logo: "huaweicloud",
            keyName: "HUAWEI_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.modelarts-maas.com/plan/anthropic",
                                 "https://support.huaweicloud.com/Token-plan-maas/tokenplan-maas-0001.html")],
            claudeCodeKeyVar: token, model: "glm-5.3", checkAuth: .bearer),
        InferencePlan(
            id: "scnet-token-plan", name: "SCNet Token Plan", vendor: "SCNet", logo: "scnet",
            keyName: "SCNET_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.scnet.cn/api/llm/anthropic",
                                 "https://www.scnet.cn/ac/openapi/doc/2.0/moduleapi/plans/token-plan.html")],
            claudeCodeKeyVar: token, model: "GLM-5.3", checkAuth: nil),
        InferencePlan(
            id: "compshare-coding-plan", name: "Compshare Coding Plan", vendor: "UCloud", logo: "ucloud",
            keyName: "COMPSHARE_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://cp.compshare.cn",
                                 "https://console.compshare.cn")],
            claudeCodeKeyVar: token, model: "glm-5.1", checkAuth: .bearer),
        InferencePlan(
            id: "kwaikat-coding-plan", name: "KwaiKAT Coding Plan", vendor: "Kuaishou StreamLake", logo: "kwaikat",
            keyName: "KWAIKAT_CODING_PLAN_KEY",
            endpoints: [endpoint("International", "https://vanchin.streamlake.ai/api/gateway/coding/kat-coder-pro-v2.5/claude-code-proxy",
                                 "https://console.streamlake.ai")],
            claudeCodeKeyVar: token, model: "kat-coder-pro-v2.5", checkAuth: .bearer),
        InferencePlan(
            id: "kuae-coding-plan", name: "KUAE AI Coding Plan", vendor: "Moore Threads", logo: "mthreads",
            keyName: "KUAE_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://coding-plan-endpoint.kuaecloud.net",
                                 "https://coding-plan.kuaecloud.net/apikey")],
            claudeCodeKeyVar: token, model: "GLM-4.7", checkAuth: .bearer),
        InferencePlan(
            id: "taotoken-coding-plan", name: "TaoToken Coding Plan", vendor: "TaoToken", logo: "taotoken",
            keyName: "TAOTOKEN_CODING_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.taotoken.net/coding",
                                 "https://taotoken.net/console/api-keys")],
            claudeCodeKeyVar: token, model: "glm-5.3-flash", checkAuth: nil),
        InferencePlan(
            id: "taotoken-token-plan", name: "TaoToken Token Plan", vendor: "TaoToken", logo: "taotoken",
            keyName: "TAOTOKEN_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://api.taotoken.net/token",
                                 "https://taotoken.net/console/api-keys")],
            claudeCodeKeyVar: token, model: "glm-5.3", checkAuth: nil),
        InferencePlan(
            id: "sensenova-token-plan", name: "SenseNova Token Plan", vendor: "SenseTime", logo: "sensenova",
            keyName: "SENSENOVA_TOKEN_PLAN_KEY",
            endpoints: [endpoint("China", "https://token.sensenova.cn",
                                 "https://platform.sensenova.cn/console/keys")],
            claudeCodeKeyVar: token, model: "sensenova-6.8-flash-lite", checkAuth: .bearer),
    ]

    public static func plan(forKey name: String) -> InferencePlan? { all.first { $0.keyName == name } }

    /// The key check for a plan: an empty Messages request to each site in
    /// turn. Every site with a `checkAuth` answered a fake key with 401 or
    /// 403 before reading the body (docs/coding-plans.md), so a real key
    /// comes back as a request error (400 or 422) without running anything.
    /// TaoToken answers a fake key with a 400, which would read as valid,
    /// so it has none.
    static func probe(for plan: InferencePlan) -> APIKeyProbe? {
        guard let auth = plan.checkAuth else { return nil }
        return APIKeyProbe(hosts: plan.endpoints.map(\.messagesURL), method: "POST",
                           body: Data("{}".utf8), auth: auth, worksOnBadRequest: true)
    }
}
