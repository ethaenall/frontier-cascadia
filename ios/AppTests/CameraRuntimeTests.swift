import XCTest
import AVFoundation
@testable import Threshold
import ThresholdDomain

@MainActor private final class CameraTestClock {
    var time: TimeInterval = 10
    var date: Date { Date(timeIntervalSince1970: 1_700_000_000 + time) }
    func advance(_ delta: TimeInterval = 0.2) { time += delta }
}

/// No capture inputs, permission API, Vision execution, hardware, or network in these tests.
@MainActor private final class ControlledCameraDriver: CameraCaptureDriving {
    struct Request {
        let generation: UUID
        let frame: CameraFrameHandler
        let failure: CameraFailureHandler
    }
    let session = AVCaptureSession()
    var authorization: AVAuthorizationStatus = .authorized
    var rearCameraAvailable = true
    var permissionCalls = 0
    var stopCalls = 0
    var requests: [Request] = []
    var holdStarts = false
    var startResult: CameraCaptureStart = .running
    var permissionReply: CheckedContinuation<Bool, Never>?
    var startReplies: [Int: CheckedContinuation<CameraCaptureStart, Never>] = [:]
    var onPermission: (() -> Void)?
    var onStart: (() -> Void)?
    func hasRearCamera() -> Bool { rearCameraAvailable }
    func authorizationStatus() -> AVAuthorizationStatus { authorization }
    func requestPermission() async -> Bool {
        permissionCalls += 1
        return await withCheckedContinuation { permissionReply = $0; onPermission?() }
    }
    func start(generation: UUID, frame: @escaping CameraFrameHandler,
               failure: @escaping CameraFailureHandler) async -> CameraCaptureStart {
        let index = requests.count
        requests.append(Request(generation: generation, frame: frame, failure: failure))
        guard holdStarts else { onStart?(); return startResult }
        return await withCheckedContinuation { startReplies[index] = $0; onStart?() }
    }
    func stop() { stopCalls += 1 }
    func resolvePermission(_ allowed: Bool) {
        let reply = permissionReply; permissionReply = nil; reply?.resume(returning: allowed)
    }
    func resolveStart(_ index: Int, result: CameraCaptureStart = .running) {
        startReplies.removeValue(forKey: index)?.resume(returning: result)
    }
    func emit(_ boxes: [CGRect], at time: TimeInterval, request index: Int? = nil) {
        let request = requests[index ?? (requests.count - 1)]
        request.frame(request.generation, CameraPersonFrame(boxes: boxes, capturedAt: time))
    }
    func interrupt(request index: Int? = nil) {
        let request = requests[index ?? (requests.count - 1)]
        request.failure(request.generation, "TEST interruption — no hardware")
    }
}

@MainActor final class CameraRuntimeTests: XCTestCase {
    private let inside = CGRect(x: 0.4, y: 0.2, width: 0.2, height: 0.6)
    private func monitor(_ driver: ControlledCameraDriver, _ clock: CameraTestClock) -> CameraEntryMonitor {
        CameraEntryMonitor(driver: driver, now: { clock.time }, wallClock: { clock.date }, automaticallyTick: false)
    }
    private func cameraFrames(_ count: Int, boxes: [CGRect], driver: ControlledCameraDriver, clock: CameraTestClock) {
        for _ in 0..<count { clock.advance(); driver.emit(boxes, at: clock.time) }
    }
    private func rehearsalFrames(_ count: Int, monitor: CameraEntryMonitor, clock: CameraTestClock) {
        for _ in 0..<count { clock.advance(); monitor.evaluateClock() }
    }

    func testMissingHardwareIsCheckedBeforePermission() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        driver.rearCameraAvailable = false; driver.authorization = .notDetermined
        await actual.startCamera()
        XCTAssertEqual(driver.permissionCalls, 0); XCTAssertTrue(driver.requests.isEmpty)
        XCTAssertEqual(actual.phase, .unavailable); XCTAssertEqual(actual.inputMode, .camera)
        XCTAssertFalse(actual.isRunning); XCTAssertTrue(actual.events.isEmpty)
    }

    func testExplicitResetRequiresDisarmedStateAndPreservesJournal() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        actual.startRehearsal(); rehearsalFrames(3, monitor: actual, clock: clock)
        XCTAssertTrue(actual.canArm)
        actual.startRehearsal()
        XCTAssertEqual(actual.phase, .clearing); XCTAssertFalse(actual.canArm)
        rehearsalFrames(3, monitor: actual, clock: clock); actual.arm()
        actual.startRehearsal(); await actual.startCamera()
        XCTAssertEqual(actual.phase, .armed); XCTAssertEqual(actual.inputMode, .rehearsal)
        XCTAssertTrue(driver.requests.isEmpty)
        actual.rehearseEntry(); rehearsalFrames(3, monitor: actual, clock: clock)
        XCTAssertEqual(actual.phase, .alert)
        actual.startRehearsal(); await actual.startCamera()
        XCTAssertEqual(actual.phase, .alert); XCTAssertEqual(actual.inputMode, .rehearsal)
        let first = actual.events.first
        actual.acknowledge(); actual.startRehearsal()
        XCTAssertEqual(actual.phase, .clearing); XCTAssertEqual(actual.events.first, first)
        actual.stop()
    }

    func testRehearsalUsesNoPermissionAndWaitsForSameClearRule() {
        let driver = ControlledCameraDriver(), clock = CameraTestClock()
        driver.authorization = .denied
        let actual = monitor(driver, clock)
        actual.startRehearsal()
        XCTAssertEqual(actual.inputMode, .rehearsal); XCTAssertEqual(actual.phase, .clearing)
        actual.arm(); actual.rehearseEntry()
        XCTAssertFalse(actual.canArm); XCTAssertTrue(actual.events.isEmpty)
        rehearsalFrames(2, monitor: actual, clock: clock)
        XCTAssertFalse(actual.canArm)
        rehearsalFrames(1, monitor: actual, clock: clock)
        XCTAssertTrue(actual.canArm); XCTAssertEqual(actual.phase, .ready)
        XCTAssertEqual(driver.permissionCalls, 0); XCTAssertTrue(driver.requests.isEmpty)
        XCTAssertTrue(driver.session.inputs.isEmpty)
        actual.stop()
    }

    func testRehearsalLatchesOnceAndAcknowledgeNeverRearms() {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        actual.startRehearsal(); rehearsalFrames(3, monitor: actual, clock: clock)
        actual.arm(); actual.rehearseEntry()
        rehearsalFrames(2, monitor: actual, clock: clock)
        XCTAssertTrue(actual.events.isEmpty)
        rehearsalFrames(1, monitor: actual, clock: clock)
        XCTAssertEqual(actual.phase, .alert); XCTAssertEqual(actual.events.count, 1)
        XCTAssertEqual(actual.events.first?.source, .rehearsal)
        let first = actual.events.first
        rehearsalFrames(12, monitor: actual, clock: clock); actual.rehearseEntry()
        XCTAssertEqual(actual.events.count, 1)
        actual.acknowledge()
        XCTAssertEqual(actual.phase, .clearing); XCTAssertFalse(actual.canArm)
        XCTAssertEqual(actual.events.first, first)
        rehearsalFrames(3, monitor: actual, clock: clock)
        XCTAssertEqual(actual.phase, .ready)
        actual.rehearseEntry(); rehearsalFrames(4, monitor: actual, clock: clock)
        XCTAssertEqual(actual.events.count, 1, "An Entry tap without a new Arm does not fire")
        actual.stop(); actual.startRehearsal()
        XCTAssertEqual(actual.events.first, first)
        actual.stop()
    }

    func testJournalBoundAndPerEventProvenanceSurviveModeSwitch() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        actual.startRehearsal()
        for _ in 0..<(CameraEntryMonitor.journalLimit + 3) {
            rehearsalFrames(3, monitor: actual, clock: clock)
            actual.arm(); actual.rehearseEntry(); rehearsalFrames(3, monitor: actual, clock: clock)
            XCTAssertEqual(actual.phase, .alert)
            actual.acknowledge()
        }
        XCTAssertEqual(actual.events.count, CameraEntryMonitor.journalLimit)
        XCTAssertTrue(actual.events.allSatisfy { $0.source == .rehearsal })
        XCTAssertEqual(Set(actual.events.map(\.id)).count, CameraEntryMonitor.journalLimit)
        actual.stop(); await actual.startCamera()
        cameraFrames(3, boxes: [], driver: driver, clock: clock); actual.arm()
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        XCTAssertEqual(actual.events.count, CameraEntryMonitor.journalLimit)
        XCTAssertEqual(actual.events.first?.source, .camera, "Only the injected camera boundary supplied these test frames")
        XCTAssertTrue(actual.events.dropFirst().allSatisfy { $0.source == .rehearsal })
        actual.stop()
    }

    func testPendingPermissionCannotStartAfterStop() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        driver.authorization = .notDetermined
        let held = expectation(description: "permission held"); driver.onPermission = { held.fulfill() }
        let start = Task { await actual.startCamera() }
        await fulfillment(of: [held], timeout: 2)
        actual.stop(); driver.resolvePermission(true); await start.value
        XCTAssertTrue(driver.requests.isEmpty)
        XCTAssertEqual(actual.phase, .stopped); XCTAssertFalse(actual.isRunning); XCTAssertFalse(actual.canArm)
    }

    func testInactivePermissionReplyAndReturnToForegroundNeverAutoStart() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        driver.authorization = .notDetermined
        let held = expectation(description: "permission held"); driver.onPermission = { held.fulfill() }
        let start = Task { await actual.startCamera() }
        await fulfillment(of: [held], timeout: 2)
        actual.lifecycle(active: false); driver.resolvePermission(true); await start.value
        actual.lifecycle(active: true)
        XCTAssertTrue(driver.requests.isEmpty); XCTAssertEqual(actual.phase, .stopped)
        driver.authorization = .authorized
        await actual.startCamera()
        XCTAssertEqual(driver.requests.count, 1); XCTAssertEqual(actual.phase, .clearing)
        actual.stop()
    }

    func testDeniedRestrictedAndMissingCameraFailClosedWithoutFallback() async {
        for authorization in [AVAuthorizationStatus.denied, .restricted] {
            let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
            driver.authorization = authorization
            await actual.startCamera()
            XCTAssertEqual(actual.phase, .unavailable); XCTAssertFalse(actual.canArm)
            XCTAssertEqual(actual.inputMode, .camera); XCTAssertFalse(actual.isRunning)
            XCTAssertTrue(driver.requests.isEmpty); XCTAssertEqual(driver.permissionCalls, 0)
        }
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        driver.startResult = .unavailable("TEST no rear camera")
        await actual.startCamera()
        XCTAssertEqual(actual.phase, .unavailable); XCTAssertEqual(actual.inputMode, .camera)
        XCTAssertFalse(actual.isRunning); XCTAssertFalse(actual.canArm); XCTAssertTrue(actual.events.isEmpty)
    }

    func testLateStartCompletionCannotReviveStoppedMonitor() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        driver.holdStarts = true
        let held = expectation(description: "start held"); driver.onStart = { held.fulfill() }
        let start = Task { await actual.startCamera() }
        await fulfillment(of: [held], timeout: 2)
        let stopsBefore = driver.stopCalls
        actual.stop(); driver.resolveStart(0); await start.value
        XCTAssertGreaterThan(driver.stopCalls, stopsBefore)
        XCTAssertEqual(actual.phase, .stopped); XCTAssertFalse(actual.isRunning); XCTAssertFalse(actual.canArm)
    }

    func testLateStartCannotReplaceExplicitRehearsal() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        driver.holdStarts = true
        let held = expectation(description: "start held"); driver.onStart = { held.fulfill() }
        let start = Task { await actual.startCamera() }
        await fulfillment(of: [held], timeout: 2)
        actual.startRehearsal(); driver.resolveStart(0); await start.value
        XCTAssertEqual(actual.inputMode, .rehearsal); XCTAssertEqual(actual.phase, .clearing)
        driver.interrupt(request: 0)
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        XCTAssertEqual(actual.inputMode, .rehearsal); XCTAssertTrue(actual.events.isEmpty)
        XCTAssertFalse(actual.personVisible)
        actual.stop()
    }

    func testOldGenerationFramesAndFailureCannotAffectNewCameraRun() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); actual.stop(); await actual.startCamera()
        XCTAssertEqual(driver.requests.count, 2)
        for _ in 0..<3 { clock.advance(); driver.emit([], at: clock.time, request: 0) }
        driver.interrupt(request: 0)
        XCTAssertEqual(actual.phase, .clearing); XCTAssertFalse(actual.canArm)
        cameraFrames(3, boxes: [], driver: driver, clock: clock)
        XCTAssertTrue(actual.canArm)
        actual.arm()
        for _ in 0..<3 { clock.advance(); driver.emit([inside], at: clock.time, request: 0) }
        XCTAssertTrue(actual.events.isEmpty); XCTAssertFalse(actual.personVisible)
        actual.stop()
    }

    func testInterruptionStopsDisarmsAndPreservesLatchedEvent() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); cameraFrames(3, boxes: [], driver: driver, clock: clock); actual.arm()
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        let event = actual.events.first; XCTAssertNotNil(event)
        driver.interrupt()
        XCTAssertEqual(actual.phase, .unavailable); XCTAssertFalse(actual.isRunning); XCTAssertFalse(actual.canArm)
        XCTAssertEqual(actual.events.first, event); XCTAssertTrue(actual.personBoxes.isEmpty)
        actual.lifecycle(active: true)
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        XCTAssertEqual(driver.requests.count, 1); XCTAssertEqual(actual.phase, .unavailable)
        XCTAssertEqual(actual.events.count, 1)
    }

    func testStaleWatchdogStopsArmedCaptureWithoutNewFrames() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); cameraFrames(3, boxes: [], driver: driver, clock: clock); actual.arm()
        clock.advance(1.3)
        XCTAssertFalse(actual.canArm)
        actual.evaluateClock()
        XCTAssertEqual(actual.phase, .unavailable); XCTAssertFalse(actual.isRunning)
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        XCTAssertTrue(actual.events.isEmpty)
    }

    func testLateCapturedFrameDoesNotLookFreshJustBecauseItArrivedNow() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); cameraFrames(3, boxes: [], driver: driver, clock: clock); actual.arm()
        let captured = clock.time + 0.1; clock.advance(2)
        driver.emit([inside], at: captured)
        XCTAssertEqual(actual.phase, .unavailable); XCTAssertFalse(actual.isRunning); XCTAssertTrue(actual.events.isEmpty)
    }

    func testZeroFirstFramesAndPendingStartBothTimeOut() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); clock.advance(3.1); actual.evaluateClock()
        XCTAssertEqual(actual.phase, .unavailable); XCTAssertFalse(actual.isRunning)
        driver.holdStarts = true
        let held = expectation(description: "start held"); driver.onStart = { held.fulfill() }
        let restart = Task { await actual.startCamera() }
        await fulfillment(of: [held], timeout: 2)
        clock.advance(8.1); actual.evaluateClock()
        XCTAssertEqual(actual.phase, .unavailable)
        driver.resolveStart(1); await restart.value
        XCTAssertFalse(actual.isRunning); XCTAssertFalse(actual.canArm)
    }

    func testFrameCapturedBeforeArmCannotCountTowardEntry() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); cameraFrames(3, boxes: [], driver: driver, clock: clock)
        let beforeArm = clock.time + 0.05; clock.advance(0.1); actual.arm()
        driver.emit([inside], at: beforeArm)
        XCTAssertEqual(actual.phase, .armed); XCTAssertFalse(actual.personVisible)
        cameraFrames(2, boxes: [inside], driver: driver, clock: clock)
        XCTAssertTrue(actual.events.isEmpty)
        cameraFrames(1, boxes: [inside], driver: driver, clock: clock)
        XCTAssertEqual(actual.events.count, 1)
        actual.stop()
    }

    func testBackgroundWhileArmedStopsAndReturnDoesNotRearm() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); cameraFrames(3, boxes: [], driver: driver, clock: clock); actual.arm()
        actual.lifecycle(active: false)
        XCTAssertEqual(actual.phase, .stopped); XCTAssertFalse(actual.isRunning)
        actual.lifecycle(active: true)
        XCTAssertEqual(actual.statusMessage, "Camera is off and disarmed. Start camera or choose Rehearsal.")
        XCTAssertEqual(actual.inputMode, .camera); XCTAssertEqual(driver.requests.count, 1)
        XCTAssertEqual(actual.phase, .stopped); XCTAssertFalse(actual.isRunning)
        actual.arm()
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        XCTAssertEqual(driver.requests.count, 1); XCTAssertEqual(actual.phase, .stopped)
        XCTAssertTrue(actual.events.isEmpty); XCTAssertFalse(actual.canArm)
    }

    func testMultiplePeopleExplicitlyDisarmOnePersonDemo() async {
        let driver = ControlledCameraDriver(), clock = CameraTestClock(), actual = monitor(driver, clock)
        await actual.startCamera(); cameraFrames(3, boxes: [], driver: driver, clock: clock); actual.arm()
        cameraFrames(1, boxes: [inside, CGRect(x: 0.02, y: 0.1, width: 0.1, height: 0.7)], driver: driver, clock: clock)
        XCTAssertEqual(actual.phase, .clearing); XCTAssertFalse(actual.canArm)
        XCTAssertTrue(actual.statusMessage.contains("One-person")); XCTAssertTrue(actual.statusMessage.contains("Disarmed"))
        cameraFrames(3, boxes: [inside], driver: driver, clock: clock)
        XCTAssertTrue(actual.events.isEmpty)
        actual.stop()
    }
}
