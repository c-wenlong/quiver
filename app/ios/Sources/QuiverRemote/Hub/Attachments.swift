import Foundation
import ImageIO
import Observation
import UIKit
import UniformTypeIdentifiers

/// The hub's answer to an upload (`attachments.stage`): the ref the next
/// message lists, and the name and size it stored.
struct StagedAttachment: Decodable, Equatable {
    let ref: String
    var name: String? = nil
    var size: Int? = nil
}

/// What the hub takes (`src/quiver/remote/attachments.py`) and how the
/// composer words a refusal. Kept in step with the hub's caps by hand.
enum AttachmentRules {
    /// `attachments.MAX_BYTES`: 20 MB per file.
    static let maxBytes = 20 << 20
    /// `attachments.ALLOWED_EXTS`.
    static let allowedExtensions: Set<String> = [
        "png", "jpg", "jpeg", "gif", "webp", "heic",
        "pdf",
        "txt", "md", "csv", "json", "log", "diff", "patch",
    ]
    /// Images a harness reads as they are. Any other image (HEIC from the
    /// camera, TIFF, BMP) becomes a JPEG on the phone first: the hub
    /// would take a HEIC, but Claude and most models can't read one.
    static let readableImages: Set<String> = ["png", "jpg", "jpeg", "gif", "webp"]
    /// The phone's own limit per message, so a big photo pick doesn't
    /// start dozens of uploads at once. The hub has none.
    static let maxPerMessage = 10
    /// Photos are scaled so their long edge is at most this many pixels:
    /// models scale bigger images down anyway, and it keeps uploads small.
    static let photoMaxEdge = 2048
    static let photoQuality: CGFloat = 0.85

    /// What the file picker lets you choose. Anything it lets through that
    /// the hub won't take (a `.swift` file is text too) gets a notice.
    static let pickerTypes: [UTType] = {
        var types: [UTType] = [.image, .pdf, .text]
        for ext in ["md", "log", "diff", "patch", "csv", "json"] {
            if let t = UTType(filenameExtension: ext) { types.append(t) }
        }
        return types
    }()

    static func ext(_ name: String) -> String {
        (name as NSString).pathExtension.lowercased()
    }

    /// An image the phone should turn into a JPEG before upload.
    static func needsConversion(_ name: String) -> Bool {
        let e = ext(name)
        guard !e.isEmpty, !readableImages.contains(e) else { return false }
        return UTType(filenameExtension: e)?.conforms(to: .image) == true
    }

    /// `photo.heic` → `photo.jpg`.
    static func jpegName(_ name: String) -> String {
        let stem = (name as NSString).deletingPathExtension
        return (stem.isEmpty ? "photo" : stem) + ".jpg"
    }

    /// Why the hub would refuse this file, worded for the composer; nil
    /// when it would take it.
    static func refusal(name: String, size: Int) -> String? {
        let e = ext(name)
        if !allowedExtensions.contains(e) {
            return "\(name) can't be attached: images, PDFs and text files only."
        }
        if size > maxBytes { return tooBig(name: name, size: size) }
        if size == 0 { return "\(name) is empty." }
        return nil
    }

    static func tooBig(name: String, size: Int) -> String {
        "\(name) is too big (\(Self.size(size))): files can be up to 20 MB."
    }

    static func size(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }

    /// The name a staged ref was uploaded under: the hub stores
    /// `<epoch>-<6 hex>-<name>` (`attachments.stage`).
    static func displayName(ref: String) -> String {
        let base = (ref as NSString).lastPathComponent
        let parts = base.split(separator: "-", maxSplits: 2, omittingEmptySubsequences: false)
        guard parts.count == 3, !parts[0].isEmpty, parts[0].allSatisfy(\.isNumber),
              parts[1].count == 6, parts[1].allSatisfy(\.isHexDigit), !parts[2].isEmpty
        else { return base }
        return String(parts[2])
    }

    /// A transcript user message the hub wrote with attachments starts
    /// with one `@<staged path> ` per file (`inject.py`). Splits those off
    /// so the bubble shows the files' names and the typed text.
    static func splitEcho(_ text: String) -> (names: [String], text: String) {
        var names: [String] = []
        var rest = Substring(text)
        while rest.hasPrefix("@") {
            guard let space = rest.firstIndex(of: " ") else { break }
            let path = rest[rest.index(after: rest.startIndex)..<space]
            guard path.contains("/remote/inbox/") else { break }
            let base = (String(path) as NSString).lastPathComponent
            let name = displayName(ref: base)
            guard name != base else { break }
            names.append(name)
            rest = rest[rest.index(after: space)...]
        }
        return (names, names.isEmpty ? text : String(rest))
    }

    /// Under the chips while files wait and nothing is typed: the hub
    /// refuses a message with no text (the web composer says the same).
    static let needsText = "Add a message to send with the files."
}

/// Photos become JPEGs with the long edge capped, through ImageIO so a
/// 48 MP original is never decoded at full size.
enum PhotoPrep {
    /// nil when `data` isn't an image ImageIO can read.
    static func jpeg(from data: Data, maxEdge: Int = AttachmentRules.photoMaxEdge,
                     quality: CGFloat = AttachmentRules.photoQuality) -> Data? {
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              CGImageSourceGetCount(source) > 0 else { return nil }
        let props = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any]
        let width = props?[kCGImagePropertyPixelWidth] as? Int ?? maxEdge
        let height = props?[kCGImagePropertyPixelHeight] as? Int ?? maxEdge
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            // Applies the EXIF orientation, so a portrait shot stays upright.
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: min(maxEdge, max(width, height, 1)),
        ]
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary) else { return nil }
        return UIImage(cgImage: image).jpegData(compressionQuality: quality)
    }
}

/// One file in the composer, from pick to the hub's ref.
struct PendingAttachment: Identifiable, Equatable {
    enum State: Equatable {
        /// A photo being loaded and converted on the phone.
        case preparing
        /// Fraction of the bytes sent, 0 to 1.
        case uploading(Double)
        case ready(ref: String)
        case failed(String)
    }

    let id: UUID
    var name: String
    var size: Int
    var isImage: Bool
    var state: State

    var ref: String? {
        if case .ready(let ref) = state { return ref }
        return nil
    }

    var inFlight: Bool {
        switch state {
        case .preparing, .uploading: true
        case .ready, .failed: false
        }
    }
}

/// The files picked for the next message. Each uploads to the hub as soon
/// as it's picked; the send carries the refs of the finished ones. Send
/// waits while any is still uploading or has failed (retry or remove it),
/// so nothing you picked silently stays behind.
@MainActor @Observable
final class ComposerAttachments {
    private(set) var items: [PendingAttachment] = []
    /// Why the last pick was refused, shown above the chips until the
    /// next pick, removal or send.
    private(set) var notice: String?

    typealias Upload = (_ name: String, _ data: Data, _ progress: @escaping @Sendable (Double) -> Void)
        async throws -> StagedAttachment
    /// Where files go; ChatModel points it at the hub's attachments route.
    @ObservationIgnored var upload: Upload = { _, _, _ in throw CancellationError() }
    /// Turns a photo into what's uploaded; tests swap in a plain copy.
    @ObservationIgnored var convert: @Sendable (Data) -> Data? = { PhotoPrep.jpeg(from: $0) }
    @ObservationIgnored private var tasks: [UUID: Task<Void, Never>] = [:]
    /// The bytes of a failed upload, kept for Retry.
    @ObservationIgnored private var payloads: [UUID: Data] = [:]
    @ObservationIgnored private var photoCount = 0

    var isEmpty: Bool { items.isEmpty }
    var uploading: Bool { items.contains(where: \.inFlight) }
    var hasFailed: Bool { items.contains { if case .failed = $0.state { true } else { false } } }
    /// Send has to wait: something is still on its way or needs a retry.
    var blocksSend: Bool { uploading || hasFailed }
    var refs: [String] { items.compactMap(\.ref) }
    /// How many more the phone takes for this message.
    var room: Int { max(0, AttachmentRules.maxPerMessage - items.count) }

    /// What a pick's loader came back with.
    enum Picked: Sendable {
        case data(Data)
        /// Not read, with why, worded for the notice.
        case refused(String)
    }

    /// A file's bytes already in memory. HEIC and other images models
    /// can't read are converted to JPEG first.
    @discardableResult
    func addFile(name: String, data: Data) -> Task<Void, Never>? {
        add(name: name) { .data(data) }
    }

    /// A file from the Files picker. Its chip shows "Preparing" while it's
    /// read off the main thread, so Send waits for it and a send or clear
    /// in the meantime drops it. One over the cap is refused from its size
    /// alone, without reading it; an image to convert may be bigger, since
    /// the JPEG comes out smaller.
    @discardableResult
    func addFile(at url: URL) -> Task<Void, Never>? {
        let name = url.lastPathComponent
        return add(name: name) {
            let read = await Task.detached { Self.read(url, name: name) }.value
            if let data = read.data { return .data(data) }
            return .refused(read.size.flatMap { size in
                // A TIFF's own type isn't the hub's, but it would be
                // converted: only its size can be the problem.
                guard AttachmentRules.needsConversion(name) else { return AttachmentRules.refusal(name: name, size: size) }
                return size > Self.convertibleReadLimit ? AttachmentRules.tooBig(name: name, size: size) : nil
            } ?? "\(name) couldn't be read.")
        }
    }

    /// Images to convert are read up to this size.
    nonisolated static let convertibleReadLimit = 200 << 20

    nonisolated static func read(_ url: URL, name: String) -> (data: Data?, size: Int?) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize
        let limit = AttachmentRules.needsConversion(name) ? convertibleReadLimit : AttachmentRules.maxBytes
        if let size, size > limit { return (nil, size) }
        return (try? Data(contentsOf: url), size)
    }

    /// A photo from the library. `load` fetches its original bytes; it
    /// always goes up as a JPEG.
    @discardableResult
    func addPhoto(load: @escaping @Sendable () async throws -> Data?) -> Task<Void, Never>? {
        guard room > 0 else {
            notice = Self.fullNote
            return nil
        }
        photoCount += 1
        let name = "photo-\(photoCount).jpg"
        return add(name: name, photo: true) {
            guard let data = try? await load() else { return .refused("\(name) couldn't be read as an image.") }
            return .data(data)
        }
    }

    /// Every pick goes through here: a "Preparing" chip at once (so it
    /// counts against the limit and holds Send), then load, convert if
    /// needed, check the hub's caps, and upload under the same chip. A
    /// chip removed or cleared meanwhile drops whatever comes back.
    private func add(name picked: String, photo: Bool = false,
                     load: @escaping @Sendable () async -> Picked) -> Task<Void, Never>? {
        notice = nil
        guard room > 0 else {
            notice = Self.fullNote
            return nil
        }
        let converts = photo || AttachmentRules.needsConversion(picked)
        let name = converts ? AttachmentRules.jpegName(picked) : picked
        // A type the hub won't take is refused from its name, unread.
        if !converts, let why = AttachmentRules.refusal(name: name, size: 1) {
            notice = why
            return nil
        }
        let id = UUID()
        let isImage = converts || AttachmentRules.readableImages.contains(AttachmentRules.ext(name))
        items.append(PendingAttachment(id: id, name: name, size: 0, isImage: isImage, state: .preparing))
        let convert = self.convert
        let task = Task { [weak self] in
            // Images load and convert one at a time: ten 200 MB TIFFs read
            // side by side would run the phone out of memory.
            if converts { await self?.prepTurn() }
            defer { if converts { self?.prepDone() } }
            guard !Task.isCancelled else { return }
            var loaded = await load()
            if converts, case .data(let raw) = loaded {
                guard !Task.isCancelled else { return }
                let jpeg = await Task.detached { convert(raw) }.value
                loaded = jpeg.map(Picked.data) ?? .refused("\(name) couldn't be read as an image.")
            }
            guard let self, !Task.isCancelled, self.items.contains(where: { $0.id == id }) else { return }
            switch loaded {
            case .refused(let why):
                self.drop(id, why)
            case .data(let data):
                if let why = AttachmentRules.refusal(name: name, size: data.count) { return self.drop(id, why) }
                self.update(id) { $0.size = data.count; $0.state = .uploading(0) }
                await self.run(id, data: data)
            }
        }
        tasks[id] = task
        return task
    }

    @ObservationIgnored private var prepBusy = false
    @ObservationIgnored private var prepWaiting: [CheckedContinuation<Void, Never>] = []

    /// Waits until no other image is loading or converting.
    private func prepTurn() async {
        guard prepBusy else {
            prepBusy = true
            return
        }
        await withCheckedContinuation { prepWaiting.append($0) }
    }

    private func prepDone() {
        if prepWaiting.isEmpty {
            prepBusy = false
        } else {
            prepWaiting.removeFirst().resume()
        }
    }

    /// A pick that turned out unusable: its chip goes, with why.
    private func drop(_ id: UUID, _ why: String) {
        items.removeAll { $0.id == id }
        tasks[id] = nil
        notice = why
    }

    private func start(_ id: UUID, data: Data) -> Task<Void, Never> {
        let task = Task { [weak self] in await self?.run(id, data: data); return }
        tasks[id] = task
        return task
    }

    private func run(_ id: UUID, data: Data) async {
        guard let name = items.first(where: { $0.id == id })?.name else { return }
        let progress: @Sendable (Double) -> Void = { [weak self] fraction in
            Task { @MainActor in self?.progressed(id, fraction) }
        }
        do {
            let staged = try await upload(name, data, progress)
            guard !Task.isCancelled else { return }
            payloads[id] = nil
            update(id) { $0.state = .ready(ref: staged.ref) }
        } catch {
            guard !Task.isCancelled else { return }
            payloads[id] = data
            update(id) { $0.state = .failed(Self.failure(error)) }
        }
        tasks[id] = nil
    }

    func progressed(_ id: UUID, _ fraction: Double) {
        guard let i = items.firstIndex(where: { $0.id == id }), case .uploading(let was) = items[i].state,
              fraction > was else { return }
        items[i].state = .uploading(min(1, fraction))
    }

    private func update(_ id: UUID, _ change: (inout PendingAttachment) -> Void) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        change(&items[i])
    }

    /// Tap on a failed chip: upload the same bytes again.
    @discardableResult
    func retry(_ id: UUID) -> Task<Void, Never>? {
        guard let i = items.firstIndex(where: { $0.id == id }), case .failed = items[i].state,
              let data = payloads.removeValue(forKey: id) else { return nil }
        notice = nil
        items[i].state = .uploading(0)
        return start(id, data: data)
    }

    /// The x on a chip: drops it, stopping its upload. A file already on
    /// the hub stays staged there until the hub sweeps it (a week, or
    /// when the session ends), since nothing will reference it.
    func remove(_ id: UUID) {
        tasks.removeValue(forKey: id)?.cancel()
        payloads[id] = nil
        notice = nil
        items.removeAll { $0.id == id }
    }

    /// The send took the finished files (or a detach dropped them on the
    /// hub): the composer starts empty.
    func clear() {
        guard !items.isEmpty || notice != nil || !tasks.isEmpty else { return }
        for task in tasks.values { task.cancel() }
        tasks = [:]
        payloads = [:]
        items = []
        notice = nil
    }

    nonisolated static var fullNote: String { "Up to \(AttachmentRules.maxPerMessage) files per message." }

    /// A failed upload's chip text.
    nonisolated static func failure(_ error: Error) -> String {
        guard let e = error as? HubError else { return "Upload failed" }
        switch e.code {
        case "network": return "Can't reach the hub"
        case "too_large": return "Over the hub's 20 MB limit"
        default: return e.message
        }
    }
}
