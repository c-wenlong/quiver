import Foundation
import Observation

/// The Settings > Services section: the catalog's fields, whether each is
/// saved, and the result of the last probe per service.
@MainActor
@Observable
public final class ServicesModel {
    /// Field name → keychain/metadata state.
    public private(set) var statuses: [String: ServiceCredentials.Status] = [:]
    /// Service id → the probe's outcome, for the line under its button.
    public private(set) var probeResults: [String: ServiceCredentials.ProbeResult] = [:]
    /// Service ids with a probe in flight.
    public private(set) var probing: Set<String> = []
    /// A failed set/remove, shown in the section; never carries a value.
    public private(set) var error: String?
    /// Runs after a field is saved or removed, with the field's name, so
    /// whatever reads that key can fetch again at once.
    public var onFieldChanged: (@MainActor (String) -> Void)?

    private let credentials: ServiceCredentials
    private let transport: any HTTPTransport
    /// A stamp per service, bumped by each probe and by any set/remove
    /// of one of its fields, so an older in-flight probe can't write its
    /// result over a newer key's.
    private var probeGenerations: [String: Int] = [:]
    /// A stamp per field, bumped by set/remove and by each field refresh,
    /// so a slow reload only writes back fields nothing touched since.
    private var statusGenerations: [String: Int] = [:]
    /// Bumped by every reload; an older reload's merge is discarded when
    /// a newer one started.
    private var reloadGeneration = 0

    public init(credentials: ServiceCredentials, transport: any HTTPTransport) {
        self.credentials = credentials
        self.transport = transport
    }

    /// Reads every known field's status; the section calls this on appear
    /// and after each change.
    public func reload() async {
        reloadGeneration += 1
        let mine = reloadGeneration
        let generations = statusGenerations
        var fresh: [String: ServiceCredentials.Status] = [:]
        for service in ServiceCredentials.services {
            for field in service.fields {
                fresh[field] = await credentials.status(for: field)
            }
        }
        // A newer reload, or a save/remove/status refresh of the field
        // while this read was in flight, wins over the collected values.
        guard mine == reloadGeneration else { return }
        for (field, status) in fresh
        where statusGenerations[field, default: 0] == generations[field, default: 0] {
            statuses[field] = status
        }
    }

    public func set(_ field: String, value: String) async {
        statusGenerations[field, default: 0] += 1
        do {
            try await credentials.set(field, value: value)
            error = nil
            clearProbe(forField: field)
        } catch {
            self.error = error.localizedDescription
        }
        await refresh(field)
        onFieldChanged?(field)
    }

    public func remove(_ field: String) async {
        statusGenerations[field, default: 0] += 1
        do {
            try await credentials.remove(field)
            error = nil
            clearProbe(forField: field)
        } catch {
            self.error = error.localizedDescription
        }
        await refresh(field)
        onFieldChanged?(field)
    }

    /// The service's probe request; keeps only the outcome. A set or
    /// remove of the key while the request flies discards the result:
    /// the answer belongs to the old value, not the new one.
    public func probe(_ service: KnownService) async {
        probeGenerations[service.id, default: 0] += 1
        let generation = probeGenerations[service.id]!
        probing.insert(service.id)
        defer { probing.remove(service.id) }
        let result = await credentials.probe(service, transport: transport)
        if probeGenerations[service.id] == generation {
            probeResults[service.id] = result
        }
        // The read recorded the key's use; show the new "last used".
        if let field = service.probe?.field { await refresh(field) }
    }

    /// Drop the owning service's probe result and retire probes in
    /// flight: they were run against a different value.
    private func clearProbe(forField field: String) {
        for service in ServiceCredentials.services where service.fields.contains(field) {
            probeGenerations[service.id, default: 0] += 1
            probeResults[service.id] = nil
        }
    }

    /// The key the service's probe needs is saved.
    public func canProbe(_ service: KnownService) -> Bool {
        guard let field = service.probe?.field else { return false }
        return statuses[field]?.isSet == true
    }

    private func refresh(_ field: String) async {
        statusGenerations[field, default: 0] += 1
        let generation = statusGenerations[field]!
        let status = await credentials.status(for: field)
        guard generation == statusGenerations[field] else { return }
        statuses[field] = status
    }
}
