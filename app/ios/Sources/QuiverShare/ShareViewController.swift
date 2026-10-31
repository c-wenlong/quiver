import SwiftUI
import UIKit

/// The share extension's entry point (`NSExtensionPrincipalClass`). Reads
/// what was shared into a temp folder, hosts the sheet, and ends the
/// request when it sends or is cancelled.
@objc(ShareViewController)
final class ShareViewController: UIViewController {
    private let folder = FileManager.default.temporaryDirectory
        .appendingPathComponent("share-\(UUID().uuidString)", isDirectory: true)
    private var model: ShareModel?

    override func viewDidLoad() {
        super.viewDidLoad()
        // Credentials in the shared Keychain group, the hub list in the App
        // Group: the store the app writes. The extension's own defaults
        // never held a pairing, so there's no old index to move.
        let model = ShareModel(store: KeychainHubStore(legacyDefaults: nil)) {
            HubClient(baseURL: $0.baseURL, credential: $0.credential.credential)
        }
        self.model = model
        let sheet = ShareSheet(model: model, close: { [weak self] sent in self?.finish(sent: sent) })
        let host = UIHostingController(rootView: sheet)
        addChild(host)
        host.view.frame = view.bounds
        host.view.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        view.addSubview(host.view)
        host.didMove(toParent: self)

        let providers = (extensionContext?.inputItems as? [NSExtensionItem] ?? [])
            .flatMap { $0.attachments ?? [] }
        let folder = self.folder
        Task {
            let read = await ShareInbox.load(providers, into: folder)
            await model.start(payloads: read.payloads, notices: read.notices)
        }
    }

    private func finish(sent: Bool) {
        try? FileManager.default.removeItem(at: folder)
        if sent {
            extensionContext?.completeRequest(returningItems: nil)
        } else {
            extensionContext?.cancelRequest(withError: CocoaError(.userCancelled))
        }
    }
}
