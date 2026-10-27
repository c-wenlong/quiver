import Foundation
import Testing
@testable import QuiverCore

/// Where QuiverCore's resources come from in a packaged app or widget
/// extension. `Bundle.module` there would stop the process, so these check
/// it is never reached.
@Suite struct CoreResourcesTests {
    /// A bundle folder `name` laid out as macOS bundles are, optionally with
    /// a QuiverCore bundle holding one logo in its Contents/Resources.
    func makeBundle(_ name: String, withCore: Bool) throws -> (Bundle, URL) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("core-resources-\(UUID().uuidString)", isDirectory: true)
        let bundleURL = root.appendingPathComponent(name, isDirectory: true)
        let contents = bundleURL.appendingPathComponent("Contents", isDirectory: true)
        let resources = contents.appendingPathComponent("Resources", isDirectory: true)
        try FileManager.default.createDirectory(at: resources, withIntermediateDirectories: true)
        let plist: [String: Any] = ["CFBundleIdentifier": "test.\(UUID().uuidString)", "CFBundleExecutable": "Test",
                                    "CFBundlePackageType": "XPC!"]
        try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0)
            .write(to: contents.appendingPathComponent("Info.plist"))
        if withCore {
            let core = resources.appendingPathComponent(CoreResources.bundleName, isDirectory: true)
            try FileManager.default.createDirectory(at: core, withIntermediateDirectories: true)
            try Data("<svg/>".utf8).write(to: core.appendingPathComponent("harness-test-color.svg"))
        }
        return (try #require(Bundle(url: bundleURL)), root)
    }

    @Test func anExtensionFindsTheCopyInItsOwnResources() throws {
        let (appex, root) = try makeBundle("QuiverWidgets.appex", withCore: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let found = CoreResources.resolve(main: appex) {
            Issue.record("fell back to Bundle.module")
            return .main
        }
        #expect(found.bundleURL.lastPathComponent == CoreResources.bundleName)
        #expect(found.bundleURL.deletingLastPathComponent().standardizedFileURL
                == appex.resourceURL?.standardizedFileURL)
        #expect(found.url(forResource: "harness-test-color", withExtension: "svg") != nil)
    }

    @Test func aPackagedBundleWithoutTheCopyFindsNothingRatherThanCrashing() throws {
        for name in ["QuiverWidgets.appex", "Quiver.app"] {
            let (main, root) = try makeBundle(name, withCore: false)
            defer { try? FileManager.default.removeItem(at: root) }
            let found = CoreResources.resolve(main: main) {
                Issue.record("\(name) fell back to Bundle.module")
                return .main
            }
            #expect(found.bundleURL.standardizedFileURL == main.bundleURL.standardizedFileURL, "\(name)")
            #expect(found.url(forResource: "harness-claude-color", withExtension: "svg") == nil, "\(name)")
        }
    }

    @Test func swiftRunAndTestsUseTheBuildFolder() throws {
        let (folder, root) = try makeBundle("debug", withCore: false)
        defer { try? FileManager.default.removeItem(at: root) }
        var fellBack = false
        _ = CoreResources.resolve(main: folder) {
            fellBack = true
            return .main
        }
        #expect(fellBack)
    }
}
