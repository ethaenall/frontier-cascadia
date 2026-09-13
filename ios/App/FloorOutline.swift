import SwiftUI
import ARKit
import SceneKit
import AVFoundation
import ThresholdDomain

@MainActor final class FloorOutline: NSObject, ObservableObject {
    enum Mode { case idle, ar, testPlan }
    @Published private(set) var mode: Mode = .idle
    @Published private(set) var draft = FrameDraft()
    @Published private(set) var message = "Outline the floor. The camera stays on this iPhone."
    @Published private(set) var trackingReady = false
    @Published private(set) var floorFound = false
    @Published private(set) var invalidated = false
    @Published private(set) var dirty = false
    @Published var name = "Entrance boundary" { didSet { if oldValue != name { publication.edited(); dirty = true } } }
    weak var view: ARSCNView?
    private var frameResources = WorldFrameResources<SCNNode>()
    private var worldFrame: String? { frameResources.frameID }
    private var targetPoint: SIMD3<Float>? {
        get { frameResources.hit }
        set { frameResources.hit = newValue }
    }
    private var updateAt = 0.0
    private let geometryRoot = SCNNode()
    private var frameDelegate: FloorFrameDelegate?
    private let runCameraSession: @MainActor (ARSCNView, ARWorldTrackingConfiguration) -> Void
    init(runCameraSession: (@MainActor (ARSCNView, ARWorldTrackingConfiguration) -> Void)? = nil) {
        self.runCameraSession = runCameraSession ?? { $0.session.run($1, options: [.resetTracking, .removeExistingAnchors]) }
        super.init()
    }
    private var publication = DraftPublication()
    @Published private(set) var publishPending = false
    var supported: Bool { ARWorldTrackingConfiguration.isSupported }
    var coordinateSpace: String { mode == .testPlan ? "test-plan" : "arkit-world" }
    var frameLabel: String { draft.frameID.map { String($0.prefix(8)).uppercased() } ?? "NO ACTIVE FRAME" }
    var canPublish: Bool { !invalidated && draft.vertices.count >= 3 && (mode == .testPlan || trackingReady) }
    func attach(_ view: ARSCNView) {
        self.view?.session.pause()
        self.view?.session.delegate = nil
        frameResources.reset(frameID: nil, discard: { $0.removeFromParentNode() })
        geometryRoot.removeFromParentNode()
        self.view = view
        view.scene = SCNScene(); view.scene.rootNode.addChildNode(geometryRoot)
        view.preferredFramesPerSecond = 30
        view.automaticallyUpdatesLighting = false
        view.isAccessibilityElement = true; view.accessibilityLabel = "Local ARKit floor view. Camera frames are not uploaded. Use Add floor corner at the center reticle."
        if mode == .ar { run() }
    }
    func detach(_ removedView: ARSCNView) {
        removedView.session.pause(); removedView.session.delegate = nil
        guard view === removedView else { return }
        view = nil
        if mode == .ar { stop(reason: "AR view closed. Local frame and render geometry invalidated. Start AR again to capture a new floor.") }
    }
    func startAR() async {
        guard !publishPending else { return }
        stop(reason: "Starting a new AR frame. Previous local vertices discarded.")
        guard supported else {
            message = "AR unavailable on this device or simulator. TEST plan is a labelled geometry editor, not a scan."; return
        }
        let permission: Bool
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized: permission = true
        case .notDetermined: permission = await AVCaptureDevice.requestAccess(for: .video)
        default: permission = false
        }
        guard permission else { invalidated = true; message = "Camera denied. No AR frame is valid. Allow Camera in iOS Settings, then retry."; return }
        mode = .ar; invalidated = false; message = "Move slowly over the floor. Find a horizontal plane; avoid tables."
        run()
    }
    private func run() {
        guard let view, mode == .ar else { return }
        frameResources.reset(frameID: UUID().uuidString, discard: { $0.removeFromParentNode() })
        draft.invalidate(); publication.edited(); dirty = false
        trackingReady = false; floorFound = false; updateAt = 0
        let delegate = FloorFrameDelegate(owner: self, frameID: worldFrame!)
        frameDelegate = delegate; view.session.delegate = delegate
        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        config.environmentTexturing = .none
        runCameraSession(view, config)
    }
    func startTestPlan() {
        guard !publishPending else { return }
        stop(reason: "New TEST plan. Units are unmeasured; no camera scan.")
        mode = .testPlan; invalidated = false; dirty = true
        frameResources.reset(frameID: UUID().uuidString, discard: { $0.removeFromParentNode() }); draft.begin(frameID: worldFrame!)
        message = "TEST plan only. Tap 3–16 perimeter corners in order. Units are unmeasured."
    }
    func addTest(_ point: Vertex) {
        guard !publishPending, mode == .testPlan, !invalidated else { return }
        guard draft.vertices.count < 16 else { message = "Maximum 16 corners. Undo or start again."; return }
        draft.append(point); publication.edited(); dirty = true
    }
    func exampleTestRectangle() {
        guard !publishPending else { return }
        startTestPlan()
        for point in [Vertex(x: 2,z: 2),Vertex(x: 8,z: 2),Vertex(x: 8,z: 7),Vertex(x: 2,z: 7)] { addTest(point) }
        message = "Four-corner TEST example. Synthetic geometry, not a measured floor."
    }
    func addFloorCorner() {
        guard !publishPending, mode == .ar, trackingReady, !invalidated, let p = targetPoint else { message = "No tracked floor at the reticle. Move slowly and try again."; return }
        guard draft.vertices.count < 16 else { message = "Maximum 16 corners. Undo or start again."; return }
        if let floor = draft.floorY, abs(Double(p.y)-floor) > 0.12 { message = "Height differs from the first corner. Aim at the same floor, not a raised surface."; return }
        draft.append(Vertex(x: Double(p.x), z: Double(p.z)), floorY: Double(p.y)); publication.edited(); dirty = true
        message = "Corner \(draft.vertices.count) marked. Follow the perimeter; camera pose is not a person location."
        drawGeometry()
    }
    func undo() { guard !publishPending else { return }; draft.undo(); publication.edited(); dirty = true; drawGeometry() }
    func beginPublication() throws -> (ZonePayload, PublishTicket) {
        guard !publishPending else { throw DomainError.invalid("An outline publication is already pending.") }
        let submitted = try payload()
        let ticket = publication.capture(frameID: worldFrame, payload: try submitted.encoded())
        publishPending = true
        return (submitted, ticket)
    }
    @discardableResult func finishPublication(_ ticket: PublishTicket, confirmed: Bool) -> Bool {
        defer { publishPending = false }
        guard confirmed else { message = "Outline publication was not confirmed. Local draft kept."; return false }
        guard let current = try? payload().encoded(), publication.accepts(ticket, frameID: worldFrame, payload: current) else {
            message = "Backend saved the submitted outline, but the local frame or draft changed. Current local work was not marked saved."
            return false
        }
        dirty = false
        if mode == .testPlan { mode = .idle }
        message = "Saved to shared backend. This is geometry, not RF coverage."
        return true
    }
    func publish(to store: InstrumentStore) async throws -> Bool {
        let (submitted, ticket) = try beginPublication()
        let confirmed = await store.publish(submitted)
        let saved = finishPublication(ticket, confirmed: confirmed)
        store.draftDirty = dirty
        return saved
    }
    func payload() throws -> ZonePayload {
        guard canPublish else { throw DomainError.invalid("A valid current frame and 3–16 corners are required.") }
        return try draft.payload(name: name, coordinateSpace: coordinateSpace, currentFrameID: worldFrame)
    }
    func stop(reason: String = "Camera stopped. Local AR frame invalidated; saved backend geometry is unchanged.") {
        view?.session.pause(); view?.session.delegate = nil; frameDelegate = nil
        mode = .idle; trackingReady = false; floorFound = false; invalidated = true
        draft.invalidate(); publication.edited()
        frameResources.reset(frameID: nil, discard: { $0.removeFromParentNode() }); updateAt = 0
        dirty = false; message = reason
    }
    func invalidateTracking(_ reason: String) {
        guard mode == .ar else { return }
        stop(reason: reason + " Local outline invalidated. Retry starts a new frame; old corners cannot be restored.")
    }
    fileprivate func receiveTracking(_ tracking: ARCamera.TrackingState, timestamp: Double, frameID: String, sessionID: ObjectIdentifier) {
        guard worldFrame == frameID, let view, ObjectIdentifier(view.session) == sessionID else { return }
        updateTracking(tracking, timestamp: timestamp)
    }
    fileprivate func receiveFailure(_ reason: String, frameID: String) {
        guard worldFrame == frameID else { return }
        invalidateTracking(reason)
    }
    private func updateTracking(_ tracking: ARCamera.TrackingState, timestamp: Double) {
        guard mode == .ar else { return }
        switch tracking {
        case .normal:
            if !trackingReady {
                guard let worldFrame else { return }
                draft.begin(frameID: worldFrame); trackingReady = true
                message = "Tracking ready. Aim at the actual floor; tap Add floor corner."
            }
        case .limited, .notAvailable:
            if trackingReady { invalidateTracking("World tracking was lost.") }
            else { message = "Establishing AR tracking. Move slowly in good light. No floor has been captured." }
            return
        }
        guard timestamp-updateAt > 0.18, let view else { return }
        updateAt = timestamp
        let center = CGPoint(x: view.bounds.midX, y: view.bounds.midY)
        guard let query = view.raycastQuery(from: center, allowing: .existingPlaneGeometry, alignment: .horizontal),
              let hit = view.session.raycast(query).first else { floorFound = false; targetPoint = nil; return }
        let t = hit.worldTransform.columns.3
        targetPoint = SIMD3<Float>(t.x,t.y,t.z); floorFound = true
    }
    private func drawGeometry() {
        frameResources.clearRender(discard: { $0.removeFromParentNode() })
        guard mode == .ar, let y = draft.floorY else { return }
        let vertices = draft.vertices
        let color = UIColor(red: 0.82, green: 0.89, blue: 0.55, alpha: 1)
        for (i,p) in vertices.enumerated() {
            let sphere = SCNSphere(radius: 0.025); sphere.segmentCount = 12; sphere.firstMaterial?.diffuse.contents = color; sphere.firstMaterial?.lightingModel = .constant
            let dot = SCNNode(geometry: sphere); dot.position = SCNVector3(p.x,y+0.012,p.z); geometryRoot.addChildNode(dot); frameResources.add(dot)
            if i > 0 { line(vertices[i-1],p,y:y,color:color) }
        }
        if vertices.count >= 3 { line(vertices.last!,vertices[0],y:y,color:color) }
    }
    #if DEBUG
    func testBeginARWithoutCamera() { mode = .ar; invalidated = false; run() }
    func testPopulateFrameResources() {
        guard let worldFrame else { return }
        draft.begin(frameID: worldFrame)
        for p in [Vertex(x: 0,z: 0), Vertex(x: 2,z: 0), Vertex(x: 2,z: 2)] { draft.append(p, floorY: 0) }
        trackingReady = true; targetPoint = SIMD3<Float>(1,0,1); floorFound = true
        drawGeometry()
    }
    var testRenderCount: Int { geometryRoot.childNodes.count }
    var testCachedHit: SIMD3<Float>? { targetPoint }
    var testWorldFrame: String? { worldFrame }
    #endif
    private func line(_ a: Vertex, _ b: Vertex, y: Double, color: UIColor) {
        let distance = hypot(a.x-b.x,a.z-b.z)
        guard distance > 0 else { return }
        let cylinder = SCNCylinder(radius: 0.008, height: distance); cylinder.radialSegmentCount = 8
        cylinder.firstMaterial?.diffuse.contents = color; cylinder.firstMaterial?.lightingModel = .constant
        let node = SCNNode(geometry: cylinder)
        node.position = SCNVector3((a.x+b.x)/2,y+0.01,(a.z+b.z)/2)
        node.simdOrientation = simd_quatf(from: SIMD3<Float>(0,1,0), to: simd_normalize(SIMD3<Float>(Float(b.x-a.x),0,Float(b.z-a.z))))
        geometryRoot.addChildNode(node); frameResources.add(node)
    }
}
/// One immutable delegate identity per AR run. Queued callbacks keep that old identity.
private final class FloorFrameDelegate: NSObject, ARSessionDelegate {
    weak var owner: FloorOutline?
    let frameID: String
    init(owner: FloorOutline, frameID: String) { self.owner = owner; self.frameID = frameID }
    func session(_ session: ARSession, didUpdate frame: ARFrame) {
        let tracking = frame.camera.trackingState, time = frame.timestamp
        let id = frameID, sessionID = ObjectIdentifier(session)
        DispatchQueue.main.async { [weak owner] in owner?.receiveTracking(tracking, timestamp: time, frameID: id, sessionID: sessionID) }
    }
    func sessionWasInterrupted(_ session: ARSession) { fail("AR session interrupted.") }
    func session(_ session: ARSession, didFailWithError error: Error) { fail("AR session failed.") }
    func sessionShouldAttemptRelocalization(_ session: ARSession) -> Bool { false }
    private func fail(_ message: String) {
        let id = frameID
        DispatchQueue.main.async { [weak owner] in owner?.receiveFailure(message, frameID: id) }
    }
}
struct ARFloorView: UIViewRepresentable {
    @ObservedObject var outline: FloorOutline
    final class Coordinator {
        weak var outline: FloorOutline?
        init(_ outline: FloorOutline) { self.outline = outline }
    }
    func makeCoordinator() -> Coordinator { Coordinator(outline) }
    func makeUIView(context: Context) -> ARSCNView { let view = ARSCNView(); outline.attach(view); return view }
    func updateUIView(_ view: ARSCNView, context: Context) {}
    static func dismantleUIView(_ view: ARSCNView, coordinator: Coordinator) { coordinator.outline?.detach(view) }
}
