import Testing
@testable import QuiverApp

/// Board rows word GitHub's numbers and run conclusions as a person would.
struct RowWordingTests {
    @Test func oneCommentIsSingular() {
        #expect(RowWording.comments(1) == "1 comment")
        #expect(RowWording.comments(2) == "2 comments")
    }

    @Test func conclusionsReadInSentenceCase() {
        #expect(RowWording.conclusion("success") == "Passed")
        #expect(RowWording.conclusion("failure") == "Failure")
        #expect(RowWording.conclusion("timed_out") == "Timed out")
        #expect(RowWording.conclusion("startup_failure") == "Startup failure")
    }
}
