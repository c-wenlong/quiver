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

    private let credentials: ServiceCredentials
    private let transport: any HTTPTransport

    public init(credentials: ServiceCredentials, transport: any HTTPTransport) {
        self.credentials = credentials
        self.transport = transport
    }

    /// Reads every known field's status; the section calls this on appear
    /// and after each change.
    public func reload() async {
        var fresh: [String: ServiceCredentials.Status] = [:]
        for service in ServiceCredentials.services {
            for field in service.fields {
                fresh[field] = await credentials.status(for: field)
            }
        }
        statuses = fresh
    }

    public func set(_ field: String, value: String) async {
        do {
            try await credentials.set(field, value: value)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        await refresh(field)
    }

    public func remove(_ field: String) async {
        do {
            try await credentials.remove(field)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
        await refresh(field)
    }

    /// The service's probe request; keeps only the outcome.
    public func probe(_ service: KnownService) async {
        probing.insert(service.id)
        defer { probing.remove(service.id) }
        probeResults[service.id] = await credentials.probe(service, transport: transport)
    }

    /// The key the service's probe needs is saved.
    public func canProbe(_ service: KnownService) -> Bool {
        guard let field = service.probe?.field else { return false }
        return statuses[field]?.isSet == true
    }

    private func refresh(_ field: String) async {
        statuses[field] = await credentials.status(for: field)
    }
}
