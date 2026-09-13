import XCTest
@testable import ThresholdDomain
final class FrameTests: XCTestCase {
    func testResetNeverReattachesOldVerticesToNewFrame() {
        var draft = FrameDraft()
        draft.begin(frameID: "frame-A")
        draft.append(Vertex(x: 1, z: 2))
        draft.invalidate()
        draft.begin(frameID: "frame-B")
        XCTAssertEqual(draft.frameID, "frame-B")
        XCTAssertTrue(draft.vertices.isEmpty, "Old AR coordinates must not attach to a new world origin")
    }
}
