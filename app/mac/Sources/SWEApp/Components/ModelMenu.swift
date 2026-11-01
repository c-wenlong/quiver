import SWECore
import SwiftUI

/// The launch sheets' Model menu (#914): "Harness default", the
/// harness's recent models newest first, then "Other…", which opens a
/// field for a typed model id. A harness that can't take a model at
/// launch gets the menu disabled on "Harness default", what it runs.
struct ModelMenu: View {
    /// The picked harness's recent models, newest first.
    var recent: [String]
    @Binding var pick: ModelPick
    /// Whether the picked harness's launch honours a `model`.
    var supported: Bool
    var label: String? = nil
    var width: CGFloat? = 280

    private var options: [(value: ModelChoice, title: String)] {
        Self.options(for: recent).map { (value: $0, title: $0.title) }
    }

    /// One row per model, newest kept: a repeat would give the menu two
    /// rows with one id.
    static func options(for recent: [String]) -> [ModelChoice] {
        var seen = Set<String>()
        return ModelPick.options(recent.filter { seen.insert($0).inserted })
    }

    /// Each recent model's brand logo, as every view naming a model draws.
    /// A model listed twice keeps one entry: uniqueKeysWithValues traps on
    /// a repeated key, and the list comes from logs, not a set.
    private var icons: [ModelChoice: String] {
        Self.icons(for: recent)
    }

    static func icons(for recent: [String]) -> [ModelChoice: String] {
        Dictionary(recent.compactMap { model in
            ModelVendor.of(logged: model).icon.map { (ModelChoice.recent(model), $0) }
        }, uniquingKeysWith: { first, _ in first })
    }

    private var shown: ModelChoice { supported ? pick.choice : .harnessDefault }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            SelectMenu(label: label, options: options,
                       selection: Binding(get: { shown }, set: { pick.pick($0) }),
                       width: width,
                       // A picked model a refresh dropped from the list
                       // still names itself on the closed menu.
                       title: shown.title,
                       icons: icons,
                       symbols: [.harnessDefault: "sparkles", .other: "pencil"])
                .disabled(!supported)
            if supported, pick.choice == .other {
                TextField("Model id, e.g. sonnet; blank for the default", text: $pick.otherText)
                    .sweField(monospaced: true)
            }
            if !supported {
                Text("This harness can't take a model at launch; it starts on its default.")
                    .font(Theme.caption).foregroundStyle(Theme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}
