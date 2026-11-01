import Foundation
import Testing
@testable import SWECore

/// The Sessions page's Devin-key hint and each service's Get-a-key link.
@Suite struct DevinKeyHintTests {
    @Test func noDevinStaysHidden() {
        #expect(!DevinKeyHint.shows(hasDevin: false, keySet: false, dismissed: false))
        #expect(!DevinKeyHint.shows(hasDevin: false, keySet: true, dismissed: false))
    }

    @Test func devinWithAKeyStaysHidden() {
        #expect(!DevinKeyHint.shows(hasDevin: true, keySet: true, dismissed: false))
    }

    @Test func devinWithoutAKeyShows() {
        #expect(DevinKeyHint.shows(hasDevin: true, keySet: false, dismissed: false))
    }

    @Test func unloadedStatusesStayHidden() {
        #expect(!DevinKeyHint.shows(hasDevin: true, keySet: nil, dismissed: false))
    }

    @Test func dismissedStaysHidden() {
        #expect(!DevinKeyHint.shows(hasDevin: true, keySet: false, dismissed: true))
    }

    @Test func everyServiceNamesAnHttpsKeyPage() {
        for service in ServiceCredentials.services {
            #expect(service.keyURL.scheme == "https", "\(service.id)")
        }
    }

    @Test func cloudSessionsCountAsDevin() {
        #expect(DevinKeyHint.isDevin(harness: "devin"))
        #expect(DevinKeyHint.isDevin(harness: DevinCloud.harness))
        #expect(!DevinKeyHint.isDevin(harness: "claude"))
    }
}
