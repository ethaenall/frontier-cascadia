import AVFoundation
import Foundation

@MainActor class AlarmSound: ObservableObject {
    @Published private(set) var enabled = false
    @Published private(set) var status = "Off · foreground sound only"
    private var player: AVAudioPlayer?
    private var lastTone = Date.distantPast
    private var mutedEvent: String?
    func toggle() { if enabled { enabled = false; player?.stop(); status = "Off · foreground sound only" } else { enabled = true; mutedEvent = nil; test() } }
    func test() {
        do {
            try AVAudioSession.sharedInstance().setCategory(.playback, mode: .default)
            try AVAudioSession.sharedInstance().setActive(true)
            player = try AVAudioPlayer(data: Self.wave())
            player?.volume = 0.7
            guard player?.play() == true else { throw NSError(domain: "audio", code: 1) }
            status = enabled ? "Enabled · foreground only; verify phone volume" : "Test requested · verify audibility on this phone"
        } catch { status = "Sound failed. Check audio route and volume."; enabled = false }
    }
    func observe(eventID: String?, alarm: Bool, foreground: Bool) {
        guard enabled, foreground, alarm, let eventID, eventID != mutedEvent else { player?.stop(); return }
        if Date().timeIntervalSince(lastTone) >= 2 { lastTone = Date(); test() }
    }
    func mute(eventID: String?) { mutedEvent = eventID; player?.stop(); status = "Muted locally · backend alarm unchanged" }
    func stop() { player?.stop() }
    private static func wave() -> Data {
        let rate = 22050, count = rate / 2
        var data = Data()
        func text(_ s: String) { data.append(contentsOf: s.utf8) }
        func u16(_ v: UInt16) { var v = v.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
        func u32(_ v: UInt32) { var v = v.littleEndian; withUnsafeBytes(of: &v) { data.append(contentsOf: $0) } }
        text("RIFF"); u32(UInt32(36+count*2)); text("WAVEfmt "); u32(16); u16(1); u16(1); u32(UInt32(rate)); u32(UInt32(rate*2)); u16(2); u16(16); text("data"); u32(UInt32(count*2))
        for i in 0..<count {
            let t = Double(i)/Double(rate)
            let envelope = min(1, t*50) * min(1, (0.5-t)*30)
            let hz = t < 0.25 ? 880.0 : 660.0
            let sample = Int16(sin(t*hz*2*Double.pi) * envelope * 12000)
            u16(UInt16(bitPattern: sample))
        }
        return data
    }
}
