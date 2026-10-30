import SwiftUI
import Testing
@testable import QuiverApp

/// A Frontmatter row's binding outlives its field when a reload or revert
/// shrinks the list; reading it then must not trap.
@Suite @MainActor struct FrontmatterFieldsTests {
    private final class Box { var fields: [(key: String, value: String)] = [] }

    @Test func aRowPastTheEndReadsEmptyAndDropsWrites() {
        let box = Box()
        box.fields = [("name", "demo"), ("description", "old")]
        let fields = Binding(get: { box.fields }, set: { box.fields = $0 })
        let row = FrontmatterCard.value(fields, at: 1)
        #expect(row.wrappedValue == "old")
        row.wrappedValue = "new"
        #expect(box.fields[1].value == "new")

        box.fields = [("name", "demo")]
        #expect(row.wrappedValue == "")
        row.wrappedValue = "late"
        #expect(box.fields.count == 1)
        #expect(box.fields[0].value == "demo")
    }
}
