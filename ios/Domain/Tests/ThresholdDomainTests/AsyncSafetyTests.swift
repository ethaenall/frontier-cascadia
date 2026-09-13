import XCTest
@testable import ThresholdDomain
final class AsyncSafetyTests: XCTestCase {
    func testNewAROriginDiscardsEveryRenderAndCachedHit() {
        var frame = WorldFrameResources<String>()
        frame.reset(frameID: "A", discard: { _ in })
        frame.add("corner-A"); frame.add("edge-A"); frame.hit = SIMD3<Float>(1,2,3)
        var removed: [String] = []
        frame.reset(frameID: "B", discard: { removed.append($0) })
        XCTAssertEqual(removed, ["corner-A", "edge-A"])
        XCTAssertTrue(frame.renderObjects.isEmpty)
        XCTAssertNil(frame.hit)
        XCTAssertEqual(frame.frameID, "B")
    }
    func testViewRemovalDiscardsFrameResources() {
        var frame = WorldFrameResources<String>()
        frame.reset(frameID: "A", discard: { _ in }); frame.add("polygon-A"); frame.hit = SIMD3<Float>(1,0,1)
        frame.reset(frameID: nil, discard: { _ in })
        XCTAssertNil(frame.frameID); XCTAssertNil(frame.hit); XCTAssertTrue(frame.renderObjects.isEmpty)
    }
    func testLateOldPublishAckCannotCleanNewDraft() {
        var draft = DraftPublication()
        let a = draft.capture(frameID:"A", payload: Data("polygon-A".utf8))
        draft.edited()
        XCTAssertFalse(draft.accepts(a, frameID:"B", payload:Data("polygon-B".utf8)))
        // A -> B -> A still is a newer edit, not the submitted transaction.
        XCTAssertFalse(draft.accepts(a, frameID:"A", payload:Data("polygon-A".utf8)))
    }
    func testExactUnchangedPublishCanBeAcknowledged() {
        let draft = DraftPublication(); let data = Data("polygon-A".utf8)
        let ticket = draft.capture(frameID:"A",payload:data)
        XCTAssertTrue(draft.accepts(ticket,frameID:"A",payload:data))
    }
    func testReversedGETCompletionCannotReplaceAlarmOrStopSound() {
        var order = RefreshOrdering()
        let old = order.issue() // captured ARMED, delayed
        let newest = order.issue() // captured ALARM, returned first
        var visible = "UNKNOWN", sounding = false
        if order.accepts(newest) { visible = "ALARM"; sounding = true }
        if order.accepts(old) { visible = "ARMED"; sounding = false }
        XCTAssertEqual(visible,"ALARM"); XCTAssertTrue(sounding)
        XCTAssertFalse(order.accepts(old), "A late old failure must also be rejected")
    }
    func testUncertainAcceptedArmKeepsSafeDisarmReachable() {
        // Backend accepted ARM, response was lost. Only cached READY is known.
        XCTAssertTrue(ControlPresentation.offersSafeDisarm(paired:true,cachedAlarm:.ready))
        XCTAssertTrue(ControlPresentation.offersSafeDisarm(paired:true,cachedAlarm:.disarmed))
        XCTAssertFalse(ControlPresentation.offersSafeDisarm(paired:false,cachedAlarm:nil))
    }
}
