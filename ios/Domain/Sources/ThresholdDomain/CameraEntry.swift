import Foundation
import CoreGraphics

public enum CameraInputMode: String, Sendable { case camera, rehearsal }
public enum CameraWatchPhase: String, Sendable {
    case stopped, starting, clearing, ready, armed, alert, unavailable
}

/// A small, memory-only journal entry. No image, identity, or biometric data.
public struct CameraEntryEvent: Identifiable, Equatable, Sendable {
    public let id: String
    public let occurredAt: Date
    public let source: CameraInputMode
    public init(id: String, occurredAt: Date, source: CameraInputMode) {
        self.id = id; self.occurredAt = occurredAt; self.source = source
    }
}

public enum CameraEntryFailure: Sendable {
    case staleFrame, invalidFrame, captureUnavailable
}

/// One-person image-region presence, NOT identity, direction, or a physical crossing.
/// Both real Vision observations and explicit rehearsal frames use this exact rule.
/// Times are monotonic seconds, not wall-clock dates. Boxes use top-left portrait coordinates.
public struct CameraEntryRule: Sendable {
    public static let zone = CGRect(x: 0.2, y: 0.15, width: 0.6, height: 0.7)
    public static let maximumFrameAge: TimeInterval = 1.2
    public static let requiredFrames = 3
    public static let requiredDwell: TimeInterval = 0.4

    public private(set) var phase: CameraWatchPhase = .stopped
    public private(set) var failure: CameraEntryFailure?
    public private(set) var multiplePeople = false
    public private(set) var lastObservationAt: TimeInterval?
    private var clearSince: TimeInterval?
    private var clearCount = 0
    private var insideSince: TimeInterval?
    private var insideCount = 0

    public init() {}

    public mutating func begin() {
        resetObservation(); failure = nil; phase = .clearing
    }
    public mutating func stop() {
        resetObservation(); failure = nil; phase = .stopped
    }
    public mutating func fail(_ reason: CameraEntryFailure) {
        resetObservation(); failure = reason; phase = .unavailable
    }
    public mutating func disarm() {
        guard phase != .stopped, phase != .unavailable else { return }
        resetObservation(); phase = .clearing
    }

    public func canArm(at now: TimeInterval) -> Bool {
        phase == .ready && lastObservationAt.map { Self.fresh($0, at: now) } == true
    }
    @discardableResult public mutating func arm(at now: TimeInterval) -> Bool {
        guard phase == .ready else { return false }
        guard canArm(at: now) else { fail(.staleFrame); return false }
        clearCandidate(); phase = .armed
        return true
    }

    /// Returns true only for the single armed -> alert transition.
    @discardableResult public mutating func observe(boxes: [CGRect], capturedAt: TimeInterval, now: TimeInterval) -> Bool {
        guard phase != .stopped, phase != .unavailable else { return false }
        guard Self.fresh(capturedAt, at: now) else { fail(.staleFrame); return false }
        if let last = lastObservationAt {
            guard capturedAt > last else { fail(.invalidFrame); return false }
            guard capturedAt - last <= Self.maximumFrameAge else { fail(.staleFrame); return false }
        }
        guard boxes.allSatisfy(Self.validBox) else { fail(.invalidFrame); return false }
        lastObservationAt = capturedAt
        multiplePeople = boxes.count > 1
        // A latched event cannot repeat or disappear just because a person leaves.
        guard phase != .alert else { return false }
        if multiplePeople {
            clearBaseline(); clearCandidate(); phase = .clearing
            return false
        }
        if phase == .armed {
            guard let box = boxes.first,
                  Self.zone.contains(CGPoint(x: box.midX, y: box.midY)) else {
                clearCandidate(); return false
            }
            if insideSince == nil { insideSince = capturedAt }
            insideCount += 1
            if insideCount >= Self.requiredFrames,
               capturedAt - (insideSince ?? capturedAt) + 0.000001 >= Self.requiredDwell {
                phase = .alert; clearCandidate(); return true
            }
            return false
        }
        // Even a person OUTSIDE the zone prevents arming from a nonempty baseline.
        guard boxes.isEmpty else { clearBaseline(); phase = .clearing; return false }
        if clearSince == nil { clearSince = capturedAt }
        clearCount += 1
        if clearCount >= Self.requiredFrames,
           capturedAt - (clearSince ?? capturedAt) + 0.000001 >= Self.requiredDwell {
            phase = .ready
        }
        return false
    }

    /// A watchdog uses this even if capture stops delivering callbacks entirely.
    @discardableResult public mutating func expire(at now: TimeInterval) -> Bool {
        guard phase != .stopped, phase != .unavailable, let last = lastObservationAt else { return false }
        guard Self.fresh(last, at: now) else { fail(.staleFrame); return true }
        return false
    }

    /// Vision is bottom-left. Physically portrait-rotated pixels need only this Y flip.
    public static func displayBox(fromVision box: CGRect) -> CGRect? {
        guard validBox(box) else { return nil }
        return CGRect(x: box.minX, y: 1 - box.maxY, width: box.width, height: box.height)
    }
    private static func validBox(_ box: CGRect) -> Bool {
        !box.isNull && !box.isInfinite &&
        [box.origin.x, box.origin.y, box.size.width, box.size.height].allSatisfy { $0.isFinite } &&
        box.size.width > 0 && box.size.height > 0 &&
        box.minX >= 0 && box.minY >= 0 && box.maxX <= 1 && box.maxY <= 1
    }
    private static func fresh(_ captured: TimeInterval, at now: TimeInterval) -> Bool {
        captured.isFinite && now.isFinite && captured >= 0 && now >= captured && now - captured <= maximumFrameAge
    }
    private mutating func clearBaseline() { clearSince = nil; clearCount = 0 }
    private mutating func clearCandidate() { insideSince = nil; insideCount = 0 }
    private mutating func resetObservation() {
        clearBaseline(); clearCandidate(); lastObservationAt = nil; multiplePeople = false
    }
}
