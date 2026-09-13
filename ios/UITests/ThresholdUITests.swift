import XCTest
import UIKit

final class ThresholdUITests: XCTestCase {
    private let tokenFile = ProcessInfo.processInfo.environment["THRESHOLD_TEST_TOKEN_FILE"] ?? ".local/control-token"
    override func setUpWithError() throws { continueAfterFailure = false }
    private func capture(_ name: String) {
        let a = XCTAttachment(screenshot: XCUIScreen.main.screenshot()); a.name = name; a.lifetime = .keepAlways; add(a)
    }
    private func reveal(_ element: XCUIElement, in app: XCUIApplication) {
        for _ in 0..<12 {
            if element.exists && element.isHittable { return }
            let scroll = app.scrollViews.firstMatch
            if element.exists && element.frame.maxY < scroll.frame.minY { scroll.swipeDown() }
            else { scroll.swipeUp() }
        }
    }
    private func value(_ element: XCUIElement, equals expected: String, timeout: TimeInterval = 20) {
        let predicate = NSPredicate(format: "label == %@", expected)
        expectation(for: predicate, evaluatedWith: element)
        waitForExpectations(timeout: timeout)
    }
    func testOfflineARIsHonestAndPublicEndpointRejected() {
        let app = XCUIApplication(); app.launchArguments = ["--radio-lab"]; app.launch()
        XCTAssertTrue(app.otherElements["instrument-root"].waitForExistence(timeout:10))
        XCTAssertFalse(app.buttons["arm"].isEnabled)
        XCTAssertTrue(app.staticTexts["ar-unavailable"].exists)
        capture("01-offline-boundary")
        app.buttons["start-ar"].tap()
        XCTAssertTrue(app.staticTexts["outline-status"].label.contains("AR unavailable"))
        app.buttons["connect"].tap()
        let endpoint=app.textFields["server-origin"]; XCTAssertTrue(endpoint.waitForExistence(timeout:5))
        endpoint.tap(); endpoint.press(forDuration:1.2)
        if app.menuItems["Select All"].waitForExistence(timeout:2) { app.menuItems["Select All"].tap() }
        endpoint.typeText(String(repeating:XCUIKeyboardKey.delete.rawValue,count:100)+"https://example.com")
        let token=app.secureTextFields["pair-token"]; token.tap(); token.typeText(String(repeating:"a",count:43)) // intentionally invalid test-only token
        app.swipeUp()
        let submit=app.buttons["pair-submit"]; reveal(submit,in:app); submit.tap()
        XCTAssertTrue(app.staticTexts["pair-error"].waitForExistence(timeout:5))
        XCTAssertTrue(app.staticTexts["pair-error"].label.contains("private IPv4"))
        capture("02-public-origin-refused")
    }
    func testGrantedProductionTESTBackendVerticalSlice() throws { try exerciseGrantedBackend(textSize:nil) }
    func testLargeTextGrantedBackendVerticalSlice() throws { try exerciseGrantedBackend(textSize:.accessibilityExtraLarge) }
    private func exerciseGrantedBackend(textSize:UIContentSizeCategory?) throws {
        // This is not a production credential or fixture. The lead grants this loopback
        // server and short-lived token file for this exact integration session.
        guard FileManager.default.fileExists(atPath:tokenFile) else { throw XCTSkip("Granted ephemeral server token file unavailable") }
        let app=XCUIApplication()
        app.launchArguments=["--integration"]
        if let textSize { app.launchArguments += ["-UIPreferredContentSizeCategoryName",textSize.rawValue] }
        app.launchEnvironment=["THRESHOLD_TEST_TOKEN_FILE":tokenFile,"THRESHOLD_TEST_ORIGIN":"http://127.0.0.1:18866","THRESHOLD_EXPECT_SESSION":"35537463-e460-4b78-985d-896aebe0eca7"]
        app.launch()
        value(app.staticTexts["source-mode"],equals:"TEST",timeout:15)
        value(app.staticTexts["alarm-state"],equals:"DISARMED")
        app.buttons["start-ar"].tap()
        XCTAssertTrue(app.staticTexts["outline-status"].label.contains("AR unavailable"))
        app.buttons["test-plan"].tap()
        let editor=app.buttons["outline-editor"];reveal(editor,in:app);editor.tap()
        let example=app.buttons["test-example"];XCTAssertTrue(example.waitForExistence(timeout:5));example.tap()
        app.buttons["publish-zone"].tap()
        XCTAssertTrue(app.buttons["outline-editor"].waitForExistence(timeout:8))
        XCTAssertEqual(app.staticTexts["geometry-provenance"].label,"SAVED TEST PLAN")
        capture("03-test-outline-saved")
        reveal(app.buttons["outline-editor"],in:app);app.buttons["outline-editor"].tap()
        let zone=app.buttons["target-zone"];reveal(zone,in:app);zone.tap()
        value(app.staticTexts["target-value"],equals:"zone-entry")
        app.buttons["editor-done"].tap()
        let calibrate=app.buttons["calibrate"]
        expectation(for:NSPredicate(format:"enabled == true"),evaluatedWith:calibrate);waitForExpectations(timeout:10)
        calibrate.tap()
        value(app.staticTexts["alarm-state"],equals:"READY",timeout:25)
        XCTAssertTrue(app.buttons["arm"].isEnabled);app.buttons["arm"].tap()
        value(app.staticTexts["alarm-state"],equals:"ARMED")
        capture("04-test-armed")
        let tools=app.staticTexts["TEST actor · synthetic only"];reveal(tools,in:app);tools.tap()
        let walk=app.buttons["test-walk"];reveal(walk,in:app);XCTAssertTrue(walk.isEnabled);walk.tap()
        value(app.staticTexts["alarm-state"],equals:"ALARM",timeout:20)
        capture("05-synthetic-alarm")
        app.buttons["acknowledge"].tap();value(app.staticTexts["alarm-state"],equals:"ARMED")
        app.buttons["disarm"].tap();value(app.staticTexts["alarm-state"],equals:"DISARMED")
        let reset=app.buttons["reset-actor"];reveal(reset,in:app);reset.tap()
        app.buttons["tab-journal"].tap()
        XCTAssertTrue(app.staticTexts["event-title"].firstMatch.waitForExistence(timeout:5))
        XCTAssertEqual(app.staticTexts["event-title"].firstMatch.label,"Synthetic zone entry")
        XCTAssertEqual(app.staticTexts["alarm-state"].label,"DISARMED")
        XCTAssertTrue(app.otherElements["alarm-dock"].exists)
        capture("06-shared-journal")
        let details=app.staticTexts["Record details"].firstMatch;reveal(details,in:app);details.tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format:"label BEGINSWITH %@","UTC ")).firstMatch.waitForExistence(timeout:5))
        capture("06b-exact-event-record")
        app.buttons["tab-signal"].tap()
        let sound=app.buttons["sound-test"];reveal(sound,in:app);sound.tap()
        XCTAssertTrue(app.staticTexts["sound-status"].exists)
        capture("07-signal-and-local-sound-request")
        // Leave the shared server disarmed and no actor running. Parent checks via API too.
        XCTAssertEqual(app.staticTexts["alarm-state"].label,"DISARMED")
    }
    func testAcceptedArmLostResponseStillHasVisibleSafeDisarm() {
        let app = XCUIApplication(); app.launchArguments = ["--ui-response-loss-fixture"]; app.launch()
        value(app.staticTexts["alarm-state"],equals:"READY")
        XCTAssertTrue(app.staticTexts["fault-fixture-label"].exists)
        app.buttons["arm"].tap()
        XCTAssertTrue(app.otherElements["connection-error"].waitForExistence(timeout:5))
        XCTAssertEqual(app.staticTexts["alarm-state"].label,"READY")
        let safe=app.buttons["safe-disarm"]
        XCTAssertTrue(safe.exists); XCTAssertTrue(safe.isHittable); XCTAssertTrue(safe.isEnabled)
        capture("08-response-loss-safe-disarm-visible-UNIT-FIXTURE")
        safe.tap(); value(app.staticTexts["alarm-state"],equals:"DISARMED")
        XCTAssertTrue(app.staticTexts["fault-fixture-label"].label.contains("Disarm POSTs: 1"))
        capture("09-safe-disarm-confirmed-once-UNIT-FIXTURE")
    }
    func testAccessibilityTextUnconfirmedArmKeepsSafeDockVisible() {
        let app=XCUIApplication()
        app.launchArguments=["--ui-response-loss-fixture","-UIPreferredContentSizeCategoryName",UIContentSizeCategory.accessibilityExtraExtraExtraLarge.rawValue]
        app.launch();value(app.staticTexts["alarm-state"],equals:"READY")
        let window=app.windows.firstMatch.frame
        let traits=UITraitCollection(preferredContentSizeCategory:.accessibilityExtraExtraExtraLarge)
        let nativeLineHeight=UIFont.preferredFont(forTextStyle:.subheadline,compatibleWith:traits).lineHeight
        for id in ["calibrate","arm","safe-disarm"] {
            let button=app.buttons[id]
            XCTAssertTrue(button.isHittable);XCTAssertGreaterThanOrEqual(button.frame.height,44)
            XCTAssertGreaterThanOrEqual(button.frame.height,nativeLineHeight + 19,"Dynamic text needs its native line height plus padding")
            XCTAssertTrue(window.contains(button.frame),"Control must fit the app window: \(id)")
        }
        capture("10-accessibility-ready-dock-UNIT-FIXTURE")
        app.buttons["arm"].tap()
        XCTAssertTrue(app.staticTexts["state-last-known"].waitForExistence(timeout:5))
        XCTAssertEqual(app.staticTexts["alarm-state"].label,"READY")
        XCTAssertTrue(app.staticTexts["dock-observation-status"].label.contains("State unavailable"))
        let safe=app.buttons["safe-disarm"]
        XCTAssertTrue(safe.isEnabled);XCTAssertTrue(safe.isHittable);XCTAssertTrue(window.contains(safe.frame))
        for tab in ["signal","journal","boundary"] {
            app.buttons["tab-\(tab)"].tap()
            XCTAssertEqual(app.staticTexts["alarm-state"].label,"READY")
            XCTAssertTrue(safe.isHittable)
            capture("11-accessibility-last-known-\(tab)-UNIT-FIXTURE")
        }
        safe.tap();value(app.staticTexts["alarm-state"],equals:"DISARMED")
        XCTAssertTrue(app.staticTexts["fault-fixture-label"].label.contains("Disarm POSTs: 1"))
    }
}
