import XCTest
@testable import ThresholdDomain
final class ControlTests: XCTestCase {
    func snapshot(mode: String = "TEST", alarm: String = "READY", health: String = "healthy", target: String = "zone-entry", frame: String = "f", inside: Bool = false) throws -> BackendState {
        let json: [String:Any] = [
            "schema_version":1,"session_id":"session","source_mode":mode,"area_name":"Entrance","server_time":"2026-09-12T12:00:00Z","alarm_state":alarm,
            "health":["status":health,"detail":"Test","sample_age_s":0.1,"packet_rate_hz":40.0,"valid_packets":100,"invalid_packets":0,"dropped_packets_estimate":0],
            "calibration":["progress":1.0,"ready":true,"detail":"Ready"],"features":["activity_score":0.0,"threshold":5.0,"baseline_ready":true],
            "graph":[],"events":[],"calls":["enabled":false,"dry_run":true,"status":"disabled","detail":"No calls"],"limitations":["Not occupancy"],
            "spatial":["target":target,"zone":["id":"zone","revision":1,"name":"Entrance","vertices":[["x":0,"z":0],["x":2,"z":0],["x":2,"z":2]],"coordinate_space":"arkit-world","frame_id":"f","units":"metres"],
                "localization":["status":"test-simulated","provenance":"TEST","exact_zone_verified":false,"detail":"Synthetic"],
                "position":["x":-1.0,"z":-1.0,"frame_id":frame,"inside":inside,"source_mode":"TEST","synthetic":true],
                "test_actor":["running":false,"phase":"outside","can_start":true,"can_reset":true],"guard":["eligible":false,"reason":"No pending entry"]]
        ]
        return try BackendState.decode(JSONSerialization.data(withJSONObject:json))
    }
    func testARAuthoredTESTZoneCanArmWithoutEntryPermit() throws {
        XCTAssertTrue(ControlGate.allows(.arm,state:try snapshot(),fresh:true,busy:false))
    }
    func testLiveAndReplayExactZoneNeverArm() throws {
        for mode in ["LIVE","REPLAY"] { XCTAssertFalse(ControlGate.allows(.arm,state:try snapshot(mode:mode),fresh:true,busy:false)) }
    }
    func testRadioTargetStillAvailableInLiveAndReplay() throws {
        for mode in ["LIVE","REPLAY"] { XCTAssertTrue(ControlGate.allows(.arm,state:try snapshot(mode:mode,target:"radio-motion"),fresh:true,busy:false)) }
    }
    func testBusyDirtyFaultAndStaleBlockArming() throws {
        let state=try snapshot()
        XCTAssertFalse(ControlGate.allows(.arm,state:state,fresh:true,busy:true))
        XCTAssertFalse(ControlGate.allows(.arm,state:state,fresh:true,busy:false,draftDirty:true))
        XCTAssertFalse(ControlGate.allows(.arm,state:state,fresh:false,busy:false))
        XCTAssertFalse(ControlGate.allows(.arm,state:try snapshot(health:"stale"),fresh:true,busy:false))
        XCTAssertFalse(ControlGate.allows(.arm,state:nil,fresh:true,busy:false))
    }
    func testWrongFrameAndInsideBlockCalibration() throws {
        XCTAssertFalse(ControlGate.allows(.calibrate,state:try snapshot(frame:"other"),fresh:true,busy:false))
        XCTAssertFalse(ControlGate.allows(.calibrate,state:try snapshot(inside:true),fresh:true,busy:false))
    }
    func testDisarmAttemptSurvivesTransportFaultButNoDuplicates() throws {
        let state=try snapshot(alarm:"ALARM")
        XCTAssertTrue(ControlGate.allows(.disarm,state:state,fresh:false,busy:false))
        XCTAssertTrue(ControlGate.allows(.acknowledge,state:state,fresh:false,busy:false))
        XCTAssertFalse(ControlGate.allows(.disarm,state:state,fresh:false,busy:true))
    }
    func testImmutableConfigurationWhileArmed() throws {
        for action in [ControlAction.setZone,.clearZone,.setThreshold,.setTarget,.calibrate] { XCTAssertFalse(ControlGate.allows(action,state:try snapshot(alarm:"ARMED"),fresh:true,busy:false)) }
    }
    func testTestMotionNotUsedToPretendZoneEntry() throws {
        XCTAssertFalse(ControlGate.allows(.testMotion,state:try snapshot(),fresh:true,busy:false))
        XCTAssertFalse(ControlGate.allows(.testWalk,state:try snapshot(mode:"LIVE"),fresh:true,busy:false))
    }
}
