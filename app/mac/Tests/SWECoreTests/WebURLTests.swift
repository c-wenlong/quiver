import Foundation
import Testing
@testable import SWECore

/// Buttons that open a URL from a swe reply or an API payload must only
/// ever reach a browser — `file:`, `smb:` or an app scheme would hand
/// the click to another program.
@Suite struct WebURLTests {
    @Test func webSchemesOpenTheRestDoesNot() {
        #expect(URL(string: "https://github.com/x")!.isWebURL)
        #expect(URL(string: "HTTP://EXAMPLE.COM")!.isWebURL)
        #expect(URL(string: "http://127.0.0.1:8477/v1")!.isWebURL)
        #expect(!URL(string: "file:///etc/passwd")!.isWebURL)
        #expect(!URL(string: "smb://nas/share")!.isWebURL)
        #expect(!URL(string: "x-apple.systempreferences:root")!.isWebURL)
        #expect(!URL(string: "mailto:a@b.c")!.isWebURL)
        #expect(!URL(string: "javascript:alert(1)")!.isWebURL)
        #expect(!URL(filePath: "/tmp/x").isWebURL)
    }
}
