import XCTest
@testable import ThresholdDomain
final class SafetyTests: XCTestCase {
    let rectangle = [Vertex(x: 0,z: 0),Vertex(x: 3,z: 0),Vertex(x: 3,z: 2),Vertex(x: 0,z: 2)]
    func testSerializationUsesXZAndOmitsUnknownFloor() throws {
        let p = try ZonePayload(name: "Entrance", vertices: rectangle, coordinateSpace: "arkit-world", frameId: UUID().uuidString)
        let json = try XCTUnwrap(JSONSerialization.jsonObject(with: p.encoded()) as? [String:Any])
        XCTAssertEqual(json["action"] as? String, "set_zone")
        XCTAssertEqual(json["coordinate_space"] as? String, "arkit-world")
        XCTAssertNil(json["floor_y"])
        XCTAssertEqual(Set((json["vertices"] as! [[String:Any]])[0].keys), ["x","z"])
    }
    func testMeasuredFloorSerialized() throws {
        let p = try ZonePayload(name: "Entrance", vertices: rectangle, coordinateSpace: "arkit-world", frameId: "frame", floorY: -1.2)
        let json = try JSONSerialization.jsonObject(with: p.encoded()) as! [String:Any]
        XCTAssertEqual(json["floor_y"] as? Double, -1.2)
    }
    func testWrongFrameCannotPublish() throws {
        var d = FrameDraft(); d.begin(frameID: "old")
        for p in rectangle { d.append(p) }
        XCTAssertThrowsError(try d.payload(name: "Entrance", coordinateSpace: "arkit-world", currentFrameID: "new"))
        d.invalidate()
        XCTAssertThrowsError(try d.payload(name: "Entrance", coordinateSpace: "arkit-world", currentFrameID: nil))
    }
    func testPolygonBoundsAndCrossings() throws {
        XCTAssertNoThrow(try Polygon.validate(rectangle))
        XCTAssertNoThrow(try Polygon.validate(rectangle.reversed()))
        XCTAssertThrowsError(try Polygon.validate([rectangle[0],rectangle[2],rectangle[1],rectangle[3]]))
        XCTAssertThrowsError(try Polygon.validate([rectangle[0],rectangle[1],rectangle[1]]))
        XCTAssertThrowsError(try Polygon.validate([Vertex(x: .nan,z: 0)] + rectangle))
        XCTAssertThrowsError(try Polygon.validate([Vertex(x: .infinity,z: 0)] + rectangle))
        XCTAssertThrowsError(try Polygon.validate([Vertex(x: 0,z: 0),Vertex(x: 1,z: 0),Vertex(x: 2,z: 0)]))
        XCTAssertThrowsError(try Polygon.validate(Array(repeating: rectangle[0], count: 17)))
    }
    func testPrivateEndpointsOnlyAndExplicitPlaintextConsent() throws {
        XCTAssertEqual(try ServerEndpoint("http://localhost:8821").origin.absoluteString, "http://127.0.0.1:8821")
        XCTAssertNoThrow(try ServerEndpoint("https://192.168.1.4:8765"))
        XCTAssertNoThrow(try ServerEndpoint("http://10.1.2.3:8765", approvePlaintextLAN: true))
        for url in ["http://192.168.1.4", "https://example.com", "https://8.8.8.8", "https://169.254.1.2", "http://0.0.0.0", "http://127.1", "http://2130706433", "http://127.0.0.01", "http://127.0.0.1@8.8.8.8", "http://127.0.0.1/a", "http://127.0.0.1?token=x", "http://127.0.0.1#x", "ftp://127.0.0.1", "http://127.0.0.1:99999", "http://127.0.0.1%2f.example.com", "http://127.0.0.1\\@example.com"] {
            XCTAssertThrowsError(try ServerEndpoint(url), url)
        }
    }
    func testTokenShape() {
        XCTAssertTrue(ServerEndpoint.validToken(String(repeating: "a", count: 43)))
        XCTAssertFalse(ServerEndpoint.validToken("secret\n"))
        XCTAssertFalse(ServerEndpoint.validToken(String(repeating: "a", count: 129)))
    }
}
