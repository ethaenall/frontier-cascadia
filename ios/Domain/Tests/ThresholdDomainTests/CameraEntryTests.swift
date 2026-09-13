import XCTest
import CoreGraphics
@testable import ThresholdDomain

final class CameraEntryTests: XCTestCase {
    private let inside = CGRect(x: 0.4, y: 0.2, width: 0.2, height: 0.6)
    private let outside = CGRect(x: 0.01, y: 0.2, width: 0.12, height: 0.6)

    private func cleared() -> CameraEntryRule {
        var rule = CameraEntryRule()
        rule.begin()
        for time in [10.0, 10.2, 10.4] { _ = rule.observe(boxes: [], capturedAt: time, now: time) }
        return rule
    }

    func testRequiresThreeFreshEmptyFramesAndDwellBeforeArm() {
        var rule = CameraEntryRule()
        rule.begin()
        XCTAssertFalse(rule.arm(at: 10))
        _ = rule.observe(boxes: [], capturedAt: 10, now: 10)
        _ = rule.observe(boxes: [], capturedAt: 10.1, now: 10.1)
        _ = rule.observe(boxes: [], capturedAt: 10.2, now: 10.2)
        XCTAssertFalse(rule.canArm(at: 10.2), "Count alone is not a stable clear baseline")
        _ = rule.observe(boxes: [], capturedAt: 10.4, now: 10.4)
        XCTAssertEqual(rule.phase, .ready)
        XCTAssertTrue(rule.arm(at: 10.4))
        XCTAssertEqual(rule.phase, .armed)
    }

    func testThreeSustainedCentreInsideFramesLatchExactlyOnce() {
        var rule = cleared()
        XCTAssertTrue(rule.arm(at: 10.4))
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 10.6, now: 10.6))
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 10.8, now: 10.8))
        XCTAssertTrue(rule.observe(boxes: [inside], capturedAt: 11.0, now: 11.0))
        XCTAssertEqual(rule.phase, .alert)
        for n in 1...20 {
            let time = 11.0 + Double(n) * 0.2
            XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: time, now: time))
        }
        XCTAssertFalse(rule.canArm(at: 15))
    }

    func testEntryFrameCountAloneCannotSkipDwell() {
        var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
        for time in [10.5, 10.51, 10.52] { XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: time, now: time)) }
        XCTAssertEqual(rule.phase, .armed)
        XCTAssertTrue(rule.observe(boxes: [inside], capturedAt: 10.91, now: 10.91))
    }

    func testPersonAlreadyVisiblePreventsBaselineEvenOutsideZone() {
        var rule = CameraEntryRule(); rule.begin()
        for time in [10.0, 10.2, 10.4, 10.6] { _ = rule.observe(boxes: [outside], capturedAt: time, now: time) }
        XCTAssertEqual(rule.phase, .clearing)
        XCTAssertFalse(rule.arm(at: 10.6))
        for time in [10.8, 11.0, 11.2] { _ = rule.observe(boxes: [], capturedAt: time, now: time) }
        XCTAssertTrue(rule.canArm(at: 11.2))
    }

    func testVisiblePersonInvalidatesReadyBeforeArm() {
        var rule = cleared()
        _ = rule.observe(boxes: [outside], capturedAt: 10.6, now: 10.6)
        XCTAssertEqual(rule.phase, .clearing)
        XCTAssertFalse(rule.arm(at: 10.6))
    }

    func testOutsideCentreAndSingleFrameFlickerDoNotTrigger() {
        var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
        // This box overlaps the region, but its centre remains outside.
        let edge = CGRect(x: 0, y: 0.2, width: 0.3, height: 0.6)
        for time in [10.6, 10.8, 11.0] { XCTAssertFalse(rule.observe(boxes: [edge], capturedAt: time, now: time)) }
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 11.2, now: 11.2))
        XCTAssertFalse(rule.observe(boxes: [], capturedAt: 11.4, now: 11.4))
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 11.6, now: 11.6))
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 11.8, now: 11.8))
        XCTAssertTrue(rule.observe(boxes: [inside], capturedAt: 12.0, now: 12.0))
    }

    func testMultiplePeopleDisarmAndRequireNewClearBaseline() {
        var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
        XCTAssertFalse(rule.observe(boxes: [inside, outside], capturedAt: 10.6, now: 10.6))
        XCTAssertEqual(rule.phase, .clearing)
        XCTAssertTrue(rule.multiplePeople)
        for time in [10.8, 11.0, 11.2] { XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: time, now: time)) }
        XCTAssertFalse(rule.canArm(at: 11.2))
    }

    func testAcknowledgeAndDisarmNeedNewEmptyFramesNeverRearm() {
        var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
        for time in [10.6, 10.8, 11.0] { _ = rule.observe(boxes: [inside], capturedAt: time, now: time) }
        rule.disarm()
        XCTAssertEqual(rule.phase, .clearing)
        XCTAssertFalse(rule.canArm(at: 11))
        for time in [11.2, 11.4, 11.6] { XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: time, now: time)) }
        for time in [11.8, 12.0, 12.2] { _ = rule.observe(boxes: [], capturedAt: time, now: time) }
        XCTAssertEqual(rule.phase, .ready)
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 12.4, now: 12.4), "Ready is not armed")
    }

    func testStaleReadyCannotArmAndStaleArmedFailsClosed() {
        var ready = cleared()
        XCTAssertFalse(ready.canArm(at: 12))
        XCTAssertFalse(ready.arm(at: 12))
        XCTAssertEqual(ready.phase, .unavailable)
        var armed = cleared(); XCTAssertTrue(armed.arm(at: 10.4))
        XCTAssertTrue(armed.expire(at: 12))
        XCTAssertEqual(armed.phase, .unavailable)
        XCTAssertFalse(armed.observe(boxes: [inside], capturedAt: 12.2, now: 12.2), "Fresh frames cannot auto-restart")
    }

    func testLateFutureNonfiniteDuplicateAndOutOfOrderFramesFailClosed() {
        let samples: [(Double, Double)] = [(10.6, 12), (11, 10.8), (.nan, 10.6), (10.4, 10.4), (10.2, 10.6)]
        for (captured, now) in samples {
            var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
            XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: captured, now: now))
            XCTAssertEqual(rule.phase, .unavailable)
            XCTAssertFalse(rule.canArm(at: now))
        }
    }

    func testLongFrameGapFailsClosedEvenIfNewFrameIsFresh() {
        var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
        XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: 12, now: 12))
        XCTAssertEqual(rule.phase, .unavailable)
    }

    func testMalformedBoxNeverBecomesEmptyBaseline() {
        let invalid = [CGRect(x: -0.1, y: 0.2, width: 0.2, height: 0.6),
                       CGRect(x: 0.4, y: 0.2, width: 0, height: 0.6),
                       CGRect(x: Double.nan, y: 0.2, width: 0.2, height: 0.6)]
        for box in invalid {
            var rule = CameraEntryRule(); rule.begin()
            XCTAssertFalse(rule.observe(boxes: [box], capturedAt: 10, now: 10))
            XCTAssertEqual(rule.phase, .unavailable)
            XCTAssertFalse(rule.arm(at: 10))
        }
    }

    func testStopAndFaultIgnoreFurtherFramesUntilExplicitBegin() {
        for shouldFault in [true, false] {
            var rule = cleared(); XCTAssertTrue(rule.arm(at: 10.4))
            if shouldFault { rule.fail(.captureUnavailable) } else { rule.stop() }
            for time in [10.6, 10.8, 11.0] { XCTAssertFalse(rule.observe(boxes: [inside], capturedAt: time, now: time)) }
            XCTAssertEqual(rule.phase, shouldFault ? .unavailable : .stopped)
            rule.begin()
            XCTAssertEqual(rule.phase, .clearing)
            XCTAssertFalse(rule.canArm(at: 11))
        }
    }

    func testVisionBottomLeftConversionPreservesPortraitAxes() {
        let converted = CameraEntryRule.displayBox(fromVision: CGRect(x: 0.1, y: 0.2, width: 0.3, height: 0.5))
        XCTAssertEqual(converted?.minX ?? -1, 0.1, accuracy: 0.000001)
        XCTAssertEqual(converted?.minY ?? -1, 0.3, accuracy: 0.000001)
        XCTAssertEqual(converted?.width ?? -1, 0.3, accuracy: 0.000001)
        XCTAssertEqual(converted?.height ?? -1, 0.5, accuracy: 0.000001)
        XCTAssertNil(CameraEntryRule.displayBox(fromVision: .null))
    }

    func testEventSourceIsExplicitAndNeverClaimsCameraForRehearsal() {
        let event = CameraEntryEvent(id: "example", occurredAt: Date(timeIntervalSince1970: 1), source: .rehearsal)
        XCTAssertEqual(event.source, .rehearsal)
        XCTAssertNotEqual(event.source, .camera)
        XCTAssertEqual(event.id, "example")
    }
}
