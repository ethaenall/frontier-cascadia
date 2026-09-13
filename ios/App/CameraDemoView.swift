import AVFoundation
import SwiftUI
import ThresholdDomain
import UIKit

/// The camera demo is independent of the backend radio lab. Nothing starts on appear.
@MainActor
struct CameraDemoView: View {
    @ObservedObject var monitor: CameraEntryMonitor
    let openRadioLab: () -> Void

    @Environment(\.dynamicTypeSize) private var typeSize
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @State private var showFullSetup = false

    var body: some View {
        ZStack {
            GlassBackdrop()
            VStack(spacing: 0) {
                header
                ScrollView {
                    VStack(alignment: .leading, spacing: compactIntroduction ? 20 : 24) {
                        introduction
                        // A tall Dynamic Type dock belongs in the scroll content, not
                        // in an inset that can consume or extend beyond the window.
                        if typeSize.isAccessibilitySize { controlDock }
                        doorway
                        positioningNote
                        journal
                        fullSetup
                        limitations
                    }
                    .frame(maxWidth: 560)
                    .padding(.horizontal, 22)
                    .padding(.top, 15)
                    .padding(.bottom, 28)
                    .frame(maxWidth: .infinity)
                }
                .scrollIndicators(.hidden)
            }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !typeSize.isAccessibilitySize {
                controlDock
                    .frame(maxWidth: 560)
                    .padding(.horizontal, 16)
                    .padding(.top, 10)
                    .padding(.bottom, 8)
                    .frame(maxWidth: .infinity)
                    .background {
                        GlassDockScrim().ignoresSafeArea(.container, edges: .bottom)
                    }
            }
        }
        .foregroundStyle(GlassTheme.ink)
        .tint(GlassTheme.green)
        .preferredColorScheme(.light)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("camera-demo-root")
    }

    private var header: some View {
        HStack(spacing: 12) {
            if !typeSize.isAccessibilitySize {
                HStack(spacing: 8) {
                    ThresholdDoorMark()
                        .stroke(GlassTheme.green, style: StrokeStyle(lineWidth: 2, lineCap: .round, lineJoin: .round))
                        .frame(width: 18, height: 23)
                        .accessibilityHidden(true)
                    Text("threshold")
                        .font(.system(.title3, design: .rounded, weight: .semibold))
                        .tracking(-0.6)
                }
                .accessibilityLabel("Threshold")
                Spacer(minLength: 4)
            }
            HStack(spacing: 6) {
                Image(systemName: monitor.inputMode == .camera ? "camera" : "play.circle")
                    .accessibilityHidden(true)
                Text(sourceTitle)
                    .tracking(0.7)
                    .accessibilityLabel(sourceTitle)
                    .accessibilityIdentifier("camera-source")
            }
            .font(.system(.caption2, design: .rounded, weight: .bold))
            .foregroundStyle(GlassTheme.green)
            .padding(.horizontal, 12)
            .padding(.vertical, 9)
            .background(Capsule().fill(reduceTransparency ? GlassTheme.solid : .white.opacity(0.74)))
            .overlay(Capsule().strokeBorder(.white.opacity(0.9), lineWidth: 1))
            if typeSize.isAccessibilitySize { Spacer(minLength: 0) }
        }
        .padding(.horizontal, 24)
        .padding(.top, 8)
        .padding(.bottom, 10)
    }

    private var compactIntroduction: Bool {
        !typeSize.isAccessibilitySize && (monitor.isRunning || monitor.phase == .starting)
    }

    private var introduction: some View {
        VStack(alignment: .leading, spacing: 8) {
            if compactIntroduction {
                Text("Your doorway, in view.")
                    .font(.system(.title2, design: .rounded, weight: .medium))
                    .tracking(-0.4)
                    .accessibilityAddTraits(.isHeader)
            } else {
                Text("A quieter watch.")
                    .font(.system(.largeTitle, design: .rounded, weight: .medium))
                    .tracking(-1.0)
                    .accessibilityAddTraits(.isHeader)
                Text("Your doorway, in view.")
                    .font(.system(.title3, design: .rounded))
                    .foregroundStyle(GlassTheme.secondary)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    private var doorway: some View {
        VStack(alignment: .leading, spacing: 12) {
            ZStack {
                // The host always reserves exactly 3:4, including stopped, denied,
                // starting and rehearsal states. The preview never crops the image.
                GeometryReader { geometry in
                    ZStack {
                        if monitor.inputMode == .camera {
                            CameraPreview(session: monitor.session)
                                .accessibilityHidden(true)
                        }
                        if !showsCameraFrames {
                            DoorwayIllustration(personVisible: monitor.inputMode == .rehearsal && monitor.personVisible)
                        }
                        if showsWatchRegion {
                            regionOverlay(in: geometry.size)
                        }
                        stageBadges
                    }
                    .frame(width: geometry.size.width, height: geometry.size.height)
                }
                .aspectRatio(3.0 / 4.0, contentMode: .fit)
            }
            .frame(maxWidth: typeSize.isAccessibilitySize ? 390 : 300)
            .background(GlassTheme.solid)
            .clipShape(RoundedRectangle(cornerRadius: 32, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: 32, style: .continuous)
                    .strokeBorder(.white.opacity(0.92), lineWidth: 1.5)
                    .allowsHitTesting(false)
            }
            .shadow(color: GlassTheme.ink.opacity(0.10), radius: 24, x: 0, y: 12)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(stageAccessibilityLabel)
            .accessibilityIdentifier("camera-aperture")
            .frame(maxWidth: .infinity)

            HStack(alignment: .top, spacing: 8) {
                Image(systemName: monitor.inputMode == .rehearsal ? "sparkles" : "lock.shield")
                    .accessibilityHidden(true)
                Text(monitor.inputMode == .rehearsal
                     ? "Synthetic rehearsal. No camera imagery or physical entry evidence."
                     : "Processed on this iPhone. Frames are not saved or uploaded.")
                    .fixedSize(horizontal: false, vertical: true)
            }
            .font(.footnote)
            .foregroundStyle(GlassTheme.secondary)
        }
    }

    private var showsCameraFrames: Bool {
        monitor.inputMode == .camera && monitor.isRunning &&
        monitor.phase != .stopped && monitor.phase != .starting && monitor.phase != .unavailable
    }

    private var showsWatchRegion: Bool {
        monitor.phase == .clearing || monitor.phase == .ready ||
        monitor.phase == .armed || monitor.phase == .alert
    }

    private var stageBadges: some View {
        VStack(spacing: 0) {
            HStack {
                HStack(spacing: 6) {
                    Circle().fill(phaseColor).frame(width: 6, height: 6)
                    Text(stageTitle)
                }
                .padding(.horizontal, 11)
                .padding(.vertical, 8)
                .background(Capsule().fill(GlassTheme.solid))
                Spacer(minLength: 0)
            }
            Spacer(minLength: 0)
            if monitor.inputMode == .rehearsal {
                Text("REHEARSAL / SYNTHETIC")
                    .tracking(0.9)
                    .padding(.horizontal, 13)
                    .padding(.vertical, 9)
                    .background(Capsule().fill(GlassTheme.solid))
            } else if !showsCameraFrames {
                VStack(spacing: 5) {
                    Text(monitor.phase == .unavailable ? "Camera unavailable" : monitor.phase == .starting ? "Opening your camera…" : "Your view starts here")
                        .font(.system(size: 17, weight: .semibold, design: .rounded))
                    Text("Illustration · not a camera image")
                        .foregroundStyle(GlassTheme.secondary)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 13)
                .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(GlassTheme.solid))
            } else {
                Text("Outlined area · watch region")
                    .padding(.horizontal, 13)
                    .padding(.vertical, 9)
                    .background(Capsule().fill(GlassTheme.solid))
            }
        }
        // Decorative image annotations have an equivalent, scalable spoken/text
        // description outside this aperture. They cannot crowd the image at AX sizes.
        .font(.system(size: 11, weight: .semibold, design: .rounded))
        .foregroundStyle(GlassTheme.ink)
        .padding(17)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private func regionOverlay(in size: CGSize) -> some View {
        let zone = imageRect(monitor.zone, in: size)
        return ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .fill(phaseColor.opacity(monitor.inputMode == .rehearsal ? 0.055 : 0.10))
                .overlay {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .strokeBorder(.white.opacity(0.80), lineWidth: 1)
                }
                .overlay {
                    RegionCorners()
                        .stroke(phaseColor, style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round))
                }
                .frame(width: zone.width, height: zone.height)
                .position(x: zone.midX, y: zone.midY)

            ForEach(Array(monitor.personBoxes.enumerated()), id: \.offset) { _, box in
                let rect = imageRect(box, in: size)
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .strokeBorder(.white, lineWidth: 1.5)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(phaseColor.opacity(0.13)))
                    .frame(width: rect.width, height: rect.height)
                    .position(x: rect.midX, y: rect.midY)
            }
        }
        .frame(width: size.width, height: size.height)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    /// The engine uses a rotated, unmirrored 480x640 buffer and top-left boxes.
    /// The 3:4 aspect-fit aperture has the same image rect, so there is no crop,
    /// offset, axis flip, or independent overlay scaling to introduce drift.
    private func imageRect(_ normalized: CGRect, in size: CGSize) -> CGRect {
        guard normalized.origin.x.isFinite, normalized.origin.y.isFinite,
              normalized.width.isFinite, normalized.height.isFinite else { return .zero }
        let bounded = normalized.intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
        guard !bounded.isNull, !bounded.isEmpty else { return .zero }
        return CGRect(x: bounded.minX * size.width, y: bounded.minY * size.height,
                      width: bounded.width * size.width, height: bounded.height * size.height)
    }

    private var positioningNote: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: monitor.inputMode == .rehearsal ? "play.square" : "iphone")
                .font(.system(size: 24, weight: .light))
                .foregroundStyle(GlassTheme.green)
                .frame(width: 36, height: 40)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                Text(monitor.inputMode == .rehearsal ? "Try the flow, without a camera." : "Set it down. Keep it still.")
                    .font(.system(.headline, design: .rounded))
                Text(monitor.inputMode == .rehearsal
                     ? "Arm the demo, then tap Simulate entry. Acknowledge the alert before arming again. Nothing here is a measured person."
                     : "Keep this iPhone upright and fixed on a well-lit doorway. Clear the whole camera view to prepare, then arm. The outlined region is the entry target.")
                    .font(.subheadline)
                    .foregroundStyle(GlassTheme.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(20)
        .thresholdGlass()
    }

    private var controlDock: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 10) {
                Image(systemName: phaseSymbol)
                    .font(.system(.title3, design: .rounded, weight: .medium))
                    .foregroundStyle(phaseColor)
                    .accessibilityHidden(true)
                Text(phaseTitle)
                    .font(.system(.headline, design: .rounded))
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(monitor.phase.rawValue)
                    .accessibilityIdentifier("camera-phase")
                Spacer(minLength: 0)
                if monitor.phase != .stopped && monitor.phase != .unavailable {
                    Button("Stop") { monitor.stop() }
                        .font(.system(.subheadline, design: .rounded, weight: .semibold))
                        .foregroundStyle(GlassTheme.secondary)
                        .frame(minWidth: 48, minHeight: 44)
                        .contentShape(Rectangle())
                        .accessibilityHint("Stops the input and disarms this demo.")
                        .accessibilityIdentifier("camera-stop")
                }
            }
            Text(monitor.statusMessage)
                .font(.footnote)
                .foregroundStyle(GlassTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("camera-status")
            controls
            if monitor.phase == .alert {
                Text("Acknowledge clears this alert. It does not rearm.")
                    .font(.caption)
                    .foregroundStyle(GlassTheme.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(18)
        .thresholdGlass(cornerRadius: 30)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("camera-control-dock")
    }

    private var actionLayout: AnyLayout {
        typeSize.isAccessibilitySize
        ? AnyLayout(VStackLayout(spacing: 9))
        : AnyLayout(HStackLayout(spacing: 9))
    }

    @ViewBuilder
    private var controls: some View {
        switch monitor.phase {
        case .stopped, .unavailable, .starting:
            actionLayout {
                Button {
                    Task { await monitor.startCamera() }
                } label: {
                    Label(monitor.phase == .unavailable ? "Retry camera" : monitor.phase == .starting ? "Starting…" : "Start camera", systemImage: "camera")
                }
                .buttonStyle(GlassButtonStyle())
                .disabled(monitor.phase == .starting)
                .accessibilityHint("Asks for camera access. Frames stay on this iPhone.")
                .accessibilityIdentifier("camera-start")

                rehearsalButton(reset: false)
                    .disabled(monitor.phase == .starting)
            }
        case .clearing, .ready:
            actionLayout {
                Button { monitor.arm() } label: {
                    Label("Arm demo", systemImage: "checkmark.shield")
                }
                .buttonStyle(GlassButtonStyle())
                .disabled(!monitor.canArm)
                .accessibilityHint("Arms only after the whole camera view is empty and ready.")
                .accessibilityIdentifier("camera-arm")
                rehearsalButton(reset: monitor.inputMode == .rehearsal)
            }
        case .armed:
            actionLayout {
                if monitor.inputMode == .rehearsal {
                    Button { monitor.rehearseEntry() } label: {
                        Label("Simulate entry", systemImage: "figure.walk")
                    }
                    .buttonStyle(GlassButtonStyle())
                    .accessibilityHint("Creates synthetic input, not physical motion evidence.")
                    .accessibilityIdentifier("camera-simulate-entry")
                }
                disarmButton
            }
        case .alert:
            actionLayout {
                Button { monitor.acknowledge() } label: {
                    Label("Acknowledge", systemImage: "checkmark")
                }
                .buttonStyle(GlassButtonStyle(tone: .alert))
                .accessibilityHint("Clears the alert and disarms. Does not automatically rearm.")
                .accessibilityIdentifier("camera-acknowledge")
                disarmButton
            }
        }
    }

    private func rehearsalButton(reset: Bool) -> some View {
        Button { monitor.startRehearsal() } label: {
            Label(reset ? "Reset" : "Rehearsal", systemImage: reset ? "arrow.counterclockwise" : "play")
        }
        .buttonStyle(GlassButtonStyle(tone: .secondary))
        .accessibilityLabel(reset ? "Reset rehearsal" : "Rehearsal")
        .accessibilityHint("Starts clearly labelled synthetic input. No camera is used.")
        .accessibilityIdentifier("camera-rehearsal")
    }

    private var disarmButton: some View {
        Button { monitor.disarm() } label: {
            Label("Disarm", systemImage: "pause")
        }
        .buttonStyle(GlassButtonStyle(tone: .secondary))
        .accessibilityIdentifier("camera-disarm")
    }

    private var journal: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Recent entries")
                .font(.system(.title3, design: .rounded, weight: .semibold))
                .accessibilityAddTraits(.isHeader)
            if monitor.events.isEmpty {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "clock")
                        .font(.title3.weight(.light))
                        .foregroundStyle(GlassTheme.secondary)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text("No entries yet.")
                            .font(.subheadline.weight(.medium))
                        Text("Each entry will show its source and local time.")
                            .font(.footnote)
                            .foregroundStyle(GlassTheme.secondary)
                    }
                }
            } else {
                ForEach(monitor.events.sorted { $0.occurredAt > $1.occurredAt }) { event in
                    eventRow(event)
                }
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(21)
        .thresholdGlass()
    }

    private func eventRow(_ event: CameraEntryEvent) -> some View {
        let source = event.source == .camera ? "CAMERA DEMO" : "REHEARSAL"
        let title = event.source == .camera ? "Person detected in region" : "Synthetic entry"
        let time = event.occurredAt.formatted(date: .omitted, time: .standard)
        return HStack(alignment: .top, spacing: 12) {
            Image(systemName: event.source == .camera ? "person.crop.rectangle" : "play.circle")
                .font(.system(.title3, design: .rounded))
                .foregroundStyle(GlassTheme.green)
                .frame(width: 28, height: 32)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 5) {
                Text(title)
                    .font(.system(.subheadline, design: .rounded, weight: .semibold))
                Text(source + (event.source == .rehearsal ? " · SYNTHETIC" : " · ON IPHONE"))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(GlassTheme.green)
                Text(time + " · local time")
                    .font(.footnote)
                    .monospacedDigit()
                    .foregroundStyle(GlassTheme.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title). \(source)\(event.source == .rehearsal ? ", SYNTHETIC" : ""). \(time), local time.")
        .accessibilityIdentifier("camera-event")
    }

    private var fullSetup: some View {
        VStack(alignment: .leading, spacing: 14) {
            DisclosureGroup(isExpanded: $showFullSetup) {
                Text("The camera-free router mode is planned, not verified. It needs a compatible router and compatible sensing hardware. Do not assume an off-the-shelf router is compatible.")
                    .font(.subheadline)
                    .foregroundStyle(GlassTheme.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.top, 10)
            } label: {
                Label("Full setup", systemImage: "wifi")
                    .font(.system(.headline, design: .rounded))
                    .foregroundStyle(GlassTheme.ink)
                    .frame(minHeight: 44)
            }
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.18), value: showFullSetup)

            Text("Compatible router required, with compatible sensing hardware. Camera-free mode is planned and unverified.")
                .font(.footnote)
                .foregroundStyle(GlassTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
                .accessibilityIdentifier("camera-router-note")

            Divider().overlay(GlassTheme.ink.opacity(0.04))

            Button {
                monitor.stop()
                openRadioLab()
            } label: {
                HStack(spacing: 10) {
                    Text("Open radio lab")
                    Spacer(minLength: 0)
                    Image(systemName: "arrow.up.right")
                }
                .font(.system(.subheadline, design: .rounded, weight: .semibold))
                .foregroundStyle(GlassTheme.green)
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Stops this demo and opens separate backend tools.")
            .accessibilityIdentifier("camera-open-radio-lab")

            Text("Separate backend tools. The radio lab does not share this demo’s alarm.")
                .font(.footnote)
                .foregroundStyle(GlassTheme.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(21)
        .thresholdGlass()
    }

    private var limitations: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("A foreground-only prototype", systemImage: "info.circle")
                .font(.footnote.weight(.medium))
            Text("On-screen alerts only. Keep the app open. Leaving it stops this watch and disarms the demo. Person presence is not identity, fall detection, or an emergency guarantee.")
                .font(.footnote)
        }
        .foregroundStyle(GlassTheme.secondary)
        .fixedSize(horizontal: false, vertical: true)
        .padding(.horizontal, 3)
    }

    private var sourceTitle: String {
        monitor.inputMode == .camera ? "CAMERA DEMO" : "REHEARSAL"
    }

    private var phaseTitle: String {
        switch monitor.phase {
        case .stopped: return "Not watching"
        case .starting: return "Opening camera"
        case .clearing: return "Preparing your view"
        case .ready: return "Ready to arm"
        case .armed: return "Watching the region"
        case .alert: return monitor.inputMode == .rehearsal ? "Synthetic entry" : "Entry detected"
        case .unavailable: return "Camera unavailable"
        }
    }

    private var phaseSymbol: String {
        switch monitor.phase {
        case .stopped: return "moon"
        case .starting: return "camera"
        case .clearing: return "viewfinder"
        case .ready: return "checkmark.circle"
        case .armed: return "checkmark.shield"
        case .alert: return "exclamationmark.circle"
        case .unavailable: return "video.slash"
        }
    }

    private var phaseColor: Color {
        switch monitor.phase {
        case .ready, .armed: return GlassTheme.green
        case .alert: return GlassTheme.alert
        case .clearing, .unavailable: return GlassTheme.warning
        case .stopped, .starting: return GlassTheme.secondary
        }
    }

    private var stageTitle: String {
        if monitor.inputMode == .rehearsal { return "Illustration only" }
        if showsCameraFrames { return "On-device camera" }
        if monitor.phase == .starting { return "Camera starting" }
        return "Camera off"
    }

    private var stageAccessibilityLabel: String {
        if monitor.inputMode == .rehearsal {
            return "Synthetic doorway illustration. REHEARSAL. No camera imagery. " +
                (monitor.personVisible ? "Synthetic person input is visible." : "No synthetic person input is visible.")
        }
        if showsCameraFrames {
            return "On-device camera preview. The outlined area is the watch region. " +
                (monitor.personVisible ? "A person is visible in the image." : "No person is currently detected in the image.")
        }
        return "Doorway illustration, not a camera image. Camera is not watching."
    }
}

private struct ThresholdDoorMark: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + 2))
        path.addQuadCurve(to: CGPoint(x: rect.minX + 2, y: rect.minY), control: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.move(to: CGPoint(x: rect.width * 0.46, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.width * 0.46, y: rect.height * 0.17))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        return path
    }
}

private struct RegionCorners: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let radius: CGFloat = min(18, rect.width / 4, rect.height / 4)
        let length: CGFloat = min(31, rect.width / 3, rect.height / 3)
        path.move(to: CGPoint(x: rect.minX, y: rect.minY + length))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + radius))
        path.addQuadCurve(to: CGPoint(x: rect.minX + radius, y: rect.minY), control: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.minX + length, y: rect.minY))
        path.move(to: CGPoint(x: rect.maxX - length, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - radius, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY + radius), control: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY + length))
        path.move(to: CGPoint(x: rect.maxX, y: rect.maxY - length))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - radius))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - radius, y: rect.maxY), control: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.maxX - length, y: rect.maxY))
        path.move(to: CGPoint(x: rect.minX + length, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + radius, y: rect.maxY))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.maxY - radius), control: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY - length))
        return path
    }
}

/// Deliberately geometric artwork, never a substitute or fixture for camera imagery.
private struct DoorwayIllustration: View {
    var personVisible: Bool
    var body: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height
            ZStack {
                // Opaque backing also hides any last camera frame after Stop or failure.
                GlassTheme.solid
                LinearGradient(colors: [GlassTheme.sky.opacity(0.52), GlassTheme.solid, GlassTheme.sage.opacity(0.62)],
                               startPoint: .topLeading, endPoint: .bottomTrailing)
                Ellipse()
                    .fill(GlassTheme.green.opacity(0.055))
                    .frame(width: width * 0.70, height: height * 0.055)
                    .position(x: width * 0.5, y: height * 0.81)
                RoundedRectangle(cornerRadius: 24, style: .continuous)
                    .fill(.white.opacity(0.58))
                    .overlay {
                        RoundedRectangle(cornerRadius: 24, style: .continuous)
                            .strokeBorder(.white.opacity(0.90), lineWidth: 2)
                    }
                    .frame(width: width * 0.54, height: height * 0.62)
                    .position(x: width * 0.5, y: height * 0.49)
                RoundedRectangle(cornerRadius: 17, style: .continuous)
                    .fill(LinearGradient(colors: [GlassTheme.sky.opacity(0.30), GlassTheme.sage.opacity(0.26)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                    .overlay {
                        RoundedRectangle(cornerRadius: 17, style: .continuous)
                            .strokeBorder(GlassTheme.green.opacity(0.14), lineWidth: 1)
                    }
                    .frame(width: width * 0.41, height: height * 0.54)
                    .position(x: width * 0.5, y: height * 0.50)
                Capsule()
                    .fill(GlassTheme.green.opacity(0.32))
                    .frame(width: 4, height: 22)
                    .position(x: width * 0.635, y: height * 0.52)
                if personVisible {
                    Image(systemName: "figure.walk")
                        .font(.system(size: width * 0.23, weight: .ultraLight))
                        .foregroundStyle(GlassTheme.green.opacity(0.62))
                        .position(x: width * 0.48, y: height * 0.50)
                }
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

private struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> CameraPreviewSurface {
        let view = CameraPreviewSurface()
        view.previewLayer.videoGravity = .resizeAspect
        view.previewLayer.session = session
        view.configurePortraitConnection()
        return view
    }

    func updateUIView(_ view: CameraPreviewSurface, context: Context) {
        if view.previewLayer.session !== session { view.previewLayer.session = session }
        view.previewLayer.videoGravity = .resizeAspect
        view.configurePortraitConnection()
    }

    static func dismantleUIView(_ view: CameraPreviewSurface, coordinator: ()) {
        // Session ownership stays in CameraEntryMonitor. Detaching this presentation
        // layer never starts, stops, or reconfigures capture inputs.
        view.previewLayer.session = nil
    }
}

private final class CameraPreviewSurface: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = UIColor(GlassTheme.solid)
        isUserInteractionEnabled = false
        isAccessibilityElement = false
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func layoutSubviews() {
        super.layoutSubviews()
        configurePortraitConnection()
    }

    func configurePortraitConnection() {
        guard let connection = previewLayer.connection else { return }
        if connection.isVideoRotationAngleSupported(90) {
            if connection.videoRotationAngle != 90 { connection.videoRotationAngle = 90 }
        } else if connection.isVideoOrientationSupported {
            connection.videoOrientation = .portrait
        }
        if connection.isVideoMirroringSupported {
            connection.automaticallyAdjustsVideoMirroring = false
            connection.isVideoMirrored = false
        }
    }
}
