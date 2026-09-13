import Foundation
import SwiftUI
import ThresholdDomain

@MainActor final class InstrumentStore: ObservableObject {
    @Published private(set) var state: BackendState?
    @Published private(set) var pending = false
    @Published private(set) var refreshing = false
    @Published private(set) var paired = false
    @Published private(set) var fault: String?
    @Published private(set) var commandError: String?
    @Published private(set) var notice = "Connect your private server to read the shared alarm."
    @Published private(set) var lastResponse: Date?
    @Published var foreground = true
    @Published var draftDirty = false
    @Published var endpointText = UserDefaults.standard.string(forKey: "threshold.serverOrigin") ?? "http://127.0.0.1:8765"
    private var client: (any BackendTransport)?
    private let clientFactory: @MainActor (ServerEndpoint, String) -> any BackendTransport
    private let automaticallyPoll: Bool
    private var polling: Task<Void, Never>?
    private var epoch = UUID()
    private var commandRevision = 0
    private var refreshOrdering = RefreshOrdering()
    let sound: AlarmSound
    @Published private var observationClock = Date()
    private var clock: Timer?
    init(clientFactory: (@MainActor (ServerEndpoint, String) -> any BackendTransport)? = nil, automaticallyPoll: Bool = true, sound: AlarmSound? = nil, clockEnabled: Bool = true) {
        self.clientFactory = clientFactory ?? { BackendClient(endpoint: $0, token: $1) }; self.automaticallyPoll = automaticallyPoll
        self.sound = sound ?? AlarmSound()
        guard clockEnabled else { return }
        clock = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.observationClock = Date()
                self.sound.observe(eventID: self.state?.activeEventId, alarm: self.state?.alarmState == .alarm, foreground: self.foreground)
            }
        }
    }
    var fresh: Bool { paired && foreground && fault == nil && lastResponse.map { Date().timeIntervalSince($0) < 3.5 } == true }
    var healthy: Bool { fresh && state?.health.status == "healthy" && state?.health.sampleAgeS.map { $0 <= 3 && $0 >= 0 } == true }
    var healthLabel: String { !paired ? "NOT CONNECTED" : !fresh ? "STATE UNAVAILABLE" : healthy ? "STREAM HEALTHY" : "INPUT \(state?.health.status.uppercased() ?? "UNKNOWN")" }
    func allowed(_ action: ControlAction) -> Bool {
        #if DEBUG && targetEnvironment(simulator)
        if let expected = ProcessInfo.processInfo.environment["THRESHOLD_EXPECT_SESSION"] {
            guard state?.sessionId == expected, state?.sourceMode == .test, state?.calls.enabled == false, state?.calls.dryRun == true else { return false }
        }
        #endif
        return ControlGate.allows(action, state: state, fresh: fresh, busy: pending, draftDirty: draftDirty)
    }
    func dismissCommandError() { commandError = nil }

    func connect(token: String, approveLAN: Bool) async {
        guard !pending else { return }
        do {
            let endpoint = try ServerEndpoint(endpointText, approvePlaintextLAN: approveLAN)
            guard ServerEndpoint.validToken(token) else { throw DomainError.invalid("Use the 32–128 character server token. It is held in memory only.") }
            disconnect()
            pending = true; notice = "Authenticating private server…"; fault = nil
            let id = epoch
            let next = clientFactory(endpoint, token); client = next
            let snapshot = try await next.state()
            guard epoch == id else { return }
            #if DEBUG
            if let required = ProcessInfo.processInfo.environment["THRESHOLD_EXPECT_SESSION"] {
                guard snapshot.sessionId == required, snapshot.sourceMode == .test, !snapshot.calls.enabled, snapshot.calls.dryRun else { throw DomainError.invalid("Integration server identity or TEST/call policy mismatch.") }
            }
            #endif
            paired = true; endpointText = endpoint.origin.absoluteString
            UserDefaults.standard.set(endpointText, forKey: "threshold.serverOrigin")
            apply(snapshot); notice = "Connected. Backend state is authoritative."; pending = false
            if automaticallyPoll { startPolling() }
        } catch {
            let failure = error.localizedDescription
            disconnect(); fault = failure; notice = "Connection failed. Nothing was armed by this attempt."
        }
    }
    func disconnect() {
        epoch = UUID(); polling?.cancel(); polling = nil
        client?.close(); client = nil; paired = false; pending = false; refreshing = false; state = nil; lastResponse = nil
        fault = nil; commandError = nil; sound.stop(); notice = "Disconnected locally. The backend may still be armed."
    }
    func lifecycle(active: Bool) {
        foreground = active
        if !active { sound.stop(); notice = "App inactive: state and sound are not monitored here. The backend remains independent." }
    }
    private func startPolling() {
        polling?.cancel()
        polling = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if foreground && !pending { await refresh() }
                try? await Task.sleep(nanoseconds: 650_000_000)
            }
        }
    }
    func refresh() async {
        guard let client, paired, !pending, !refreshing else { return }
        refreshing = true
        let id = epoch, revision = commandRevision, refreshTicket = refreshOrdering.issue()
        defer { if epoch == id { refreshing = false } }
        do {
            let snapshot = try await client.state()
            guard epoch == id, revision == commandRevision, !pending, refreshOrdering.accepts(refreshTicket) else { return }
            apply(snapshot)
        } catch {
            guard epoch == id, revision == commandRevision, refreshOrdering.accepts(refreshTicket) else { return }
            fault = error.localizedDescription
            sound.observe(eventID: state?.activeEventId, alarm: state?.alarmState == .alarm, foreground: foreground)
        }
    }
    private func apply(_ snapshot: BackendState) {
        if let old = state, old.sessionId != snapshot.sessionId { notice = "Server restarted. Read the new state; saved server geometry may be gone." }
        state = snapshot; lastResponse = Date(); fault = nil
        sound.observe(eventID: snapshot.activeEventId, alarm: snapshot.alarmState == .alarm, foreground: foreground)
    }
    func command(_ action: ControlAction, extra: [String:Any] = [:]) async {
        guard allowed(action), let client else { notice = "Control unavailable. Check current state and draft."; return }
        pending = true; commandRevision += 1; let id = epoch
        commandError = nil; notice = "Sending \(action.rawValue)…"
        do {
            let snapshot = try await client.control(action: action, extra: extra)
            guard epoch == id else { return }
            apply(snapshot); notice = "Backend confirmed \(action.rawValue.replacingOccurrences(of: "_", with: " "))."
        } catch { guard epoch == id else { return }; fault = error.localizedDescription; commandError = error.localizedDescription; notice = "Command not confirmed. Read current state before retrying." }
        pending = false
    }
    func publish(_ zone: ZonePayload) async -> Bool {
        guard allowed(.setZone), let client else { notice = "Disarm and connect before publishing geometry."; return false }
        pending = true; commandError = nil; commandRevision += 1; let id = epoch
        defer { if epoch == id { pending = false } }
        do {
            let snapshot = try await client.publish(zone)
            guard epoch == id else { return false }
            apply(snapshot); notice = "Outline saved. Geometry is not coverage or a person location. Calibrate again."
            return true
        } catch { guard epoch == id else { return false }; fault = error.localizedDescription; commandError = error.localizedDescription; notice = "Outline was not confirmed saved."; return false }
    }
}
