import SwiftUI

@main struct ThresholdApp: App {
    @StateObject private var store: InstrumentStore = {
        #if DEBUG && targetEnvironment(simulator)
        if ProcessInfo.processInfo.arguments.contains("--ui-response-loss-fixture") {
            return InstrumentStore(clientFactory: { _,_ in ResponseLossFixture.shared }, automaticallyPoll: true)
        }
        #endif
        return InstrumentStore()
    }()
    @StateObject private var outline = FloorOutline()
    @StateObject private var camera = CameraEntryMonitor()
    @State private var showRadioLab = false
    @State private var confirmLeaveLab = false
    @Environment(\.scenePhase) private var phase
    private var directRadioLab: Bool {
        #if DEBUG && targetEnvironment(simulator)
        let arguments = ProcessInfo.processInfo.arguments
        return arguments.contains("--radio-lab") || arguments.contains("--integration") || arguments.contains("--ui-response-loss-fixture")
        #else
        return false
        #endif
    }
    var body: some Scene {
        WindowGroup {
            Group {
                if directRadioLab {
                    InstrumentView(store: store, outline: outline).preferredColorScheme(.dark)
                } else {
                    CameraDemoView(monitor: camera) { camera.stop(); showRadioLab = true }
                        .fullScreenCover(isPresented: $showRadioLab) {
                            NavigationStack {
                                InstrumentView(store: store, outline: outline)
                                    .toolbar {
                                        ToolbarItem(placement: .topBarLeading) {
                                            Button("Back to camera demo") { confirmLeaveLab = true }
                                                .accessibilityIdentifier("radio-lab-close")
                                        }
                                    }
                                    .alert("Leave radio lab?", isPresented: $confirmLeaveLab) {
                                        Button("Leave radio lab") { showRadioLab = false }
                                            .accessibilityIdentifier("radio-lab-leave")
                                        Button("Stay and check alarm", role: .cancel) {}
                                    } message: {
                                        Text("Leaving does not disarm the separate radio backend. Return to its controls if you need to disarm it.")
                                    }
                            }.preferredColorScheme(.dark)
                        }
                }
            }
                .onChange(of: showRadioLab) { _, visible in
                    if visible { camera.stop() }
                    else { outline.stop(reason: "Radio lab closed.") }
                    store.lifecycle(active: phase == .active && (directRadioLab || visible))
                    camera.lifecycle(active: phase == .active && !directRadioLab && !visible)
                }
                .onChange(of: phase) { _, next in
                    store.lifecycle(active: next == .active && (directRadioLab || showRadioLab))
                    camera.lifecycle(active: next == .active && !directRadioLab && !showRadioLab)
                    if next != .active && outline.mode == .ar { outline.invalidateTracking("App left the foreground.") }
                }
                .task {
                    store.lifecycle(active: phase == .active && (directRadioLab || showRadioLab))
                    camera.lifecycle(active: phase == .active && !directRadioLab && !showRadioLab)
                    #if DEBUG && targetEnvironment(simulator)
                    // Only the explicit local integration run reads its granted token file.
                    // No backend fixture, API response override, or Release route exists.
                    let env = ProcessInfo.processInfo.environment
                    if ProcessInfo.processInfo.arguments.contains("--ui-response-loss-fixture") {
                        store.endpointText = "http://127.0.0.1:1"
                        await store.connect(token: String(repeating: "a", count: 43), approveLAN: false)
                        return
                    }
                    if ProcessInfo.processInfo.arguments.contains("--integration"),
                       let file = env["THRESHOLD_TEST_TOKEN_FILE"],
                       let origin = env["THRESHOLD_TEST_ORIGIN"],
                       env["THRESHOLD_EXPECT_SESSION"] != nil,
                       origin == "http://127.0.0.1:18866",
                       let data = FileManager.default.contents(atPath: file),
                       let token = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines) {
                        store.endpointText = origin
                        await store.connect(token: token, approveLAN: false)
                    }
                    #endif
                }
        }
    }
}

#if DEBUG && targetEnvironment(simulator)
import ThresholdDomain

@MainActor final class ResponseLossFixture: BackendTransport {
    static let shared = ResponseLossFixture()
    let endpoint = try! ServerEndpoint("http://127.0.0.1:1")
    private(set) var disarmPOSTs = 0
    private var backendAlarm = "READY"
    func close() {}
    func state() async throws -> BackendState {
        if backendAlarm == "ARMED" { throw DomainError.invalid("UNIT FAULT FIXTURE: responses withheld after accepted Arm. No network was used.") }
        return try snapshot(backendAlarm)
    }
    func control(action: ControlAction, extra: [String:Any]) async throws -> BackendState {
        if action == .arm {
            backendAlarm = "ARMED"
            throw DomainError.invalid("UNIT FAULT FIXTURE: Arm accepted; response lost. Last known READY is not current. No network was used.")
        }
        if action == .disarm { disarmPOSTs += 1; backendAlarm = "DISARMED" }
        return try snapshot(backendAlarm)
    }
    func publish(_ zone: ZonePayload) async throws -> BackendState { throw DomainError.invalid("Not available in fault fixture") }
    private func snapshot(_ alarm: String) throws -> BackendState {
        let json: [String:Any] = ["schema_version":1,"session_id":"UNIT-FIXTURE","source_mode":"TEST","area_name":"UNIT FAULT FIXTURE","server_time":"2026-09-12T12:00:00Z","alarm_state":alarm,
            "health":["status":"healthy","detail":"Unit fixture, no network","sample_age_s":0.1,"packet_rate_hz":40.0,"valid_packets":100,"invalid_packets":0,"dropped_packets_estimate":0],
            "calibration":["progress":1.0,"ready":true,"detail":"Unit fixture"],"features":["activity_score":0.0,"threshold":3.0,"baseline_ready":true],"graph":[],"events":[],
            "calls":["enabled":false,"dry_run":true,"status":"disabled","detail":"No network"],"limitations":["UNIT FIXTURE"],
            "spatial":["target":"radio-motion","localization":["status":"unavailable","provenance":"none","exact_zone_verified":false,"detail":"No person located"],"test_actor":["running":false,"phase":"unavailable","can_start":false,"can_reset":false],"guard":["eligible":false,"reason":"No entry"]]]
        return try BackendState.decode(JSONSerialization.data(withJSONObject:json))
    }
}
#endif
