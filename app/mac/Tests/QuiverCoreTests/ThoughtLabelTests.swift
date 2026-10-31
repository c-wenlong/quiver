import Foundation
import Testing
@testable import QuiverCore

/// The thinking card's title (#1440). The same cases as the iPhone's
/// `ThoughtLabelTests` and the web app's `Fmt.thought` test.
struct ThoughtLabelTests {
    @Test func readsTheDurationTheHubSent() {
        #expect(ThoughtLabel.text(12) == "Thought for 12s")
        #expect(ThoughtLabel.text(12.4) == "Thought for 12s")
        #expect(ThoughtLabel.text(59.6) == "Thought for 1m")
        #expect(ThoughtLabel.text(65) == "Thought for 1m 5s")
        #expect(ThoughtLabel.text(3600) == "Thought for 1h")
        #expect(ThoughtLabel.text(3725) == "Thought for 1h 2m")
    }

    @Test func neverReadsZeroSeconds() {
        #expect(ThoughtLabel.text(0) == "Thought for 1s")
        #expect(ThoughtLabel.text(0.3) == "Thought for 1s")
    }

    @Test func plainWhenUnknown() {
        #expect(ThoughtLabel.text(nil) == "Thought")
        #expect(ThoughtLabel.text(-1) == "Thought")
        #expect(ThoughtLabel.text(.nan) == "Thought")
        #expect(ThoughtLabel.text(.infinity) == "Thought")
    }

    @Test func decodesDurationFromTheWire() throws {
        let raw = Data(#"{"kind":"thinking","text":"hmm","ts":1,"duration_s":12.5}"#.utf8)
        let msg = try HubWire.decoder.decode(HubMessage.self, from: raw)
        #expect(msg.durationS == 12.5)
        let old = try HubWire.decoder.decode(HubMessage.self, from: Data(#"{"kind":"thinking","text":"hmm"}"#.utf8))
        #expect(old.durationS == nil)
    }
}
