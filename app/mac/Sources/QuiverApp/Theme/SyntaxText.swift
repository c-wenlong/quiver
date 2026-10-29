import QuiverCore
import SwiftUI

extension AttributedString {
    /// `code` tokenised in `language`, each run in its `Theme.syntax`
    /// colour (plain runs keep the ambient foreground).
    init(highlighting code: String, language: String?) {
        self.init()
        for token in SyntaxHighlight.tokens(code, language: language ?? "") {
            var run = AttributedString(token.text)
            if token.kind != .plain { run.foregroundColor = Theme.syntax(token.kind) }
            append(run)
        }
    }
}
