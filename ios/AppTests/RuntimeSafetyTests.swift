import XCTest
import ARKit
import SceneKit
@testable import Threshold
import ThresholdDomain

@MainActor private func fixtureState(_ alarm: String = "READY") throws -> BackendState {
    let event: [String:Any] = ["event_id":"fixture-event","type":"zone_entry","source_mode":"TEST","occurred_at":"2026-09-12T12:00:00Z","area_name":"UNIT FIXTURE","acknowledged":false,"activity_score":8.0,"threshold":3.0,"call_status":"disabled"]
    var json: [String:Any] = ["schema_version":1,"session_id":"UNIT-FIXTURE","source_mode":"TEST","area_name":"UNIT FIXTURE — NO NETWORK","server_time":"2026-09-12T12:00:00Z","alarm_state":alarm,
        "health":["status":"healthy","detail":"Synthetic unit fixture","sample_age_s":0.1,"packet_rate_hz":40.0,"valid_packets":100,"invalid_packets":0,"dropped_packets_estimate":0],
        "calibration":["progress":1.0,"ready":true,"detail":"Unit fixture"],"features":["activity_score":0.0,"threshold":3.0,"baseline_ready":true],"graph":[],"events":alarm == "ALARM" ? [event]:[],
        "calls":["enabled":false,"dry_run":true,"status":"disabled","detail":"No network"],"limitations":["UNIT FIXTURE"],
        "spatial":["target":"radio-motion","localization":["status":"unavailable","provenance":"none","exact_zone_verified":false,"detail":"No person located"],"test_actor":["running":false,"phase":"unavailable","can_start":false,"can_reset":false],"guard":["eligible":false,"reason":"No entry"]]]
    if alarm == "ALARM" { json["active_event_id"] = "fixture-event" }
    return try BackendState.decode(JSONSerialization.data(withJSONObject:json))
}

@MainActor private final class ControlledTransport: BackendTransport {
    let endpoint = try! ServerEndpoint("http://127.0.0.1:1")
    var server = try! fixtureState()
    var stateCalls = 0, inFlight = 0, maxInFlight = 0
    var holdGET = false, holdArm = false, holdDisarm = false
    var onGET: (() -> Void)?, onControl: (() -> Void)?, onPublish: (() -> Void)?
    var getReplies: [CheckedContinuation<BackendState,Error>] = []
    var getReply: CheckedContinuation<BackendState,Error>? {
        get { getReplies.first }
        set { if newValue == nil && !getReplies.isEmpty { getReplies.removeFirst() } }
    }
    var commandReply: CheckedContinuation<BackendState,Error>?
    var publishReply: CheckedContinuation<BackendState,Error>?
    var commands: [ControlAction] = []
    var submitted: ZonePayload?
    func close() {}
    func state() async throws -> BackendState {
        stateCalls += 1
        guard holdGET else { return server }
        inFlight += 1; maxInFlight = max(maxInFlight,inFlight)
        defer { inFlight -= 1 }
        return try await withCheckedThrowingContinuation { getReplies.append($0); onGET?() }
    }
    func control(action: ControlAction, extra: [String:Any]) async throws -> BackendState {
        commands.append(action)
        if action == .arm { server = try fixtureState("ARMED") }
        if action == .disarm { server = try fixtureState("DISARMED") }
        if (action == .arm && holdArm) || (action == .disarm && holdDisarm) {
            return try await withCheckedThrowingContinuation { commandReply = $0; onControl?() }
        }
        return server
    }
    func publish(_ zone: ZonePayload) async throws -> BackendState {
        submitted = zone
        return try await withCheckedThrowingContinuation { publishReply = $0; onPublish?() }
    }
}
@MainActor private final class SpySound: AlarmSound {
    var latest: (event: String?, alarm: Bool, foreground: Bool)?
    override func observe(eventID: String?, alarm: Bool, foreground: Bool) { latest = (eventID,alarm,foreground) }
    override func stop() {}
}

@MainActor final class RuntimeSafetyTests: XCTestCase {
    private func paired(_ transport: ControlledTransport, sound: AlarmSound? = nil) async -> InstrumentStore {
        let store = InstrumentStore(clientFactory: { _,_ in transport }, automaticallyPoll:false, sound:sound, clockEnabled:false)
        store.endpointText = "http://127.0.0.1:1"
        await store.connect(token:String(repeating:"a",count:43),approveLAN:false)
        XCTAssertTrue(store.paired)
        return store
    }
    func testActualSceneKitNodesAndHitClearedOnResetDetachReattach() {
        var cameraRuns = 0
        let outline = FloorOutline(runCameraSession: { _,_ in cameraRuns += 1 })
        let first = ARSCNView(); outline.attach(first); outline.testBeginARWithoutCamera(); outline.testPopulateFrameResources()
        let old = outline.testWorldFrame
        XCTAssertGreaterThan(outline.testRenderCount,0); XCTAssertNotNil(outline.testCachedHit)
        outline.testBeginARWithoutCamera()
        XCTAssertNotEqual(outline.testWorldFrame,old)
        XCTAssertEqual(outline.testRenderCount,0); XCTAssertNil(outline.testCachedHit); XCTAssertFalse(outline.trackingReady)
        outline.testPopulateFrameResources()
        outline.detach(first)
        XCTAssertEqual(outline.testRenderCount,0); XCTAssertNil(outline.testCachedHit); XCTAssertNil(outline.testWorldFrame)
        XCTAssertEqual(outline.mode,.idle)
        let second = ARSCNView(); outline.attach(second)
        XCTAssertEqual(outline.testRenderCount,0); XCTAssertNil(outline.testCachedHit)
        outline.testBeginARWithoutCamera()
        XCTAssertNotEqual(outline.testWorldFrame,old); XCTAssertFalse(outline.trackingReady)
        XCTAssertEqual(cameraRuns,3,"Only the no-camera adapter was called")
        outline.detach(second)
    }
    func testActualStoreUnconfirmedArmRetainsSafeDisarmAndNoDuplicatePOST() async throws {
        let api = ControlledTransport()
        let actual = await paired(api)
        let held = expectation(description:"Arm accepted but response held")
        api.holdArm = true; api.onControl = { held.fulfill() }
        let arm = Task { await actual.command(.arm) }
        await fulfillment(of:[held],timeout:2)
        XCTAssertEqual(api.server.alarmState,.armed); XCTAssertEqual(actual.state?.alarmState,.ready)
        api.commandReply?.resume(throwing:DomainError.invalid("Response lost after accept")); api.commandReply=nil
        await arm.value
        XCTAssertEqual(actual.state?.alarmState,.ready); XCTAssertNotNil(actual.fault)
        XCTAssertTrue(actual.allowed(.disarm)); XCTAssertTrue(ControlPresentation.offersSafeDisarm(paired:actual.paired,cachedAlarm:actual.state?.alarmState))
        let disarmHeld = expectation(description:"Safe disarm held")
        api.holdDisarm = true; api.onControl = { disarmHeld.fulfill() }
        let disarm = Task { await actual.command(.disarm) }
        await fulfillment(of:[disarmHeld],timeout:2)
        await actual.command(.disarm)
        XCTAssertEqual(api.commands.filter { $0 == .disarm }.count,1)
        XCTAssertEqual(actual.state?.alarmState,.ready,"No optimistic completion")
        api.commandReply?.resume(returning:try fixtureState("DISARMED")); api.commandReply=nil
        await disarm.value
        XCTAssertEqual(actual.state?.alarmState,.disarmed); XCTAssertNil(actual.fault)
    }
    func testActualPublishOldAckDoesNotCleanNewNameOrPermitArm() async throws {
        let api = ControlledTransport(), outline = FloorOutline(runCameraSession:{ _,_ in })
        let store = await paired(api)
        outline.exampleTestRectangle(); outline.name = "A"; store.draftDirty = true
        let held = expectation(description:"Publish A held")
        api.onPublish = { held.fulfill() }
        let send = Task { try await outline.publish(to:store) }
        await fulfillment(of:[held],timeout:2)
        XCTAssertTrue(outline.publishPending); XCTAssertEqual(api.submitted?.name,"A")
        let before = outline.draft.vertices
        outline.undo(); outline.exampleTestRectangle()
        XCTAssertEqual(outline.draft.vertices,before,"Mutating controls are frozen")
        // An out-of-band binding update while awaiting still cannot be certified by A.
        outline.name = "B"
        api.publishReply?.resume(returning:try fixtureState()); api.publishReply=nil
        let mayDismissEditor = try await send.value
        XCTAssertFalse(mayDismissEditor); XCTAssertEqual(outline.name,"B")
        XCTAssertTrue(outline.dirty); XCTAssertTrue(store.draftDirty); XCTAssertFalse(store.allowed(.arm))
        XCTAssertEqual(outline.mode,.testPlan); XCTAssertFalse(outline.publishPending)
    }
    func testActualPublishFrameInvalidationRejectsOldAck() async throws {
        let api = ControlledTransport(), outline = FloorOutline(runCameraSession:{ _,_ in })
        let store = await paired(api); outline.exampleTestRectangle(); store.draftDirty=true
        let held=expectation(description:"Publish frame A held");api.onPublish={ held.fulfill() }
        let send=Task { try await outline.publish(to:store) }
        await fulfillment(of:[held],timeout:2)
        outline.stop(reason:"TEST interruption invalidated frame")
        api.publishReply?.resume(returning:try fixtureState());api.publishReply=nil
        let mayDismissEditor=try await send.value
        XCTAssertFalse(mayDismissEditor);XCTAssertNil(outline.testWorldFrame);XCTAssertTrue(outline.invalidated)
    }
    func testActualStoreCoalescesRefreshAndPreservesAlarmAndSoundInput() async throws {
        let api=ControlledTransport(), sound=SpySound()
        let store=await paired(api,sound:sound)
        let held=expectation(description:"GET A held");api.holdGET=true;api.onGET={ held.fulfill() }
        let a=Task { await store.refresh() }
        await fulfillment(of:[held],timeout:2)
        api.onGET = nil
        let coalesced = expectation(description:"Duplicate refreshes return without issuing GETs")
        coalesced.expectedFulfillmentCount = 2
        let b = Task { await store.refresh(); coalesced.fulfill() }
        let c = Task { await store.refresh(); coalesced.fulfill() }
        await fulfillment(of:[coalesced],timeout:1)
        XCTAssertEqual(api.stateCalls,2,"Pair + one shared in-flight refresh only")
        XCTAssertEqual(api.maxInFlight,1);XCTAssertTrue(store.refreshing)
        // A legacy overlapping implementation is still drained deterministically:
        // newer ALARM arrives before old ARMED. No leaked continuation or hung test.
        let replies = api.getReplies; api.getReplies.removeAll()
        for (offset, reply) in replies.reversed().enumerated() {
            reply.resume(returning:try fixtureState(offset == 0 ? "ALARM" : "ARMED"))
        }
        await a.value; await b.value; await c.value
        XCTAssertEqual(store.state?.alarmState,.alarm);XCTAssertEqual(store.state?.activeEventId,"fixture-event")
        XCTAssertEqual(sound.latest?.event,"fixture-event");XCTAssertEqual(sound.latest?.alarm,true)
        XCTAssertNil(store.fault);XCTAssertNotNil(store.lastResponse);XCTAssertFalse(store.refreshing)
    }
    func testOldRefreshFailureCannotOverrideNewCommandSuccess() async throws {
        let api=ControlledTransport()
        let actual=await paired(api)
        let held=expectation(description:"Old GET held");api.holdGET=true;api.onGET={ held.fulfill() }
        let old=Task { await actual.refresh() };await fulfillment(of:[held],timeout:2)
        await actual.command(.disarm)
        let confirmedAt=actual.lastResponse
        api.getReply?.resume(throwing:DomainError.invalid("Old GET failure"));api.getReply=nil
        await old.value
        XCTAssertEqual(actual.state?.alarmState,.disarmed);XCTAssertNil(actual.fault);XCTAssertEqual(actual.lastResponse,confirmedAt)
    }
}

@MainActor final class PresentationTests: XCTestCase {
    func testSavedPlanFitPreservesVerticesAndUsesReservedViewport() throws {
        let vertices = [Vertex(x:-2,z:4),Vertex(x:10,z:4),Vertex(x:10,z:7),Vertex(x:-2,z:7)]
        let before = vertices
        let fit = try XCTUnwrap(SavedPlanProjection(vertices:vertices,size:CGSize(width:393,height:310)))
        for vertex in vertices { XCTAssertTrue(fit.plotRect.contains(fit.point(vertex))) }
        XCTAssertEqual(vertices,before,"Display fitting cannot edit authoritative coordinates")
        let centre=fit.point(Vertex(x:4,z:5.5))
        XCTAssertEqual(centre.x,fit.plotRect.midX,accuracy:0.000001)
        XCTAssertEqual(centre.y,fit.plotRect.midY,accuracy:0.000001)
    }
    func testSavedPlanFitKeepsAspectAndBothAxisDirections() throws {
        let a=Vertex(x:2,z:2), b=Vertex(x:8,z:2), c=Vertex(x:8,z:6), d=Vertex(x:2,z:6)
        let fit=try XCTUnwrap(SavedPlanProjection(vertices:[a,b,c,d],size:CGSize(width:393,height:310)))
        let pa=fit.point(a), pb=fit.point(b), pc=fit.point(c)
        XCTAssertGreaterThan(pb.x,pa.x);XCTAssertGreaterThan(pc.y,pb.y)
        XCTAssertEqual((pb.x-pa.x)/6,(pc.y-pb.y)/4,accuracy:0.000001)
    }
    func testSavedPlanFitRejectsInvalidInputsAndReservesLargeTextCaptions() throws {
        let v=[Vertex(x:0,z:0),Vertex(x:2,z:0),Vertex(x:0,z:2)]
        XCTAssertNil(SavedPlanProjection(vertices:[],size:CGSize(width:393,height:310)))
        XCTAssertNil(SavedPlanProjection(vertices:[Vertex(x:.nan,z:0)],size:CGSize(width:393,height:310)))
        XCTAssertNil(SavedPlanProjection(vertices:v,size:.zero))
        XCTAssertNil(SavedPlanProjection(vertices:[Vertex(x:-1e308,z:0),Vertex(x:1e308,z:0)],size:CGSize(width:393,height:310)))
        let fit=try XCTUnwrap(SavedPlanProjection(vertices:v,size:CGSize(width:393,height:400),topInset:120,bottomInset:160))
        XCTAssertEqual(fit.plotRect.minY,120);XCTAssertEqual(fit.plotRect.maxY,240)
        for vertex in v { XCTAssertTrue(fit.plotRect.contains(fit.point(vertex))) }
    }
    func testLocalTimeDisplayParsesUTCWithoutReplacingExactRecord() throws {
        let raw="2026-09-12T22:09:00.123+00:00"
        let whole=try XCTUnwrap(EventTimeDisplay.date(from:"2026-09-12T22:09:00Z"))
        let fraction=try XCTUnwrap(EventTimeDisplay.date(from:raw))
        XCTAssertEqual(fraction.timeIntervalSince(whole),0.123,accuracy:0.000001)
        XCTAssertEqual(raw,"2026-09-12T22:09:00.123+00:00")
        XCTAssertNil(EventTimeDisplay.date(from:"not a timestamp"))
    }
}
