import AVFoundation
import Combine
import Foundation
import ImageIO
import ThresholdDomain
import Vision

private func cameraHostTime() -> TimeInterval {
    CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))
}

struct CameraPersonFrame: Sendable {
    let boxes: [CGRect]
    let capturedAt: TimeInterval
}
enum CameraCaptureStart {
    case running, cancelled, unavailable(String)
}
typealias CameraFrameHandler = @MainActor (UUID, CameraPersonFrame) -> Void
typealias CameraFailureHandler = @MainActor (UUID, String) -> Void

/// Test seam at the actual adapter boundary. The app always uses RearCameraDriver.
/// Rehearsal does not implement this protocol and cannot submit camera evidence.
@MainActor protocol CameraCaptureDriving: AnyObject {
    var session: AVCaptureSession { get }
    func hasRearCamera() -> Bool
    func authorizationStatus() -> AVAuthorizationStatus
    func requestPermission() async -> Bool
    func start(generation: UUID, frame: @escaping CameraFrameHandler,
               failure: @escaping CameraFailureHandler) async -> CameraCaptureStart
    func stop()
}

@MainActor final class CameraEntryMonitor: ObservableObject {
    let session: AVCaptureSession
    let zone = CameraEntryRule.zone
    @Published private(set) var inputMode: CameraInputMode = .camera
    @Published private(set) var phase: CameraWatchPhase = .stopped
    @Published private(set) var statusMessage = "Start the rear camera, or choose a labeled rehearsal."
    @Published private(set) var personVisible = false
    @Published private(set) var personBoxes: [CGRect] = []
    @Published private(set) var events: [CameraEntryEvent] = []
    @Published private(set) var isRunning = false

    private let driver: any CameraCaptureDriving
    private let now: () -> TimeInterval
    private let wallClock: () -> Date
    private let automaticallyTick: Bool
    private var rule = CameraEntryRule()
    private var generation = UUID()
    private var foreground = true
    private var timer: Timer?
    private var startedAt: TimeInterval?
    private var minimumFrameTime: TimeInterval = 0
    private var rehearsalPerson = false
    static let journalLimit = 20

    init(driver: (any CameraCaptureDriving)? = nil,
         now: @escaping () -> TimeInterval = cameraHostTime,
         wallClock: @escaping () -> Date = Date.init,
         automaticallyTick: Bool = true) {
        let capture = driver ?? RearCameraDriver()
        self.driver = capture; session = capture.session
        self.now = now; self.wallClock = wallClock; self.automaticallyTick = automaticallyTick
    }

    var canArm: Bool { foreground && isRunning && rule.canArm(at: now()) }

    func startCamera() async {
        guard foreground else { statusMessage = "Return to the active demo, then tap Start camera."; return }
        guard phase != .armed, phase != .alert else {
            statusMessage = "Disarm or acknowledge the current run before changing input. Stop is always safe."; return
        }
        guard !(inputMode == .camera && (isRunning || phase == .starting)) else { return }
        invalidateCapture()
        inputMode = .camera; rule.stop(); phase = .starting
        statusMessage = "Requesting the rear camera. No microphone, recording, or uploads."
        let ticket = generation
        guard driver.hasRearCamera() else {
            fail("No supported rear camera is available. Choose Rehearsal explicitly; it is not camera evidence."); return
        }
        startedAt = now(); installTimer(ticket: ticket)
        let allowed: Bool
        switch driver.authorizationStatus() {
        case .authorized: allowed = true
        case .notDetermined: allowed = await driver.requestPermission()
        default: allowed = false
        }
        guard ticket == generation, foreground else { return }
        guard !Task.isCancelled else { stop(); return }
        guard allowed else {
            fail("Camera permission is unavailable. Allow Camera in Settings, then tap Start again. Rehearsal needs no permission.")
            return
        }
        let result = await driver.start(generation: ticket, frame: { [weak self] ticket, sample in
            self?.receiveCameraFrame(sample, ticket: ticket)
        }, failure: { [weak self] ticket, message in
            guard let self, self.generation == ticket, self.inputMode == .camera else { return }
            self.fail(message)
        })
        guard ticket == generation, foreground else { return }
        guard !Task.isCancelled else { stop(); return }
        switch result {
        case .running:
            rule.begin(); isRunning = true
            startedAt = now(); minimumFrameTime = now()
            synchronizePresentation()
        case .cancelled: fail("Camera start was cancelled. Tap Start again when ready.")
        case .unavailable(let message): fail(message)
        }
    }

    /// A shipped, explicit demonstration, never an automatic camera/network fallback.
    func startRehearsal() {
        guard foreground else { statusMessage = "Return to the active demo, then choose Rehearsal."; return }
        guard phase != .armed, phase != .alert else {
            statusMessage = "Disarm or acknowledge the current run before resetting rehearsal. Stop is always safe."; return
        }
        invalidateCapture()
        inputMode = .rehearsal; rule.begin(); isRunning = true
        startedAt = now(); minimumFrameTime = now(); rehearsalPerson = false
        synchronizePresentation(); installTimer(ticket: generation)
    }

    func rehearseEntry() {
        guard inputMode == .rehearsal, isRunning, foreground else { return }
        if rule.expire(at: now()) {
            fail("Rehearsal frames became stale. Start rehearsal again explicitly."); return
        }
        guard phase == .armed else {
            if phase != .alert { statusMessage = "Rehearsal only: wait for clear, tap Arm, then rehearse one entry." }
            return
        }
        rehearsalPerson = true
        statusMessage = "REHEARSAL · synthetic person entering; waiting for sustained presence."
    }

    func arm() {
        guard foreground, isRunning else { return }
        let time = now()
        guard rule.arm(at: time) else {
            if rule.phase == .unavailable { fail("Frames became stale. Camera authority is disarmed.") }
            else { synchronizePresentation() }
            return
        }
        // A frame captured before the user's command cannot count as an armed observation.
        minimumFrameTime = time
        synchronizePresentation()
    }

    func disarm() {
        if phase == .starting { stop(); return }
        guard isRunning else { return }
        rule.disarm(); minimumFrameTime = now(); startedAt = now()
        rehearsalPerson = false; clearPerson()
        synchronizePresentation()
    }
    func acknowledge() {
        guard phase == .alert else { return }
        disarm() // Journal remains. Fresh clear frames AND a new Arm tap are required.
    }
    func stop() {
        invalidateCapture(); rule.stop(); phase = .stopped
        statusMessage = "Stopped and disarmed. Start again explicitly; the local journal is kept."
    }
    func lifecycle(active: Bool) {
        let wasForeground = foreground
        foreground = active
        if !active {
            stop()
            statusMessage = "Demo inactive: camera stopped and disarmed. Tap Start again, including after granting permission."
        } else if !wasForeground && phase == .stopped {
            statusMessage = "Camera is off and disarmed. Start camera or choose Rehearsal."
        }
        // Becoming active never restarts capture and never rearms.
    }

    /// The same watchdog/rehearsal clock is called by the live timer and deterministic adapter tests.
    func evaluateClock() {
        guard foreground else { return }
        let time = now()
        guard time.isFinite else { fail("Input clock is invalid. Start again explicitly."); return }
        if phase == .starting {
            if let start = startedAt, time - start > 8 { fail("Camera start timed out. Start again explicitly.") }
            return
        }
        guard isRunning else { return }
        if inputMode == .rehearsal {
            let boxes = rehearsalPerson ? [CGRect(x: 0.4, y: 0.2, width: 0.2, height: 0.6)] : []
            receive(boxes: boxes, capturedAt: time, source: .rehearsal)
            return
        }
        if rule.expire(at: time) {
            fail("Camera frames are stale. Capture stopped and authority is disarmed.")
        } else if rule.lastObservationAt == nil, let start = startedAt, time - start > 3 {
            fail("No fresh camera frames arrived. Capture stopped and authority is disarmed.")
        }
    }

    private func receiveCameraFrame(_ sample: CameraPersonFrame, ticket: UUID) {
        guard ticket == generation, inputMode == .camera, foreground, isRunning else { return }
        receive(boxes: sample.boxes, capturedAt: sample.capturedAt, source: .camera)
    }
    private func receive(boxes: [CGRect], capturedAt: TimeInterval, source: CameraInputMode) {
        guard source == inputMode, isRunning, foreground else { return }
        guard !(capturedAt < minimumFrameTime) else { return }
        let entered = rule.observe(boxes: boxes, capturedAt: capturedAt, now: now())
        guard rule.phase != .unavailable else {
            fail("Invalid, late, or stale camera observations. Input stopped and disarmed; start again explicitly.")
            return
        }
        personBoxes = Array(boxes.prefix(8)); personVisible = !boxes.isEmpty
        if entered {
            events.insert(CameraEntryEvent(id: UUID().uuidString, occurredAt: wallClock(), source: source), at: 0)
            if events.count > Self.journalLimit { events.removeLast(events.count - Self.journalLimit) }
        }
        synchronizePresentation()
    }
    private func synchronizePresentation() {
        phase = rule.phase
        let prefix = inputMode == .rehearsal ? "REHEARSAL · synthetic frames · " : "CAMERA · "
        switch phase {
        case .clearing:
            statusMessage = prefix + (rule.multiplePeople ? "One-person demo only. Disarmed; clear all people from view." : "Disarmed. Keep the whole view empty for a fresh clear baseline.")
        case .ready: statusMessage = prefix + "Clear baseline ready. Tap Arm; nothing arms automatically."
        case .armed: statusMessage = prefix + "Armed for one person’s box centre entering the doorway region."
        case .alert: statusMessage = prefix + "Entry latched. Acknowledge, clear the view, then explicitly Arm again."
        default: break
        }
    }
    private func clearPerson() { personVisible = false; personBoxes = [] }
    private func invalidateCapture() {
        generation = UUID(); timer?.invalidate(); timer = nil
        driver.stop(); isRunning = false; startedAt = nil
        rehearsalPerson = false; clearPerson()
    }
    private func fail(_ message: String) {
        invalidateCapture(); rule.fail(.captureUnavailable); phase = .unavailable
        statusMessage = message + (events.isEmpty ? "" : " Previous events remain in the local journal.")
    }
    private func installTimer(ticket: UUID) {
        guard automaticallyTick else { return }
        let clock = Timer(timeInterval: 0.2, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.generation == ticket else { return }
                self.evaluateClock()
            }
        }
        timer = clock
        RunLoop.main.add(clock, forMode: .common)
    }
    deinit { timer?.invalidate() }
}

@MainActor private final class RearCameraDriver: CameraCaptureDriving {
    private let worker = CameraCaptureWorker()
    var session: AVCaptureSession { worker.session }
    func hasRearCamera() -> Bool {
        AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) != nil
    }
    func authorizationStatus() -> AVAuthorizationStatus { AVCaptureDevice.authorizationStatus(for: .video) }
    func requestPermission() async -> Bool { await AVCaptureDevice.requestAccess(for: .video) }
    func start(generation: UUID, frame: @escaping CameraFrameHandler,
               failure: @escaping CameraFailureHandler) async -> CameraCaptureStart {
        await worker.start(generation: generation, frame: frame, failure: failure)
    }
    func stop() { worker.stop() }
    deinit { worker.stop() }
}

/// A synchronous cancellation gate closes before any queued stop can run.
private final class CameraRunGate: @unchecked Sendable {
    private let lock = NSLock()
    private var generation: UUID?
    func set(_ value: UUID?) { lock.lock(); generation = value; lock.unlock() }
    func accepts(_ value: UUID) -> Bool {
        lock.lock(); defer { lock.unlock() }; return generation == value
    }
    func cancel(_ value: UUID) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard generation == value else { return false }
        generation = nil; return true
    }
}

/// Every session mutation and Vision request runs on this one serial background queue.
private final class CameraCaptureWorker: @unchecked Sendable {
    let session = AVCaptureSession()
    private let queue = DispatchQueue(label: "local.threshold.camera", qos: .userInitiated)
    private let gate = CameraRunGate()
    private let output = AVCaptureVideoDataOutput()
    private var configured = false
    private var runningGeneration: UUID?
    private var delegate: CameraVisionDelegate?
    private var observers: [NSObjectProtocol] = []

    @MainActor func start(generation: UUID, frame: @escaping CameraFrameHandler,
                          failure: @escaping CameraFailureHandler) async -> CameraCaptureStart {
        gate.set(generation)
        return await withCheckedContinuation { continuation in
            queue.async { [self] in
                guard gate.accepts(generation) else { continuation.resume(returning: .cancelled); return }
                stopSession()
                do {
                    try configure()
                    guard gate.accepts(generation) else { continuation.resume(returning: .cancelled); return }
                    let vision = CameraVisionDelegate(generation: generation, session: session, gate: gate,
                                                      frame: frame, failure: { [weak self] message in
                        self?.reportFailure(generation: generation, message: message, callback: failure)
                    })
                    delegate = vision; runningGeneration = generation
                    output.setSampleBufferDelegate(vision, queue: queue)
                    observeFailures(generation: generation, callback: failure)
                    guard gate.accepts(generation) else {
                        stopSession(); continuation.resume(returning: .cancelled); return
                    }
                    session.startRunning()
                    // startRunning is blocking. Cancellation during it must stop the resulting session.
                    guard gate.accepts(generation) else {
                        stopSession(); continuation.resume(returning: .cancelled); return
                    }
                    guard session.isRunning, !session.isInterrupted else {
                        stopSession(); continuation.resume(returning: .unavailable("Rear camera did not start. Tap Start again; there is no automatic retry.")); return
                    }
                    continuation.resume(returning: .running)
                } catch {
                    stopSession()
                    continuation.resume(returning: .unavailable("Rear camera unavailable: \(error.localizedDescription)"))
                }
            }
        }
    }
    func stop() {
        gate.set(nil)
        queue.async { [self] in
            // Do not let an older queued cleanup stop a newer explicit run.
            if let active = runningGeneration, gate.accepts(active) { return }
            stopSession()
        }
    }
    private func stopSession() {
        observers.forEach(NotificationCenter.default.removeObserver); observers.removeAll()
        output.setSampleBufferDelegate(nil, queue: nil)
        delegate = nil; runningGeneration = nil
        if session.isRunning { session.stopRunning() }
    }
    private func configure() throws {
        guard !configured else { return }
        session.beginConfiguration()
        defer { session.commitConfiguration() }
        session.inputs.forEach(session.removeInput)
        session.outputs.forEach(session.removeOutput)
        guard session.canSetSessionPreset(.vga640x480),
              let device = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back) else {
            throw CameraSetupError("A supported rear camera is required. Rehearsal is available separately.")
        }
        session.sessionPreset = .vga640x480
        let input = try AVCaptureDeviceInput(device: device)
        guard session.canAddInput(input) else { throw CameraSetupError("Rear video input could not be attached.") }
        session.addInput(input)
        // Video only. No audio input, photo output, movie output, file write, or upload exists here.
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange]
        guard session.canAddOutput(output) else { throw CameraSetupError("Video analysis output could not be attached.") }
        session.addOutput(output)
        guard let connection = output.connection(with: .video) else { throw CameraSetupError("Video connection is unavailable.") }
        if connection.isVideoRotationAngleSupported(90) {
            connection.videoRotationAngle = 90 // Physical 480x640 portrait pixels.
        } else if connection.isVideoOrientationSupported {
            connection.videoOrientation = .portrait
        } else { throw CameraSetupError("Portrait capture is unavailable.") }
        if connection.isVideoMirroringSupported {
            connection.automaticallyAdjustsVideoMirroring = false; connection.isVideoMirrored = false
        }
        // Keep capture at 15 fps where the selected format supports it; Vision is limited to ~5 fps.
        if device.activeFormat.videoSupportedFrameRateRanges.contains(where: { $0.minFrameRate <= 15 && $0.maxFrameRate >= 15 }) {
            try device.lockForConfiguration()
            device.activeVideoMinFrameDuration = CMTime(value: 1, timescale: 15)
            device.activeVideoMaxFrameDuration = CMTime(value: 1, timescale: 15)
            device.unlockForConfiguration()
        }
        configured = true
    }
    private func observeFailures(generation: UUID, callback: @escaping CameraFailureHandler) {
        let notices: [(Notification.Name, String)] = [
            (AVCaptureSession.wasInterruptedNotification, "Camera interrupted. Stopped and disarmed; tap Start again."),
            (AVCaptureSession.runtimeErrorNotification, "Camera runtime error. Stopped and disarmed; tap Start again."),
            (AVCaptureSession.didStopRunningNotification, "Camera stopped unexpectedly. Disarmed; tap Start again.")
        ]
        for (name, message) in notices {
            observers.append(NotificationCenter.default.addObserver(forName: name, object: session, queue: nil) { [weak self] _ in
                self?.reportFailure(generation: generation, message: message, callback: callback)
            })
        }
        // No interruption-ended observer: resuming is always an explicit user action.
    }
    private func reportFailure(generation: UUID, message: String, callback: @escaping CameraFailureHandler) {
        guard gate.cancel(generation) else { return }
        queue.async { [self] in
            if runningGeneration == generation { stopSession() }
        }
        Task { @MainActor in callback(generation, message) }
    }
}

private struct CameraSetupError: LocalizedError {
    let detail: String
    init(_ detail: String) { self.detail = detail }
    var errorDescription: String? { detail }
}

/// One per run: a delayed callback never acquires a newer generation's identity.
private final class CameraVisionDelegate: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    private let generation: UUID
    private let session: AVCaptureSession
    private let gate: CameraRunGate
    private let failure: (String) -> Void
    private let mailbox: CameraFrameMailbox
    private let request = VNDetectHumanRectanglesRequest()
    private var lastAnalysisAt: TimeInterval = -.infinity

    init(generation: UUID, session: AVCaptureSession, gate: CameraRunGate,
         frame: @escaping CameraFrameHandler, failure: @escaping (String) -> Void) {
        self.generation = generation; self.session = session; self.gate = gate; self.failure = failure
        mailbox = CameraFrameMailbox(generation: generation, gate: gate, callback: frame)
        super.init()
        request.upperBodyOnly = false
        request.preferBackgroundProcessing = true
    }
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard gate.accepts(generation) else { return }
        let now = cameraHostTime()
        guard now - lastAnalysisAt >= 0.2 else { return }
        lastAnalysisAt = now
        guard let pixels = CMSampleBufferGetImageBuffer(sampleBuffer),
              CVPixelBufferGetWidth(pixels) == 480, CVPixelBufferGetHeight(pixels) == 640,
              let clock = session.synchronizationClock else {
            failure("Portrait camera frame or capture clock unavailable. Stopped and disarmed."); return
        }
        let timestamp = CMSyncConvertTime(CMSampleBufferGetPresentationTimeStamp(sampleBuffer), from: clock, to: CMClockGetHostTimeClock())
        let capturedAt = CMTimeGetSeconds(timestamp)
        guard capturedAt.isFinite, capturedAt >= 0, now >= capturedAt,
              now - capturedAt <= CameraEntryRule.maximumFrameAge else {
            failure("Camera delivered a stale frame. Stopped and disarmed."); return
        }
        do {
            // The connection already rotated pixels; .right here would rotate twice.
            try VNImageRequestHandler(cvPixelBuffer: pixels, orientation: .up, options: [:]).perform([request])
            guard gate.accepts(generation) else { return }
            guard let observations = request.results else { failure("Vision returned no valid result. Stopped and disarmed."); return }
            var boxes: [CGRect] = []
            for person in observations {
                guard person.confidence.isFinite, (0...1).contains(person.confidence) else {
                    failure("Vision confidence is invalid. Stopped and disarmed."); return
                }
                guard person.confidence >= 0.55 else { continue }
                guard let box = CameraEntryRule.displayBox(fromVision: person.boundingBox) else {
                    failure("Vision returned invalid person geometry. Stopped and disarmed."); return
                }
                boxes.append(box)
                if boxes.count == 8 { break } // More than one already disarms this one-person demo.
            }
            mailbox.submit(CameraPersonFrame(boxes: boxes, capturedAt: capturedAt))
        } catch {
            failure("Person analysis failed. Stopped and disarmed; tap Start again.")
        }
    }
}

/// Coalesce main-actor delivery too: at most one pending frame, never a stale frame backlog.
private final class CameraFrameMailbox: @unchecked Sendable {
    private let lock = NSLock()
    private var latest: CameraPersonFrame?
    private var scheduled = false
    private let generation: UUID
    private let gate: CameraRunGate
    private let callback: CameraFrameHandler
    init(generation: UUID, gate: CameraRunGate, callback: @escaping CameraFrameHandler) {
        self.generation = generation; self.gate = gate; self.callback = callback
    }
    func submit(_ frame: CameraPersonFrame) {
        lock.lock(); latest = frame
        let needsTask = !scheduled; scheduled = true; lock.unlock()
        if needsTask { Task { @MainActor [weak self] in self?.deliverLatest() } }
    }
    @MainActor private func deliverLatest() {
        lock.lock(); let frame = latest; latest = nil; scheduled = false; lock.unlock()
        guard gate.accepts(generation), let frame else { return }
        callback(generation, frame)
    }
}
