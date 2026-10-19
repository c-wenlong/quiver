import Foundation

/// A model plan: a subscription on a model provider that you use through a
/// harness (Claude Code, OpenCode…) rather than a CLI of its own. Settings ›
/// Providers › Inference lists them apart from the harnesses, since they
/// run no sessions. Quiver reads no usage for them yet.
public struct InferencePlan: Identifiable, Sendable, Equatable {
    public var id: String
    public var name: String
    /// The model provider that sells the plan.
    public var vendor: String
    /// The `HarnessIcons` id for its logo.
    public var icon: String
    /// Commands on PATH that mean it's set up on this Mac. Only existence
    /// is checked.
    public var commands: [String]
}

public enum InferenceCatalog {
    public static let all: [InferencePlan] = [
        InferencePlan(id: "ark-coding-plan", name: "Ark Coding Plan", vendor: "Volcano Engine",
                      icon: "ark", commands: ["arkcli"]),
        InferencePlan(id: "ark-agent-plan", name: "Ark Agent Plan", vendor: "Volcano Engine",
                      icon: "ark", commands: ["arkcli"]),
    ]

    /// Ids of the plans whose commands are on this Mac, in catalog order.
    public static func detected(commandExists: (String) -> Bool) -> [String] {
        all.filter { $0.commands.contains(where: commandExists) }.map(\.id)
    }
}
