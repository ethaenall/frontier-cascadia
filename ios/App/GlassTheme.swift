import SwiftUI

/// A light, quiet palette. No animated background or GPU-heavy blur is needed.
enum GlassTheme {
    static let ink = Color(red: 0.12, green: 0.20, blue: 0.17)
    static let secondary = Color(red: 0.34, green: 0.41, blue: 0.38)
    static let green = Color(red: 0.16, green: 0.39, blue: 0.29)
    static let sage = Color(red: 0.79, green: 0.87, blue: 0.80)
    static let sky = Color(red: 0.83, green: 0.90, blue: 0.94)
    static let solid = Color(red: 0.97, green: 0.98, blue: 0.96)
    static let warning = Color(red: 0.52, green: 0.33, blue: 0.14)
    static let alert = Color(red: 0.66, green: 0.24, blue: 0.17)
}

struct GlassBackdrop: View {
    var body: some View {
        ZStack {
            LinearGradient(
                colors: [Color(red: 0.98, green: 0.97, blue: 0.94), GlassTheme.solid],
                startPoint: .topLeading, endPoint: .bottomTrailing
            )
            RadialGradient(
                colors: [GlassTheme.sky.opacity(0.75), .clear],
                center: .topTrailing, startRadius: 0, endRadius: 530
            )
            RadialGradient(
                colors: [GlassTheme.sage.opacity(0.66), .clear],
                center: .bottomLeading, startRadius: 0, endRadius: 560
            )
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

/// A soft edge behind the floating dock, opaque at the home-indicator area.
/// Content can scroll underneath the glass, but cannot peek out below the dock.
struct GlassDockScrim: View {
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        Group {
            if reduceTransparency {
                GlassTheme.solid
            } else {
                LinearGradient(
                    stops: [
                        .init(color: .clear, location: 0),
                        .init(color: GlassTheme.solid.opacity(0.38), location: 0.20),
                        .init(color: GlassTheme.solid.opacity(0.85), location: 0.55),
                        .init(color: GlassTheme.solid, location: 0.76),
                        .init(color: GlassTheme.solid, location: 1)
                    ],
                    startPoint: .top, endPoint: .bottom
                )
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }
}

private struct GlassPanel: ViewModifier {
    var cornerRadius: CGFloat
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        content
            .background {
                if reduceTransparency {
                    shape.fill(GlassTheme.solid)
                } else {
                    shape.fill(.regularMaterial)
                        .overlay { shape.fill(.white.opacity(0.30)) }
                }
            }
            .overlay {
                shape.strokeBorder(
                    LinearGradient(
                        colors: [.white.opacity(0.96), .white.opacity(0.30), .white.opacity(0.76)],
                        startPoint: .topLeading, endPoint: .bottomTrailing
                    ), lineWidth: 1
                )
                .allowsHitTesting(false)
            }
            .shadow(color: GlassTheme.ink.opacity(0.055), radius: 18, x: 0, y: 8)
    }
}

extension View {
    func thresholdGlass(cornerRadius: CGFloat = 28) -> some View {
        modifier(GlassPanel(cornerRadius: cornerRadius))
    }
}

struct GlassButtonStyle: ButtonStyle {
    enum Tone { case primary, secondary, alert }
    var tone: Tone = .primary
    @Environment(\.isEnabled) private var enabled
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var fill: Color {
        switch tone {
        case .primary: return GlassTheme.green
        case .alert: return GlassTheme.alert
        case .secondary: return reduceTransparency ? .white : .white.opacity(0.82)
        }
    }

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.subheadline, design: .rounded, weight: .semibold))
            .multilineTextAlignment(.center)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, 14)
            .padding(.vertical, 13)
            .frame(maxWidth: .infinity, minHeight: 48)
            .foregroundStyle(tone == .secondary ? GlassTheme.ink : .white)
            .background {
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .fill(fill)
                    .overlay {
                        RoundedRectangle(cornerRadius: 18, style: .continuous)
                            .strokeBorder(tone == .secondary ? GlassTheme.ink.opacity(0.08) : .white.opacity(0.16), lineWidth: 1)
                    }
            }
            .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .opacity(enabled ? (configuration.isPressed ? 0.85 : 1) : 0.43)
            .scaleEffect(configuration.isPressed && !reduceMotion ? 0.985 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.12), value: configuration.isPressed)
    }
}
