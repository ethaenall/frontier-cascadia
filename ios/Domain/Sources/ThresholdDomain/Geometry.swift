import Foundation

public struct Vertex: Codable, Equatable, Sendable {
    public var x: Double
    public var z: Double
    public init(x: Double, z: Double) { self.x = x; self.z = z }
}
public struct FrameDraft: Sendable {
    public private(set) var frameID: String?
    public private(set) var vertices: [Vertex] = []
    public private(set) var floorY: Double?
    public init() {}
    public mutating func begin(frameID: String) {
        // Never carry world coordinates across an origin change, even without invalidate().
        if self.frameID != frameID { vertices = []; floorY = nil }
        self.frameID = frameID
    }
    public mutating func append(_ vertex: Vertex, floorY: Double? = nil) {
        guard frameID != nil, vertices.count < 16, vertex.x.isFinite, vertex.z.isFinite else { return }
        vertices.append(vertex)
        if self.floorY == nil { self.floorY = floorY }
    }
    public mutating func undo() { if !vertices.isEmpty { vertices.removeLast() }; if vertices.isEmpty { floorY = nil } }
    public mutating func invalidate() { frameID = nil; vertices = []; floorY = nil }
    public func payload(name: String, coordinateSpace: String, currentFrameID: String?) throws -> ZonePayload {
        guard let frameID, frameID == currentFrameID else { throw DomainError.invalid("This outline belongs to an invalid AR frame. Start a new outline.") }
        return try ZonePayload(name: name, vertices: vertices, coordinateSpace: coordinateSpace, frameId: frameID, floorY: floorY)
    }
}

public struct ZonePayload: Encodable, Sendable {
    public let action = "set_zone"
    public let name: String
    public let vertices: [Vertex]
    public let coordinateSpace: String
    public let frameId: String
    public let floorY: Double?
    enum CodingKeys: String, CodingKey { case action, name, vertices; case coordinateSpace = "coordinate_space", frameId = "frame_id", floorY = "floor_y" }
    public init(name: String, vertices: [Vertex], coordinateSpace: String, frameId: String, floorY: Double? = nil) throws {
        let name = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard (1...64).contains(name.count), (1...96).contains(frameId.count), !frameId.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
              !name.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              !frameId.unicodeScalars.contains(where: CharacterSet.controlCharacters.contains),
              ["arkit-world", "test-plan"].contains(coordinateSpace),
              floorY == nil || (floorY!.isFinite && abs(floorY!) <= 10000) else { throw DomainError.invalid("Use a name of 1–64 characters and a valid coordinate frame.") }
        try Polygon.validate(vertices)
        self.name = name; self.vertices = vertices; self.coordinateSpace = coordinateSpace; self.frameId = frameId; self.floorY = floorY
    }
    public func encoded() throws -> Data { let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]; return try encoder.encode(self) }
}
public enum Polygon {
    static let epsilon = 1e-7
    static func cross(_ a: Vertex, _ b: Vertex, _ c: Vertex) -> Double { (b.x-a.x)*(c.z-a.z) - (b.z-a.z)*(c.x-a.x) }
    static func on(_ a: Vertex, _ b: Vertex, _ p: Vertex) -> Bool {
        abs(cross(a,b,p)) <= epsilon && p.x >= min(a.x,b.x)-epsilon && p.x <= max(a.x,b.x)+epsilon && p.z >= min(a.z,b.z)-epsilon && p.z <= max(a.z,b.z)+epsilon
    }
    static func intersects(_ a: Vertex, _ b: Vertex, _ c: Vertex, _ d: Vertex) -> Bool {
        let abC = cross(a,b,c), abD = cross(a,b,d), cdA = cross(c,d,a), cdB = cross(c,d,b)
        return (abC * abD < 0 && cdA * cdB < 0) || on(a,b,c) || on(a,b,d) || on(c,d,a) || on(c,d,b)
    }
    public static func validate(_ v: [Vertex]) throws {
        guard (3...16).contains(v.count), v.allSatisfy({ $0.x.isFinite && $0.z.isFinite && abs($0.x) <= 10000 && abs($0.z) <= 10000 }) else { throw DomainError.invalid("Outline needs 3–16 finite corners within 10,000 units.") }
        var area = 0.0
        for i in v.indices {
            let a = v[i], b = v[(i+1)%v.count], prev = v[(i+v.count-1)%v.count]
            area += a.x*b.z - b.x*a.z
            for j in v.indices where j > i {
                if hypot(a.x-v[j].x, a.z-v[j].z) <= epsilon { throw DomainError.invalid("Two corners overlap.") }
            }
            if abs(cross(prev,a,b)) <= epsilon && ((prev.x-a.x)*(b.x-a.x)+(prev.z-a.z)*(b.z-a.z)) > 0 { throw DomainError.invalid("An edge doubles back.") }
            for j in v.indices where j > i && j != i+1 && !(i == 0 && j == v.count-1) {
                if intersects(a,b,v[j],v[(j+1)%v.count]) { throw DomainError.invalid("Edges cross or touch. Outline the perimeter in order.") }
            }
        }
        guard abs(area)/2 >= 1e-6 else { throw DomainError.invalid("The outline has no usable area.") }
    }
}
