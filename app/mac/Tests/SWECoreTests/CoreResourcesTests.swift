import Foundation
import Testing
@testable import SWECore

/// Where SWECore's resources come from in a packaged app or widget
/// extension. `Bundle.module` there would stop the process, so these check
/// it is never reached.
@Suite struct CoreResourcesTests {
    /// A bundle folder `name` laid out as macOS bundles are, optionally with
    /// a SWECore bundle holding one logo in its Contents/Resources. The
    /// native build system makes that bundle flat; `nestedCore` lays it out
    /// as swiftbuild does, with its own Contents/Info.plist and Resources.
    func makeBundle(_ name: String, withCore: Bool, nestedCore: Bool = false) throws -> (Bundle, URL) {
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
            var files = core
            if nestedCore {
                let coreContents = core.appendingPathComponent("Contents", isDirectory: true)
                files = coreContents.appendingPathComponent("Resources", isDirectory: true)
                try FileManager.default.createDirectory(at: files, withIntermediateDirectories: true)
                let corePlist: [String: Any] = ["CFBundleIdentifier": "test.core.\(UUID().uuidString)",
                                                "CFBundlePackageType": "BNDL"]
                try PropertyListSerialization.data(fromPropertyList: corePlist, format: .xml, options: 0)
                    .write(to: coreContents.appendingPathComponent("Info.plist"))
            }
            try FileManager.default.createDirectory(at: files, withIntermediateDirectories: true)
            try Data("<svg/>".utf8).write(to: files.appendingPathComponent("harness-test-color.svg"))
        }
        return (try #require(Bundle(url: bundleURL)), root)
    }

    @Test func anExtensionFindsTheCopyInItsOwnResources() throws {
        let (appex, root) = try makeBundle("SWEWidgets.appex", withCore: true)
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

    @Test func theCopyResolvesInEitherBuildSystemsLayout() throws {
        for nested in [false, true] {
            let (app, root) = try makeBundle("SWE.app", withCore: true, nestedCore: nested)
            defer { try? FileManager.default.removeItem(at: root) }
            let found = CoreResources.resolve(main: app) {
                Issue.record("fell back to Bundle.module")
                return .main
            }
            #expect(found.bundleURL.lastPathComponent == CoreResources.bundleName, "nested: \(nested)")
            #expect(found.url(forResource: "harness-test-color", withExtension: "svg") != nil, "nested: \(nested)")
        }
    }

    @Test func aPackagedBundleWithoutTheCopyFindsNothingRatherThanCrashing() throws {
        for name in ["SWEWidgets.appex", "SWE.app"] {
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
