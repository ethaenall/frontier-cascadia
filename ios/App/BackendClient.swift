import Foundation
import ThresholdDomain

private final class NoRedirects: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) { completionHandler(nil) }
}

/// Ephemeral, in-memory bearer. No cookies, URL credentials, request logs, or trust override.
@MainActor protocol BackendTransport: AnyObject {
    var endpoint: ServerEndpoint { get }
    func close()
    func state() async throws -> BackendState
    func control(action: ControlAction, extra: [String:Any]) async throws -> BackendState
    func publish(_ zone: ZonePayload) async throws -> BackendState
}

@MainActor final class BackendClient: BackendTransport {
    let endpoint: ServerEndpoint
    private var token: String
    private let session: URLSession
    init(endpoint: ServerEndpoint, token: String) {
        self.endpoint = endpoint; self.token = token
        let config = URLSessionConfiguration.ephemeral
        config.httpCookieStorage = nil; config.httpShouldSetCookies = false
        config.urlCache = nil; config.requestCachePolicy = .reloadIgnoringLocalCacheData
        config.timeoutIntervalForRequest = 3; config.timeoutIntervalForResource = 5
        config.waitsForConnectivity = false
        session = URLSession(configuration: config, delegate: NoRedirects(), delegateQueue: nil)
    }
    func close() { token = ""; session.invalidateAndCancel() }
    func state() async throws -> BackendState { try await request(path: "api/state") }
    func control(action: ControlAction, extra: [String:Any] = [:]) async throws -> BackendState {
        var body = extra; body["action"] = action.rawValue
        let spatial: Set<ControlAction> = [.setZone, .clearZone, .setTarget, .testWalk, .resetActor]
        return try await request(path: spatial.contains(action) ? "api/spatial/control" : "api/control", body: JSONSerialization.data(withJSONObject: body))
    }
    func publish(_ zone: ZonePayload) async throws -> BackendState { try await request(path: "api/spatial/control", body: zone.encoded()) }
    private func request(path: String, body: Data? = nil) async throws -> BackendState {
        var request = URLRequest(url: endpoint.origin.appendingPathComponent(path))
        request.httpMethod = body == nil ? "GET" : "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body { request.setValue("application/json", forHTTPHeaderField: "Content-Type"); request.httpBody = body }
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse, http.url?.scheme == endpoint.origin.scheme, http.url?.host == endpoint.origin.host, http.url?.port == endpoint.origin.port, data.count <= 2_000_000 else { throw DomainError.invalid("Invalid server response. Controls are locked.") }
            guard (200...299).contains(http.statusCode) else {
                // Do not reflect arbitrary response bodies: they could contain credentials.
                let messages = [401: "Pair token rejected. Disconnect and enter the current token.", 403: "Server refused this origin.", 409: "Command rejected by backend rules. Refresh state, disarm, and check calibration, frame, and TEST actor.", 422: "Backend rejected the command geometry or fields.", 429: "Too many pairing attempts. Wait one minute."]
                throw DomainError.invalid(messages[http.statusCode] ?? "Request failed (HTTP \(http.statusCode)). No success was recorded.")
            }
            return try BackendState.decode(data)
        } catch let e as DomainError { throw e }
        catch is DecodingError { throw DomainError.invalid("Backend schema mismatch. No control success was recorded.") }
        catch { throw DomainError.invalid("Server unavailable. Check the private address, permission, trusted TLS, and LAN/ATS setup. Last known state is not an all-clear.") }
    }
}
