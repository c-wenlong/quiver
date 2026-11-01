import AppKit
import Foundation

/// One running instance of a desktop app: its pid and argv, which is how a
/// `--user-data-dir` instance is told apart from the default one.
public struct RunningInstance: Equatable, Sendable {
    public var pid: Int32
    public var arguments: [String]

    public init(pid: Int32, arguments: [String]) {
        self.pid = pid
        self.arguments = arguments
    }
}

/// Lists the running instances of a desktop app and brings one to the
/// front. The real implementation reads `NSRunningApplication`; tests use a
/// fake so `AccountsModel.openApp` can be exercised without launching or
/// activating anything.
@MainActor
public protocol RunningInstances {
    /// Every running process that belongs to the app bundle at `path`.
    func instances(ofAppAt path: String) -> [RunningInstance]
    /// Focuses one already-running instance and asks it to reopen a window
    /// (the same reopen event `open -a` sends); a dead pid is ignored.
    func activate(pid: Int32)
}

/// The `NSRunningApplication` implementation. SWECore already links
/// AppKit (MarkdownStyleAttributes), so the system call can live here while
/// AccountsModel keeps a fake in tests.
@MainActor
public struct AppRunningInstances: RunningInstances {
    public init() {}

    public func instances(ofAppAt path: String) -> [RunningInstance] {
        let target = URL(fileURLWithPath: path).standardizedFileURL
        // bundleURL can be nil for an app re-launched while its bundle was
        // replaced; fall back to the bundle id from the app's own plist.
        let bundleID = Bundle(url: target)?.bundleIdentifier
        return NSWorkspace.shared.runningApplications.compactMap { app in
            let belongs = app.bundleURL.map { $0.standardizedFileURL == target }
                ?? (bundleID != nil && app.bundleIdentifier == bundleID)
            guard belongs, app.processIdentifier > 0 else { return nil }
            return RunningInstance(pid: app.processIdentifier,
                                   arguments: ProcessArguments.of(pid: app.processIdentifier))
        }
    }

    public func activate(pid: Int32) {
        guard let app = NSRunningApplication(processIdentifier: pid) else { return }
        // NSApp is nil in a context with no application (a test runner):
        // activating still works, only the yield is skipped.
        if let shared = NSApp { shared.yieldActivation(to: app) }
        app.activate()
        // Activation alone leaves a windowless instance showing nothing;
        // `open -a` also sends a reopen ('aevt'/'rapp'), so do the same. The
        // result is ignored: core events don't need Automation consent, and
        // the no-prompt flag is a guard, not a request.
        let event = NSAppleEventDescriptor(
            eventClass: AEEventClass(kCoreEventClass),
            eventID: AEEventID(kAEReopenApplication),
            targetDescriptor: NSAppleEventDescriptor(processIdentifier: pid),
            returnID: AEReturnID(kAutoGenerateReturnID),
            transactionID: AETransactionID(kAnyTransactionID))
        AESendMessage(event.aeDesc, nil,
                      AESendMode(kAENoReply | kAEDoNotPromptForUserConsent),
                      kAEDefaultTimeout)
    }
}
