import Foundation
import UIKit
import UniformTypeIdentifiers

/// One thing another app handed the share sheet, read and ready to send.
/// Text and links go in the message; images and files upload first.
enum SharedPayload: Equatable {
    case text(String)
    case link(URL)
    /// An image on disk (copied into the extension's temp folder), sent
    /// as a downscaled JPEG.
    case image(name: String, file: URL)
    /// An image the other app handed over already as a JPEG (it shared a
    /// decoded image rather than a file).
    case imageData(name: String, data: Data)
    /// Any other file on disk, streamed to the hub as it is.
    case file(name: String, file: URL, size: Int)

    var isUpload: Bool {
        switch self {
        case .text, .link: false
        case .image, .imageData, .file: true
        }
    }

    /// The name it uploads under (`photo.heic` goes up as `photo.jpg`).
    var uploadName: String? {
        switch self {
        case .text, .link: nil
        case .image(let name, _), .imageData(let name, _), .file(let name, _, _): name
        }
    }
}

/// How the share sheet reads an item provider: the first matching kind
/// wins, so a photo that also offers a file URL is still a photo, and a
/// web page that also offers its title as text is still a link.
enum SharedKind: Equatable {
    case image(String)
    case fileURL
    case link
    case text
    case file(String)
    case unsupported

    static func of(_ typeIdentifiers: [String]) -> SharedKind {
        let types = typeIdentifiers.compactMap(UTType.init)
        if let t = types.first(where: { $0.conforms(to: .image) }) { return .image(t.identifier) }
        if types.contains(where: { $0.conforms(to: .fileURL) }) { return .fileURL }
        if types.contains(where: { $0.conforms(to: .url) }) { return .link }
        if types.contains(where: { $0.conforms(to: .plainText) }) { return .text }
        if let t = types.first(where: { $0.conforms(to: .data) }) { return .file(t.identifier) }
        return .unsupported
    }
}

/// Builds the message the share sheet sends and says why Send is off.
enum ShareDraft {
    /// The typed message first, then each shared text and link on its own
    /// paragraph. A link Safari also sent as text appears once.
    static func text(message: String, payloads: [SharedPayload]) -> String {
        var parts: [String] = []
        func add(_ s: String) {
            let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
            if !t.isEmpty, !parts.contains(t) { parts.append(t) }
        }
        add(message)
        for payload in payloads {
            switch payload {
            case .text(let s): add(s)
            case .link(let url): add(url.absoluteString)
            default: break
            }
        }
        return parts.joined(separator: "\n\n")
    }

    /// Keeps at most the phone's per-message cap of files, in order;
    /// texts and links all stay. The second value names what was cut.
    static func capped(_ payloads: [SharedPayload]) -> ([SharedPayload], String?) {
        var kept: [SharedPayload] = []
        var files = 0, dropped = 0
        for payload in payloads {
            if payload.isUpload {
                files += 1
                if files > AttachmentRules.maxPerMessage { dropped += 1; continue }
            }
            kept.append(payload)
        }
        guard dropped > 0 else { return (kept, nil) }
        return (kept, "Only the first \(AttachmentRules.maxPerMessage) files are sent: \(dropped) left out.")
    }
}

/// Reads the share sheet's item providers into payloads, one at a time.
/// Files are copied into `folder` (the provider's own copy is gone once
/// its callback returns), checked against the hub's caps by size alone
/// and never read into memory here.
enum ShareInbox {
    static func load(_ providers: [NSItemProvider], into folder: URL) async -> (payloads: [SharedPayload], notices: [String]) {
        try? FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var payloads: [SharedPayload] = []
        var notices: [String] = []
        for (i, provider) in providers.enumerated() {
            switch await load(provider, index: i, into: folder) {
            case .success(let payload): payloads.append(payload)
            case .failure(let refusal): notices.append(refusal.message)
            }
        }
        return (payloads, notices)
    }

    struct Refusal: Error, Equatable { let message: String }

    static func load(_ provider: NSItemProvider, index: Int, into folder: URL) async -> Result<SharedPayload, Refusal> {
        let fallbackName = provider.suggestedName ?? "item-\(index + 1)"
        switch SharedKind.of(provider.registeredTypeIdentifiers) {
        case .image(let type):
            if let url = await copyFile(provider, type: type, into: folder, index: index) {
                return .success(.image(name: AttachmentRules.jpegName(url.lastPathComponent), file: url))
            }
            // Some apps share a decoded image instead of a file.
            if let image = await loadItem(provider, type: type) as? UIImage, let data = PhotoPrep.jpeg(from: image) {
                return .success(.imageData(name: AttachmentRules.jpegName(fallbackName), data: data))
            }
            return .failure(Refusal(message: "\(fallbackName) couldn't be read as an image."))
        case .fileURL:
            guard let url = await loadItem(provider, type: UTType.fileURL.identifier) as? URL else {
                return .failure(Refusal(message: "\(fallbackName) couldn't be read."))
            }
            return copyLocal(url, into: folder, index: index)
        case .link:
            switch await loadItem(provider, type: UTType.url.identifier) {
            case let url as URL where url.isFileURL: return copyLocal(url, into: folder, index: index)
            case let url as URL: return .success(.link(url))
            case let s as String: return URL(string: s).map { .success(.link($0)) } ?? .success(.text(s))
            default: return .failure(Refusal(message: "That link couldn't be read."))
            }
        case .text:
            switch await loadItem(provider, type: UTType.plainText.identifier) {
            case let s as String: return .success(.text(s))
            case let s as NSAttributedString: return .success(.text(s.string))
            case let url as URL where url.isFileURL: return copyLocal(url, into: folder, index: index)
            case let data as Data: return .success(.text(String(decoding: data, as: UTF8.self)))
            default: return .failure(Refusal(message: "That text couldn't be read."))
            }
        case .file(let type):
            guard let url = await copyFile(provider, type: type, into: folder, index: index) else {
                return .failure(Refusal(message: "\(fallbackName) couldn't be read."))
            }
            return checked(url)
        case .unsupported:
            return .failure(Refusal(message: "\(fallbackName) can't be attached: images, PDFs and text files only."))
        }
    }

    /// A file that's on disk in `folder` as the payload it uploads as,
    /// or why the hub would refuse it. Images become JPEGs on the way up,
    /// so only their type matters, never their original size.
    static func checked(_ url: URL) -> Result<SharedPayload, Refusal> {
        let name = url.lastPathComponent
        let ext = AttachmentRules.ext(name)
        if AttachmentRules.readableImages.contains(ext) || AttachmentRules.needsConversion(name) {
            return .success(.image(name: AttachmentRules.jpegName(name), file: url))
        }
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0
        if let why = AttachmentRules.refusal(name: name, size: size) { return .failure(Refusal(message: why)) }
        return .success(.file(name: name, file: url, size: size))
    }

    /// Copies a file URL another app shared into `folder`.
    private static func copyLocal(_ url: URL, into folder: URL, index: Int) -> Result<SharedPayload, Refusal> {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        guard let copy = copy(url, into: folder, index: index) else {
            return .failure(Refusal(message: "\(url.lastPathComponent) couldn't be read."))
        }
        return checked(copy)
    }

    /// `folder/<index>/<name>`: one subfolder per item, so two shared
    /// files with the same name both survive.
    static func copy(_ url: URL, into folder: URL, index: Int, name: String? = nil) -> URL? {
        let dir = folder.appendingPathComponent("\(index)", isDirectory: true)
        let dest = dir.appendingPathComponent(name ?? url.lastPathComponent)
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            try? FileManager.default.removeItem(at: dest)
            try FileManager.default.copyItem(at: url, to: dest)
            return dest
        } catch {
            return nil
        }
    }

    private static func copyFile(_ provider: NSItemProvider, type: String, into folder: URL, index: Int) async -> URL? {
        guard provider.hasItemConformingToTypeIdentifier(type) else { return nil }
        let suggested = provider.suggestedName
        return await withCheckedContinuation { done in
            _ = provider.loadFileRepresentation(forTypeIdentifier: type) { url, _ in
                // The provider deletes `url` once this returns: copy now.
                guard let url else { return done.resume(returning: nil) }
                done.resume(returning: copy(url, into: folder, index: index,
                                            name: fileName(suggested: suggested, provided: url, type: type)))
            }
        }
    }

    /// The provider's suggested name when it has one (its file is often a
    /// temp name), with the type's extension when the name lacks one.
    static func fileName(suggested: String?, provided: URL, type: String) -> String {
        guard let suggested, !suggested.isEmpty else { return provided.lastPathComponent }
        let safe = suggested.replacingOccurrences(of: "/", with: "-")
        guard (safe as NSString).pathExtension.isEmpty else { return safe }
        let ext = provided.pathExtension.isEmpty ? UTType(type)?.preferredFilenameExtension : provided.pathExtension
        return ext.map { "\(safe).\($0)" } ?? safe
    }

    private static func loadItem(_ provider: NSItemProvider, type: String) async -> NSSecureCoding? {
        await withCheckedContinuation { done in
            provider.loadItem(forTypeIdentifier: type, options: nil) { item, _ in done.resume(returning: item) }
        }
    }
}
