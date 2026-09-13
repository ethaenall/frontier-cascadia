import XCTest
import UIKit

/// Runs without the radio backend. Synthetic observations are selected only by
/// the visible REHEARSAL control; no launch flag impersonates a live camera.
final class CameraDemoUITests: XCTestCase {
    override func setUpWithError() throws { continueAfterFailure = false }

    func testDefaultLaunchIsStoppedAndDisclosesFullRouterRequirements() {
        let app = launch()
        phase("stopped", in: app)
        source("CAMERA DEMO", in: app)
        assertCannotActivate("camera-arm", in: app)
        assertCannotActivate("camera-simulate-entry", in: app)
        XCTAssertEqual(events(in: app).count, 0)
        assertNoPermissionPrompt(in: app)
        control("camera-start", in: app)
        control("camera-rehearsal", in: app)
        assertApertureAboveDock(in: app)
        capture("CAMERA-DEMO-01-stopped-explicit-source-choice")

        let note = app.staticTexts["camera-router-note"]
        reveal(note, in: app)
        let disclosure = note.label.lowercased()
        for required in ["compatible", "router", "sensing", "hardware", "unverified"] {
            XCTAssertTrue(disclosure.contains(required), "Missing router disclosure: \(required)")
        }
        control("camera-open-radio-lab", in: app)
        phase("stopped", in: app)
        capture("CAMERA-DEMO-02-router-and-sensing-hardware-disclosure")
        // The secondary lab is reachable. This suite never pairs or controls it.
    }

    func testExplicitRehearsalLatchesOneProvenancedEventAndCanRepeatAfterAcknowledgement() {
        let app = launch()
        startRehearsal(in: app)
        XCTAssertEqual(events(in: app).count, 0)
        captureRehearsal("01-empty-ready", in: app)
        tap("camera-arm", in: app)
        phase("armed", in: app)
        assertApertureAboveDock(in: app)
        captureRehearsal("02-armed", in: app)
        tap("camera-simulate-entry", in: app)
        phase("alert", in: app)
        assertApertureAboveDock(in: app)
        captureRehearsal("03a-latched-alert-doorway", in: app)
        assertEventCount(1, in: app)
        assertRehearsalJournal(in: app, count: 1)
        capture("REHEARSAL-03-latched-alert-journal-synthetic")
        assertRemainsLatched(eventCount: 1, in: app)
        captureRehearsal("04-latched-alert", in: app)

        tap("camera-acknowledge", in: app)
        phase(in: ["clearing", "ready"], app: app)
        assertDoesNotAutoArm(in: app)
        assertEventCount(1, in: app)
        captureRehearsal("05-acknowledged-not-armed", in: app)

        // Re-selecting REHEARSAL is the explicit empty-scene reset. It must not
        // erase the prior event or silently arm the next run.
        startRehearsal(in: app)
        assertEventCount(1, in: app)
        tap("camera-arm", in: app)
        phase("armed", in: app)
        tap("camera-simulate-entry", in: app)
        phase("alert", in: app)
        assertEventCount(2, in: app)
        assertRehearsalJournal(in: app, count: 2)
        assertRemainsLatched(eventCount: 2, in: app)
        capture("REHEARSAL-06-two-explicit-runs-two-journal-records")
        tap("camera-acknowledge", in: app)
        phase(in: ["clearing", "ready"], app: app)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
        assertCannotActivate("camera-arm", in: app)
        assertEventCount(2, in: app)
    }

    func testDisarmAndStopRequireAnExplicitNewArm() {
        let app = launch()
        startRehearsal(in: app)
        tap("camera-arm", in: app)
        phase("armed", in: app)
        tap("camera-disarm", in: app)
        phase(in: ["clearing", "ready"], app: app)
        assertDoesNotAutoArm(in: app)
        XCTAssertEqual(events(in: app).count, 0)
        phase("ready", in: app)

        tap("camera-arm", in: app)
        phase("armed", in: app)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
        assertCannotActivate("camera-arm", in: app)
        assertCannotActivate("camera-simulate-entry", in: app)
        assertRemainsStopped(in: app)
        captureRehearsal("07-stopped-and-disarmed", in: app)

        startRehearsal(in: app)
        phase("ready", in: app)
        XCTAssertEqual(events(in: app).count, 0)
        assertDoesNotAutoArm(in: app)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
    }

    func testLeavingForegroundStopsAndDoesNotResumeMonitoring() {
        let app = launch()
        startRehearsal(in: app)
        tap("camera-arm", in: app)
        phase("armed", in: app)
        XCUIDevice.shared.press(.home)
        waitFor(NSPredicate { _, _ in
            app.state == .runningBackground || app.state == .runningBackgroundSuspended
        }, object: app, message: "App must actually leave the foreground")
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 5))
        phase("stopped", in: app)
        assertCannotActivate("camera-arm", in: app)
        assertCannotActivate("camera-simulate-entry", in: app)
        XCTAssertEqual(events(in: app).count, 0)
        assertRemainsStopped(in: app)
        captureRehearsal("08-foreground-return-stays-stopped", in: app)

        startRehearsal(in: app)
        phase("ready", in: app)
        XCTAssertEqual(events(in: app).count, 0)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
    }

    func testRehearsalStartsAfterReturningFromBackgroundedRadioLab() {
        let app = launch()
        tap("camera-open-radio-lab", in: app)
        let lab = app.otherElements["instrument-root"]
        XCTAssertTrue(lab.waitForExistence(timeout: 5))
        // Open only the separate offline tools. Do not pair or send controls.
        XCUIDevice.shared.press(.home)
        waitFor(NSPredicate { _, _ in
            app.state == .runningBackground || app.state == .runningBackgroundSuspended
        }, object: app, message: "The radio lab must actually leave the foreground")
        app.activate()
        XCTAssertTrue(app.wait(for: .runningForeground, timeout: 5))
        XCTAssertTrue(lab.waitForExistence(timeout: 5))

        let close = app.buttons["radio-lab-close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        XCTAssertTrue(close.isHittable)
        close.tap()
        let exitAlert = app.alerts["Leave radio lab?"]
        XCTAssertTrue(exitAlert.waitForExistence(timeout: 5))
        let leave = exitAlert.buttons["Leave radio lab"]
        XCTAssertTrue(leave.waitForExistence(timeout: 5))
        waitFor(NSPredicate { _, _ in leave.isHittable }, object: leave,
                message: "The explicit radio-lab exit confirmation must be usable")
        XCTAssertTrue(leave.isHittable)
        capture("CAMERA-DEMO-04-explicit-radio-lab-exit-confirmation")
        leave.tap()
        waitFor(NSPredicate(format: "exists == false"), object: close,
                message: "The radio lab must finish dismissing")
        XCTAssertTrue(app.otherElements["camera-demo-root"].waitForExistence(timeout: 5))
        phase("stopped", in: app)
        source("CAMERA DEMO", in: app)
        assertRemainsStopped(in: app)

        // Dismissing the lab does not produce a new scenePhase transition.
        // The root must explicitly restore camera foreground eligibility.
        startRehearsal(in: app)
        phase("ready", in: app)
        XCTAssertEqual(events(in: app).count, 0)
        assertDoesNotAutoArm(in: app)
        captureRehearsal("10-ready-after-backgrounded-radio-lab-return", in: app)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
    }

    func testSimulatorCameraUnavailableRequiresExplicitRehearsalRecovery() throws {
        #if targetEnvironment(simulator)
        let app = launch()
        tap("camera-start", in: app)
        phase("unavailable", in: app, timeout: 10)
        source("CAMERA DEMO", in: app)
        let status = app.staticTexts["camera-status"]
        XCTAssertTrue(status.exists)
        XCTAssertTrue(status.label.lowercased().contains("camera"), "Explain the unavailable camera")
        assertCannotActivate("camera-arm", in: app)
        XCTAssertEqual(events(in: app).count, 0)
        reveal(status, in: app)
        capture("CAMERA-DEMO-03-simulator-unavailable-not-live-evidence")

        // Simulator unavailability is real adapter failure, not a camera fixture.
        // This does not test physical permission refusal or Vision detection.
        startRehearsal(in: app)
        captureRehearsal("09-explicit-recovery-from-unavailable-camera", in: app)
        tap("camera-arm", in: app)
        phase("armed", in: app)
        tap("camera-simulate-entry", in: app)
        phase("alert", in: app)
        assertEventCount(1, in: app)
        assertRehearsalJournal(in: app, count: 1)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
        #else
        throw XCTSkip("Simulator-only hardware-unavailable path. Use the physical iPhone acceptance checklist for permission and Vision checks.")
        #endif
    }

    func testLargestDynamicTypeKeepsPrimaryControlsReachableAndAtLeast44Points() {
        let app = launch(textSize: .accessibilityExtraExtraExtraLarge)
        control("camera-start", in: app)
        tap("camera-rehearsal", in: app)
        phase("ready", in: app)
        source("REHEARSAL", in: app)
        assertNoPermissionPrompt(in: app)
        captureRehearsal("AXXXL-01-empty-ready-synthetic", in: app)

        control("camera-arm", in: app)
        capture("REHEARSAL-AXXXL-02-arm-control")
        tap("camera-arm", in: app)
        phase("armed", in: app)
        control("camera-disarm", in: app)
        control("camera-stop", in: app)
        control("camera-simulate-entry", in: app)
        capture("REHEARSAL-AXXXL-03-simulate-entry-control")
        tap("camera-simulate-entry", in: app)
        phase("alert", in: app)
        assertEventCount(1, in: app)
        control("camera-acknowledge", in: app)
        capture("REHEARSAL-AXXXL-04-acknowledge-control")
        tap("camera-acknowledge", in: app)
        phase(in: ["clearing", "ready"], app: app)
        assertDoesNotAutoArm(in: app)
        startRehearsal(in: app)
        tap("camera-arm", in: app)
        phase("armed", in: app)
        tap("camera-disarm", in: app)
        phase(in: ["clearing", "ready"], app: app)
        tap("camera-stop", in: app)
        phase("stopped", in: app)
        captureRehearsal("AXXXL-05-stopped-synthetic", in: app)
    }

    private func launch(textSize: UIContentSizeCategory? = nil) -> XCUIApplication {
        let app = XCUIApplication()
        if let textSize {
            app.launchArguments = ["-UIPreferredContentSizeCategoryName", textSize.rawValue]
        }
        app.launch()
        XCTAssertTrue(app.otherElements["camera-demo-root"].waitForExistence(timeout: 10))
        phase("stopped", in: app)
        return app
    }

    private func startRehearsal(in app: XCUIApplication) {
        tap("camera-rehearsal", in: app)
        phase("ready", in: app) // allows real starting/clearing debounce, up to 5 s
        source("REHEARSAL", in: app)
        assertNoPermissionPrompt(in: app)
    }

    private func source(_ expected: String, in app: XCUIApplication) {
        waitFor(NSPredicate(format: "label == %@", expected), object: app.staticTexts["camera-source"],
                message: "Expected explicit source \(expected)")
    }

    private func phase(_ expected: String, in app: XCUIApplication, timeout: TimeInterval = 5) {
        phase(in: [expected], app: app, timeout: timeout)
    }

    private func phase(in expected: [String], app: XCUIApplication, timeout: TimeInterval = 5) {
        waitFor(NSPredicate(format: "label IN %@", expected), object: app.staticTexts["camera-phase"],
                timeout: timeout, message: "Expected phase \(expected); saw \(app.staticTexts["camera-phase"].label)")
    }

    private func events(in app: XCUIApplication) -> XCUIElementQuery {
        app.descendants(matching: .any).matching(identifier: "camera-event")
    }

    private func assertEventCount(_ expected: Int, in app: XCUIApplication) {
        waitFor(NSPredicate { _, _ in self.events(in: app).count == expected }, object: app,
                message: "Expected exactly \(expected) camera event record(s)")
    }

    private func assertRehearsalJournal(in app: XCUIApplication, count: Int) {
        let rows = events(in: app)
        XCTAssertEqual(rows.count, count)
        for index in 0..<count {
            let row = rows.element(boundBy: index)
            reveal(row, in: app)
            XCTAssertTrue(row.label.uppercased().contains("REHEARSAL"),
                          "Every synthetic event must retain its source provenance")
        }
    }

    private func assertRemainsLatched(eventCount: Int, in app: XCUIApplication) {
        let changed = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in
            app.staticTexts["camera-phase"].label != "alert" || self.events(in: app).count != eventCount
        }, object: app)
        changed.isInverted = true
        // Observe across multiple 0.4 s debounce intervals; no arbitrary sleep.
        XCTAssertEqual(XCTWaiter.wait(for: [changed], timeout: 1.2), .completed,
                       "The alert must stay latched without duplicate events")
    }

    private func assertRemainsStopped(in app: XCUIApplication) {
        let resumed = XCTNSPredicateExpectation(predicate: NSPredicate(format: "label != %@", "stopped"),
                                                object: app.staticTexts["camera-phase"])
        resumed.isInverted = true
        XCTAssertEqual(XCTWaiter.wait(for: [resumed], timeout: 1.2), .completed,
                       "Monitoring must stay stopped until an explicit source start")
    }

    private func assertDoesNotAutoArm(in app: XCUIApplication) {
        let unsafe = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "label IN %@", ["armed", "alert", "starting"]),
            object: app.staticTexts["camera-phase"])
        unsafe.isInverted = true
        XCTAssertEqual(XCTWaiter.wait(for: [unsafe], timeout: 1.2), .completed,
                       "Recovery must not restart, arm, or alert by itself")
    }

    private func assertCannotActivate(_ identifier: String, in app: XCUIApplication) {
        let button = app.buttons[identifier]
        XCTAssertTrue(!button.exists || !button.isEnabled, "Unsafe action is enabled: \(identifier)")
    }

    private func assertNoPermissionPrompt(in app: XCUIApplication) {
        XCTAssertFalse(app.alerts.firstMatch.exists, "No login or permission prompt belongs in rehearsal/default launch")
        XCTAssertFalse(XCUIApplication(bundleIdentifier: "com.apple.springboard").alerts.firstMatch.exists,
                       "Default launch and explicit rehearsal must not request system permissions")
    }

    @discardableResult
    private func control(_ identifier: String, in app: XCUIApplication,
                         file: StaticString = #filePath, line: UInt = #line) -> XCUIElement {
        let button = app.buttons[identifier]
        reveal(button, in: app, file: file, line: line)
        XCTAssertTrue(button.exists, "Missing control: \(identifier)", file: file, line: line)
        XCTAssertTrue(button.isEnabled, "Disabled control: \(identifier)", file: file, line: line)
        XCTAssertTrue(button.isHittable, "Unreachable control: \(identifier)", file: file, line: line)
        let frame = button.frame
        XCTAssertGreaterThanOrEqual(frame.height, 44, identifier, file: file, line: line)
        XCTAssertGreaterThanOrEqual(frame.width, 44, identifier, file: file, line: line)
        XCTAssertTrue(app.windows.firstMatch.frame.insetBy(dx: -0.5, dy: -0.5).contains(frame),
                      "Control must fit the app window after scrolling: \(identifier)", file: file, line: line)
        return button
    }

    private func tap(_ identifier: String, in app: XCUIApplication) {
        control(identifier, in: app).tap()
    }

    private func reveal(_ element: XCUIElement, in app: XCUIApplication,
                        file: StaticString = #filePath, line: UInt = #line) {
        let scroll = app.scrollViews.firstMatch
        for _ in 0..<16 {
            let window = app.windows.firstMatch.frame
            if element.exists && element.isHittable && window.contains(element.frame) { return }
            guard scroll.exists else { break }
            let viewport = scroll.frame.intersection(window)
            if element.exists && element.frame.minY < viewport.minY { scroll.swipeDown() }
            else { scroll.swipeUp() }
        }
        XCTFail("Element did not become fully reachable: \(element.identifier)", file: file, line: line)
    }

    private func waitFor(_ predicate: NSPredicate, object: Any?, timeout: TimeInterval = 5,
                         message: String, file: StaticString = #filePath, line: UInt = #line) {
        let condition = XCTNSPredicateExpectation(predicate: predicate, object: object)
        XCTAssertEqual(XCTWaiter.wait(for: [condition], timeout: timeout), .completed,
                       message, file: file, line: line)
    }

    private func assertApertureAboveDock(in app: XCUIApplication) {
        let aperture = app.descendants(matching: .any).matching(identifier: "camera-aperture").firstMatch
        let dock = app.otherElements["camera-control-dock"]
        XCTAssertTrue(aperture.exists); XCTAssertTrue(dock.exists)
        XCTAssertGreaterThan(aperture.frame.width, 0)
        XCTAssertGreaterThan(aperture.frame.height, 0)
        // The accessibility frame includes the centering container. The preview's
        // pixel aperture remains 3:4 and is checked in source and render review.
        XCTAssertLessThanOrEqual(aperture.frame.maxY, dock.frame.minY + 1,
                                 "The fixed doorway aperture and its provenance must not hide beneath the dock")
    }

    private func captureRehearsal(_ name: String, in app: XCUIApplication) {
        source("REHEARSAL", in: app)
        reveal(app.staticTexts["camera-source"], in: app)
        capture("REHEARSAL-\(name)")
    }

    private func capture(_ name: String) {
        let attachment = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}
