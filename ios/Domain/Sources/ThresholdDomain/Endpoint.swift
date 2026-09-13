import Foundation

public enum DomainError: LocalizedError, Equatable {
    case invalid(String)
    public var errorDescription: String? { if case .invalid(let text) = self { return text }; return nil }
}

/// Literal RFC1918 IPv4 or loopback only. No DNS, URLs containing secrets, or redirects.
public struct ServerEndpoint: Equatable, Sendable {
    public let origin: URL
    public let isLoopback: Bool
    public let isPlaintext: Bool
    public init(_ text: String, approvePlaintextLAN: Bool = false) throws {
        let raw = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.contains("%"), !raw.contains("\\"),
              let c = URLComponents(string: raw), let scheme = c.scheme,
              ["http", "https"].contains(scheme), let host = c.host,
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.path.isEmpty || c.path == "/", c.port == nil || (1...65535).contains(c.port!),
              !raw.contains(where: { $0.isWhitespace }) else {
            throw DomainError.invalid("Enter only a private server origin, such as https://192.168.1.20:8765. No path, credentials, or query.")
        }
        let resolved = host == "localhost" ? "127.0.0.1" : host
        let parts = resolved.split(separator: ".", omittingEmptySubsequences: false)
        let octets = parts.compactMap { Int($0) }
        guard parts.count == 4, octets.count == 4,
              zip(parts, octets).allSatisfy({ String($0.1) == String($0.0) && (0...255).contains($0.1) }) else {
            throw DomainError.invalid("Use a literal private IPv4 address or localhost. Public hosts and DNS names are refused.")
        }
        let loopback = resolved == "127.0.0.1"
        let privateIP = octets[0] == 10 || (octets[0] == 172 && (16...31).contains(octets[1])) || (octets[0] == 192 && octets[1] == 168)
        guard loopback || privateIP else { throw DomainError.invalid("Only loopback and RFC1918 private addresses are allowed.") }
        guard scheme != "http" || loopback || approvePlaintextLAN else {
            throw DomainError.invalid("HTTP exposes your bearer token to the local network. Approve a trusted private LAN explicitly, or use trusted HTTPS.")
        }
        var safe = URLComponents()
        safe.scheme = scheme; safe.host = resolved; safe.port = c.port
        guard let url = safe.url else { throw DomainError.invalid("Invalid origin.") }
        origin = url; isLoopback = loopback; isPlaintext = scheme == "http"
    }
    public static func validToken(_ text: String) -> Bool {
        (32...128).contains(text.utf8.count) && text.utf8.allSatisfy { (65...90).contains($0) || (97...122).contains($0) || (48...57).contains($0) || $0 == 45 || $0 == 95 }
    }
}
