import SWECore
import SwiftUI

extension AttributedString {
    /// `code` tokenised in `language`, each run in its `Theme.syntax`
    /// colour (plain runs keep the ambient foreground).
    init(highlighting code: String, language: String?) {
        self.init(tokens: SyntaxHighlight.tokens(code, language: language ?? ""))
    }

    /// Styled runs in `Theme.syntax` colours (plain runs keep the
    /// ambient foreground).
    init(tokens: [SyntaxToken]) {
        self.init()
        for token in tokens {
            var run = AttributedString(token.text)
            if token.kind != .plain { run.foregroundColor = Theme.syntax(token.kind) }
            append(run)
        }
    }
}
