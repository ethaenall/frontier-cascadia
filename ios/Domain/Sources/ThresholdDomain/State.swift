import Foundation

public enum SourceMode: String, Codable, Sendable { case test = "TEST", live = "LIVE", replay = "REPLAY" }
public enum AlarmState: String, Codable, Sendable { case disarmed = "DISARMED", calibrating = "CALIBRATING", ready = "READY", armed = "ARMED", alarm = "ALARM" }
public enum DetectionTarget: String, Codable, Sendable { case radio = "radio-motion", zone = "zone-entry" }
public struct Health: Codable, Sendable {
    public let status: String
    public let detail: String
    public let sampleAgeS: Double?
    public let packetRateHz: Double
    public let validPackets: Int
    public let invalidPackets: Int
    public let droppedPacketsEstimate: Int
    public let serialPort: String?
}
public struct Calibration: Codable, Sendable { public let progress: Double; public let ready: Bool; public let detail: String }
public struct Features: Codable, Sendable { public let activityScore: Double?; public let threshold: Double; public let baselineReady: Bool }
public struct GraphPoint: Codable, Sendable { public let timestamp: String; public let activityScore: Double? }
public struct CallStatus: Codable, Sendable { public let status: String; public let detail: String; public let enabled: Bool; public let dryRun: Bool }
public struct Zone: Codable, Sendable {
    public let id: String; public let revision: Int; public let name: String
    public let vertices: [Vertex]; public let coordinateSpace: String; public let frameId: String
    public let floorY: Double?; public let units: String
}
public struct Localization: Codable, Sendable { public let status: String; public let provenance: String; public let exactZoneVerified: Bool; public let detail: String }
public struct Position: Codable, Sendable {
    public let x: Double; public let z: Double; public let frameId: String
    public let inside: Bool; public let sourceMode: String; public let synthetic: Bool
}
public struct TestActor: Codable, Sendable { public let running: Bool; public let phase: String; public let canStart: Bool; public let canReset: Bool }
public struct SpatialGuard: Codable, Sendable { public let eligible: Bool; public let reason: String }
public struct Spatial: Codable, Sendable {
    public let target: DetectionTarget; public let zone: Zone?; public let localization: Localization
    public let position: Position?; public let testActor: TestActor; public let guardInfo: SpatialGuard
    enum CodingKeys: String, CodingKey { case target, zone, localization, position, testActor; case guardInfo = "guard" }
}
public struct EventSpatial: Codable, Sendable {
    public let zoneId: String?; public let zoneRevision: Int?; public let frameId: String?
    public let provenance: String?; public let exactZoneVerified: Bool?
}
public struct AlarmEvent: Codable, Identifiable, Sendable {
    public var id: String { eventId }
    public let eventId: String; public let type: String; public let sourceMode: SourceMode
    public let occurredAt: String; public let areaName: String; public let acknowledged: Bool
    public let activityScore: Double; public let threshold: Double
    public let callStatus: String; public let callDetail: String?; public let spatial: EventSpatial?
}
public struct BackendState: Codable, Sendable {
    public let schemaVersion: Int; public let sessionId: String; public let sourceMode: SourceMode
    public let areaName: String; public let serverTime: String; public let alarmState: AlarmState
    public let health: Health; public let calibration: Calibration; public let features: Features
    public let graph: [GraphPoint]; public let events: [AlarmEvent]; public let activeEventId: String?
    public let calls: CallStatus; public let spatial: Spatial; public let limitations: [String]
    public static func decode(_ data: Data) throws -> BackendState {
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        let s = try d.decode(Self.self, from: data)
        guard s.schemaVersion == 1, !s.sessionId.isEmpty,
              ["connecting", "healthy", "stale", "disconnected", "degraded"].contains(s.health.status),
              s.features.threshold.isFinite, s.features.threshold > 0,
              s.features.activityScore == nil || (s.features.activityScore!.isFinite && s.features.activityScore! >= 0),
              (0...1).contains(s.calibration.progress), s.graph.count <= 10000, s.events.count <= 1000,
              s.spatial.localization.exactZoneVerified == false else { throw DomainError.invalid("Unsupported backend state; controls are locked.") }
        return s
    }
}

public enum ControlAction: String, Sendable { case calibrate, arm, disarm, acknowledge, testMotion = "test_motion", setThreshold = "set_threshold", setZone = "set_zone", clearZone = "clear_zone", setTarget = "set_target", testWalk = "test_walk", resetActor = "reset_test_actor" }
public enum ControlGate {
    public static func allows(_ action: ControlAction, state: BackendState?, fresh: Bool, busy: Bool, draftDirty: Bool = false) -> Bool {
        guard !busy, let s = state else { return false }
        // A safe attempt stays available even when the last observation is stale.
        if action == .disarm { return true }
        if action == .acknowledge { return s.alarmState == .alarm }
        guard fresh else { return false }
        let adjustable = s.alarmState == .disarmed || s.alarmState == .ready
        let healthy = s.health.status == "healthy" && s.health.sampleAgeS.map { $0 >= 0 && $0 <= 3 } == true
        let p = s.spatial.position
        let zoneReady = s.spatial.target == .radio || (s.sourceMode == .test && s.spatial.zone != nil && s.spatial.localization.status == "test-simulated" && p?.frameId == s.spatial.zone?.frameId && p?.inside == false && p?.synthetic == true && p?.sourceMode == "TEST" && !s.spatial.testActor.running)
        switch action {
        case .arm: return healthy && adjustable && zoneReady && !draftDirty && s.calibration.ready && s.features.baselineReady
        case .calibrate: return healthy && adjustable && zoneReady && !draftDirty
        case .setZone, .clearZone, .setTarget, .setThreshold: return adjustable
        case .testMotion: return s.sourceMode == .test && healthy && s.alarmState != .calibrating && s.spatial.target == .radio
        case .testWalk: return s.sourceMode == .test && healthy && s.alarmState != .calibrating && s.spatial.testActor.canStart && !draftDirty
        case .resetActor: return s.sourceMode == .test && s.spatial.testActor.canReset
        default: return false
        }
    }
}
