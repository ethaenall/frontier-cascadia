import Foundation

/// Pure frame ownership for every rendered object and cached raycast hit.
public struct WorldFrameResources<Resource> {
    public private(set) var frameID: String?
    public private(set) var renderObjects: [Resource] = []
    public var hit: SIMD3<Float>?
    public init() {}
    public mutating func add(_ resource: Resource) { renderObjects.append(resource) }
    public mutating func clearRender(discard: (Resource) -> Void) {
        renderObjects.forEach(discard); renderObjects.removeAll()
    }
    public mutating func reset(frameID: String?, discard: (Resource) -> Void) {
        clearRender(discard: discard)
        hit = nil
        self.frameID = frameID
    }
}

/// Each GET has a distinct ticket, in addition to store epoch/command guards.
public struct RefreshOrdering: Sendable {
    private var generation: UInt64 = 0
    public init() {}
    public mutating func issue() -> UInt64 { generation &+= 1; return generation }
    public func accepts(_ ticket: UInt64) -> Bool { ticket == generation }
}

public struct PublishTicket: Equatable, Sendable {
    fileprivate let revision: UInt64
    fileprivate let frameID: String?
    fileprivate let payload: Data
}
public struct DraftPublication: Sendable {
    private var revision: UInt64 = 0
    public init() {}
    public mutating func edited() { revision &+= 1 }
    public func capture(frameID: String?, payload: Data) -> PublishTicket { PublishTicket(revision: revision, frameID: frameID, payload: payload) }
    public func accepts(_ ticket: PublishTicket, frameID: String?, payload: Data) -> Bool {
        ticket.revision == revision && ticket.frameID == frameID && ticket.payload == payload
    }
}

public enum ControlPresentation {
    public static func offersSafeDisarm(paired: Bool, cachedAlarm: AlarmState?) -> Bool {
        paired && cachedAlarm != nil
    }
}
