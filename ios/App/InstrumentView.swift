import SwiftUI
import ThresholdDomain

private enum Ink {
    static let background = Color(red: 0.063, green: 0.075, blue: 0.065)
    static let field = Color(red: 0.09, green: 0.108, blue: 0.091)
    static let ivory = Color(red: 0.94, green: 0.93, blue: 0.88)
    static let muted = Color(red: 0.66, green: 0.70, blue: 0.62)
    static let rule = Color(red: 0.27, green: 0.31, blue: 0.24)
    static let lime = Color(red: 0.82, green: 0.89, blue: 0.55)
    static let amber = Color(red: 0.94, green: 0.76, blue: 0.53)
    static let alarm = Color(red: 1, green: 0.57, blue: 0.45)
}
private struct Micro: View {
    let text: String
    var color: Color = Ink.muted
    var body: some View { Text(text).font(.caption2.monospaced().weight(.medium)).tracking(0.8).foregroundStyle(color).fixedSize(horizontal: false, vertical: true) }
}
private struct InstrumentButton: ButtonStyle {
    var primary = false
    var danger = false
    @Environment(\.isEnabled) private var enabled
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.subheadline.weight(.semibold)).multilineTextAlignment(.center)
            .padding(.horizontal, 8).padding(.vertical, 10)
            .frame(maxWidth: .infinity, minHeight: 48).fixedSize(horizontal: false, vertical: true).contentShape(Rectangle())
            .foregroundStyle(primary ? Ink.background : danger ? Ink.alarm : Ink.ivory)
            .background(primary ? (danger ? Ink.alarm : Ink.lime) : Ink.field)
            .overlay(Rectangle().stroke(primary ? Color.clear : Ink.rule, lineWidth: 1))
            .opacity(enabled ? (configuration.isPressed ? 0.75 : 1) : 0.4)
    }
}

struct InstrumentView: View {
    @ObservedObject var store: InstrumentStore
    @ObservedObject var outline: FloorOutline
    @State private var pairing = false
    @State private var editing = false
    @State private var tab = 0
    @State private var localError: String?
    @State private var showTestActions = false
    @Environment(\.dynamicTypeSize) private var typeSize
    var body: some View {
        VStack(spacing: 0) {
            #if DEBUG && targetEnvironment(simulator)
            if ProcessInfo.processInfo.arguments.contains("--ui-response-loss-fixture") {
                Text("UNIT FAULT FIXTURE · NO NETWORK · Disarm POSTs: \(ResponseLossFixture.shared.disarmPOSTs)")
                    .font(.system(size: 11, design: .monospaced)).foregroundStyle(Ink.amber).padding(8).accessibilityIdentifier("fault-fixture-label")
            }
            #endif
            header
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    title
                    if let error = store.commandError ?? store.fault ?? localError { errorStrip(error) }
                    if tab == 0 { boundary }
                    else if tab == 1 { SignalView(store: store) }
                    else { JournalView(store: store) }
                }.padding(.horizontal, 20).padding(.top, 16).padding(.bottom, 24)
            }.scrollDismissesKeyboard(.interactively)
            dock
        }
        .background(Ink.background.ignoresSafeArea()).foregroundStyle(Ink.ivory)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("instrument-root")
        .sheet(isPresented: $pairing) { PairingView(store: store) }
        .sheet(isPresented: $editing) { OutlineEditor(store: store, outline: outline) }
        .onChange(of: outline.dirty) { _, value in store.draftDirty = value }
    }
    private var header: some View {
        HStack(spacing: 10) {
            HStack(alignment: .bottom, spacing: 4) { Rectangle().frame(width: 4, height: 22); Rectangle().frame(width: 4, height: 15) }.foregroundStyle(Ink.lime).accessibilityHidden(true)
            Text("threshold").font(.system(size: 23, weight: .semibold)).tracking(-1)
            Text(".").font(.system(size: 25, weight: .bold)).foregroundStyle(Ink.lime).padding(.leading, -10)
            Spacer()
            Button { pairing = true } label: {
                HStack(spacing: 6) { Circle().fill(store.fresh ? Ink.lime : Ink.amber).frame(width: 5,height: 5); Text(store.paired ? "SERVER" : "CONNECT").font(.caption2.monospaced().weight(.semibold)).tracking(0.5).lineLimit(1).minimumScaleFactor(0.6) }.frame(minWidth: 86, minHeight: 44)
            }.foregroundStyle(Ink.ivory).accessibilityIdentifier("connect")
        }.padding(.horizontal, 22).padding(.top, 6).padding(.bottom, 5).background(Ink.background)
            .overlay(alignment: .bottom) { Rectangle().fill(Ink.rule).frame(height: 1).padding(.horizontal, 22) }
    }
    private var title: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Micro(text: "FIELD INSTRUMENT / 01")
                Spacer()
                Text(store.state?.sourceMode.rawValue ?? "OFFLINE").font(.caption2.monospaced().weight(.bold)).tracking(0.8).padding(.horizontal, 7).padding(.vertical, 4).overlay(Rectangle().stroke(Ink.amber, lineWidth: 1)).foregroundStyle(Ink.amber).accessibilityIdentifier("source-mode")
            }
            Text(store.state?.spatial.zone?.name ?? store.state?.areaName ?? "A quieter watch.")
                .font(.system(.title, design: .serif)).tracking(-0.6).fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("area-name")
            Text(modeCopy).font(.footnote).foregroundStyle(Ink.muted).lineSpacing(2).fixedSize(horizontal: false, vertical: true)
        }
    }
    private var modeCopy: String {
        switch store.state?.sourceMode {
        case .test: return "TEST · Synthetic data and actor. No physical motion evidence."
        case .live: return "LIVE · Radio motion only. Exact-zone localization unavailable."
        case .replay: return "REPLAY · Recorded data. Not live monitoring or person tracking."
        case nil: return "Private, local control. No connection is not an all-clear."
        }
    }
    private var boundary: some View {
        VStack(alignment: .leading, spacing: 13) {
            ZStack {
                if outline.mode == .ar { ARFloorView(outline: outline) }
                else { PlanField(outline: outline, saved: store.state?.spatial.zone, actor: store.state?.spatial.position) }
                // This visual reticle shares the existing raycast's viewport centre.
                // No AR world coordinates, nodes, or session settings change here.
                if outline.mode == .ar {
                    Image(systemName: "plus").font(.system(size: 26, weight: .ultraLight))
                        .foregroundStyle(outline.floorFound ? Ink.lime : Ink.ivory).frame(width: 50, height: 50)
                        .overlay(Circle().stroke(outline.floorFound ? Ink.lime : Ink.ivory.opacity(0.5)))
                        .allowsHitTesting(false).accessibilityHidden(true)
                }
                VStack(alignment: .leading, spacing: 0) {
                    HStack(alignment: .top, spacing: 12) {
                        Micro(text: "01 / GEOMETRY", color: Ink.ivory)
                        Spacer(minLength: 4)
                        Micro(text: geometryLabel, color: outline.mode == .ar ? Ink.lime : Ink.amber)
                            .multilineTextAlignment(.trailing).accessibilityIdentifier("geometry-provenance")
                    }.padding(13).background(Ink.background.opacity(0.88))
                    Spacer()
                    if outline.mode == .idle && store.state?.spatial.zone == nil {
                        VStack(alignment: .leading, spacing: 12) {
                            if !typeSize.isAccessibilitySize {
                                Text("Set the\nperimeter.").font(.system(.largeTitle, design: .serif)).tracking(-0.8)
                                Rectangle().fill(Ink.lime).frame(width: 32, height: 2)
                            }
                            Text(outline.supported ? "Find the floor. Mark its corners." : "AR unavailable in Simulator.\nUse a clearly labelled TEST plan.")
                                .font(.footnote).foregroundStyle(Ink.muted).lineSpacing(2).accessibilityIdentifier("ar-unavailable")
                        }.padding(.horizontal, 22)
                    }
                    Spacer()
                    HStack(alignment: .bottom, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(geometryCaption).font(.caption).foregroundStyle(Ink.muted)
                            Text(geometryUnits).font(.caption).foregroundStyle(Ink.lime)
                        }.fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 0)
                        VStack(alignment: .trailing, spacing: 1) {
                            Text(String(format: "%02d", geometryCornerCount)).font(.title2.monospaced().weight(.light))
                            Text("corners").font(.caption2).foregroundStyle(Ink.muted)
                        }.accessibilityElement(children: .ignore).accessibilityLabel("\(geometryCornerCount) corners").accessibilityIdentifier("corner-count")
                    }.padding(13).background(Ink.background.opacity(0.9))
                }.allowsHitTesting(false)
            }
            .frame(height: typeSize.isAccessibilitySize ? 400 : 310)
            .background(Ink.field).overlay(Rectangle().stroke(Ink.rule, lineWidth: 1)).clipped()
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("spatial-stage")
            HStack(spacing: 8) {
                if outline.mode == .ar {
                    Button("Add floor corner") { outline.addFloorCorner() }.buttonStyle(InstrumentButton(primary: true)).disabled(outline.publishPending || !outline.floorFound || !outline.trackingReady).accessibilityIdentifier("add-floor-corner")
                    Button("Stop AR") { outline.stop() }.buttonStyle(InstrumentButton(danger: true)).accessibilityIdentifier("stop-ar")
                } else {
                    Button { Task { await outline.startAR() } } label: { Label(outline.invalidated ? "Retry AR" : "Start AR",systemImage: "viewfinder") }.buttonStyle(InstrumentButton()).accessibilityIdentifier("start-ar")
                    Button("TEST plan") { outline.startTestPlan() }.buttonStyle(InstrumentButton()).accessibilityIdentifier("test-plan")
                }
            }
            Text(outline.message).font(.footnote).foregroundStyle(outline.invalidated ? Ink.amber : Ink.muted).fixedSize(horizontal: false,vertical: true).accessibilityIdentifier("outline-status")
            HStack {
                Button { editing = true } label: { Text("Outline & target"); Image(systemName: "arrow.up.right") }.font(.subheadline.weight(.medium)).foregroundStyle(Ink.lime).frame(minHeight: 44).accessibilityIdentifier("outline-editor")
                Spacer()
                if outline.dirty { Micro(text: "UNSAVED", color: Ink.amber) }
            }
            if store.state?.sourceMode == .test {
                DisclosureGroup("TEST actor · synthetic only", isExpanded: $showTestActions) {
                    HStack(spacing: 8) {
                        Button("Walk through") { Task { await store.command(.testWalk) } }.buttonStyle(InstrumentButton()).disabled(!store.allowed(.testWalk)).accessibilityIdentifier("test-walk")
                        Button("Reset outside") { Task { await store.command(.resetActor) } }.buttonStyle(InstrumentButton()).disabled(!store.allowed(.resetActor)).accessibilityIdentifier("reset-actor")
                    }.padding(.top, 10)
                    Text("Actor: \(store.state?.spatial.testActor.phase ?? "unavailable"). Never the phone or a measured person.").font(.footnote).foregroundStyle(Ink.muted).padding(.top, 8)
                }.font(.subheadline).tint(Ink.lime).accessibilityElement(children: .contain).accessibilityIdentifier("test-tools")
            }
            if let s = store.state { Text(s.spatial.localization.detail).font(.footnote).foregroundStyle(Ink.muted) }
        }
    }
    private var geometryLabel: String {
        if outline.mode == .ar { return "ARKIT DRAFT" }
        if outline.mode == .testPlan { return "TEST DRAFT" }
        guard let zone = store.state?.spatial.zone else { return "NO OUTLINE" }
        if zone.coordinateSpace == "test-plan" { return "SAVED TEST PLAN" }
        return zone.coordinateSpace == "arkit-world" ? "SAVED AR PLAN" : "SAVED GEOMETRY"
    }
    private var geometryCornerCount: Int {
        outline.mode == .idle ? (store.state?.spatial.zone?.vertices.count ?? 0) : outline.draft.vertices.count
    }
    private var geometryCaption: String {
        if outline.mode == .ar { return "Frame \(outline.frameLabel)" }
        if outline.mode == .testPlan { return "Local draft · not camera-registered" }
        return store.state?.spatial.zone == nil ? "Camera stays on this iPhone" : "Saved · not camera-registered"
    }
    private var geometryUnits: String {
        if outline.mode == .testPlan || (outline.mode == .idle && store.state?.spatial.zone?.coordinateSpace == "test-plan") { return "Unmeasured TEST units · not coverage" }
        return outline.mode == .ar ? "Estimated metres · not radio coverage" : "Geometry is not radio coverage"
    }
    private func errorStrip(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Rectangle().fill(Ink.amber).frame(width: 2)
            VStack(alignment: .leading, spacing: 8) {
                Text(message).font(.subheadline).foregroundStyle(Ink.amber).fixedSize(horizontal: false,vertical: true)
                Button("Retry state") { localError = nil; store.dismissCommandError(); Task { await store.refresh() } }.font(.subheadline.weight(.semibold)).frame(minHeight: 44).foregroundStyle(Ink.ivory).disabled(!store.paired || store.pending || store.refreshing).accessibilityIdentifier("retry-state")
            }
        }.fixedSize(horizontal: false, vertical: true).accessibilityElement(children: .contain).accessibilityIdentifier("connection-error")
    }
    private var alarmTone: Color {
        if store.state?.alarmState == .alarm { return Ink.alarm }
        if !store.fresh { return Ink.amber }
        return store.state?.alarmState == .armed ? Ink.lime : Ink.ivory
    }
    private var controlLayout: AnyLayout {
        typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(spacing: 8)) : AnyLayout(HStackLayout(spacing: 8))
    }
    private var dock: some View {
        VStack(spacing: 8) {
            VStack(alignment: .leading, spacing: 4) {
                let statusLayout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .leading, spacing: 4)) : AnyLayout(HStackLayout(alignment: .firstTextBaseline, spacing: 12))
                statusLayout {
                    HStack(alignment: .firstTextBaseline, spacing: 7) {
                        if !store.fresh && store.state != nil { Micro(text: "LAST KNOWN", color: Ink.amber).accessibilityIdentifier("state-last-known") }
                        Text(store.state?.alarmState.rawValue ?? "UNKNOWN")
                            .font(.title3.monospaced().weight(.semibold)).foregroundStyle(alarmTone)
                            .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("alarm-state")
                    }
                    if !typeSize.isAccessibilitySize { Spacer(minLength: 0) }
                    Micro(text: store.state.map { $0.sourceMode.rawValue + ($0.sourceMode == .test ? " / SYNTHETIC" : $0.sourceMode == .replay ? " / RECORDED" : " / RADIO") } ?? "OFFLINE / UNKNOWN", color: Ink.amber).lineLimit(1).minimumScaleFactor(0.75)
                }
                Text(store.fresh ? (store.healthy ? (typeSize.isAccessibilitySize ? "Stream healthy" : "Backend stream healthy") : store.healthLabel) : "State unavailable · not an all-clear")
                    .font(.caption).foregroundStyle(store.fresh && store.healthy ? Ink.muted : Ink.amber)
                    .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("dock-observation-status")
            }.frame(maxWidth: .infinity, alignment: .leading).accessibilityElement(children: .contain).accessibilityIdentifier("alarm-dock")
            if store.state?.alarmState == .alarm {
                VStack(alignment: .leading, spacing: 9) {
                    Text(store.state?.sourceMode == .test ? "TEST ALERT · synthetic event" : "MOTION ALERT · not a confirmed crossing").font(.footnote.weight(.semibold)).foregroundStyle(Ink.alarm).fixedSize(horizontal: false, vertical: true)
                    controlLayout {
                        Button("Acknowledge") { Task { await store.command(.acknowledge) } }.buttonStyle(InstrumentButton(primary: true,danger: true)).disabled(!store.allowed(.acknowledge)).accessibilityIdentifier("acknowledge")
                        Button("Disarm") { Task { await store.command(.disarm) } }.buttonStyle(InstrumentButton()).disabled(!store.allowed(.disarm)).accessibilityIdentifier("disarm")
                    }
                }
            } else {
                controlLayout {
                    if store.state?.alarmState == .armed || store.state?.alarmState == .calibrating {
                        Button("Disarm") { Task { await store.command(.disarm) } }.buttonStyle(InstrumentButton(primary: true)).disabled(!store.allowed(.disarm)).accessibilityIdentifier("disarm")
                    } else {
                        Button("Calibrate") { Task { await store.command(.calibrate) } }.buttonStyle(InstrumentButton()).disabled(!store.allowed(.calibrate)).accessibilityIdentifier("calibrate")
                        Button { Task { await store.command(.arm) } } label: { HStack { Text("Arm watch"); Image(systemName: "arrow.up.right") } }.buttonStyle(InstrumentButton(primary: true)).disabled(!store.allowed(.arm)).accessibilityIdentifier("arm")
                    }
                    if store.pending { ProgressView().tint(Ink.lime).accessibilityLabel("Waiting for backend confirmation") }
                }
            }
            if ControlPresentation.offersSafeDisarm(paired: store.paired, cachedAlarm: store.state?.alarmState),
               store.state?.alarmState != .armed && store.state?.alarmState != .alarm && store.state?.alarmState != .calibrating {
                Button(typeSize.isAccessibilitySize ? "Try Disarm" : "Disarm · safe attempt") { Task { await store.command(.disarm) } }
                    .buttonStyle(InstrumentButton(danger: true)).disabled(!store.allowed(.disarm))
                    .accessibilityLabel("Disarm · safe attempt").accessibilityHint("The backend must confirm the result.").accessibilityIdentifier("safe-disarm")
            }
            if store.state?.alarmState == .calibrating {
                ProgressView(value: store.state?.calibration.progress ?? 0).tint(Ink.lime)
                Text("Keep the area empty · \(Int((store.state?.calibration.progress ?? 0)*100))%").font(.footnote).foregroundStyle(Ink.muted)
            }
            HStack(spacing: 0) {
                dockTab("Boundary", symbol: "viewfinder", index: 0)
                dockTab("Signal", symbol: "waveform.path", index: 1)
                dockTab("Journal", symbol: "list.bullet", index: 2)
            }
        }.padding(.horizontal, 20).padding(.top, 12).padding(.bottom, 4).background(Ink.background)
            .overlay(alignment: .top) { Rectangle().fill(store.state?.alarmState == .alarm ? Ink.alarm : Ink.rule).frame(height: store.state?.alarmState == .alarm ? 2 : 1) }
    }
    private func dockTab(_ name: String, symbol: String, index: Int) -> some View {
        Button { tab = index } label: {
            VStack(spacing: 4) {
                Image(systemName: symbol).font(.system(size: 15, weight: tab == index ? .semibold : .regular))
                Text(name).font(.caption.weight(tab == index ? .semibold : .regular)).lineLimit(1).minimumScaleFactor(0.5)
            }.padding(.vertical, 6).frame(maxWidth: .infinity, minHeight: 44).fixedSize(horizontal: false, vertical: true).contentShape(Rectangle())
        }.foregroundStyle(tab == index ? Ink.lime : Ink.muted).accessibilityIdentifier("tab-\(name.lowercased())").accessibilityAddTraits(tab == index ? .isSelected : [])
    }
}

/// Display-only fit for a saved plan. It never edits vertices or AR transforms.
struct SavedPlanProjection {
    let plotRect: CGRect
    private let centreX: Double
    private let centreZ: Double
    private let scale: Double
    init?(vertices: [Vertex], size: CGSize, topInset: CGFloat = 58, bottomInset: CGFloat = 88) {
        guard !vertices.isEmpty, vertices.allSatisfy({ $0.x.isFinite && $0.z.isFinite }),
              size.width.isFinite, size.height.isFinite, topInset.isFinite, bottomInset.isFinite,
              topInset >= 0, bottomInset >= 0, size.width > 56, size.height > topInset + bottomInset else { return nil }
        let xs = vertices.map(\.x), zs = vertices.map(\.z)
        let minX = xs.min()!, maxX = xs.max()!, minZ = zs.min()!, maxZ = zs.max()!
        let spanX = maxX - minX, spanZ = maxZ - minZ
        guard spanX.isFinite, spanZ.isFinite else { return nil }
        plotRect = CGRect(x: 28, y: topInset, width: size.width - 56, height: size.height - topInset - bottomInset)
        centreX = minX / 2 + maxX / 2; centreZ = minZ / 2 + maxZ / 2
        scale = min(Double(plotRect.width) / max(spanX, 0.01), Double(plotRect.height) / max(spanZ, 0.01)) * 0.82
    }
    func point(_ vertex: Vertex) -> CGPoint {
        CGPoint(x: Double(plotRect.midX) + (vertex.x - centreX) * scale,
                y: Double(plotRect.midY) + (vertex.z - centreZ) * scale)
    }
}

private struct PlanField: View {
    @Environment(\.dynamicTypeSize) private var typeSize
    @ObservedObject var outline: FloorOutline
    let saved: Zone?
    let actor: Position?
    var body: some View {
        GeometryReader { geometry in
            let vertices = outline.mode == .testPlan ? outline.draft.vertices : saved?.vertices ?? []
            let xs = vertices.map(\.x), zs = vertices.map(\.z)
            let minX = min(0,xs.min() ?? 0)-1, maxX = max(10,xs.max() ?? 10)+1
            let minZ = min(0,zs.min() ?? 0)-1, maxZ = max(10,zs.max() ?? 10)+1
            // The active TEST editor keeps its existing coordinate mapping exactly.
            let savedProjection = outline.mode == .idle ? SavedPlanProjection(vertices: vertices, size: geometry.size, topInset: typeSize.isAccessibilitySize ? 120 : 58, bottomInset: typeSize.isAccessibilitySize ? 160 : 88) : nil
            let map: (Vertex) -> CGPoint = { p in
                if let savedProjection { return savedProjection.point(p) }
                return CGPoint(x: 20 + (p.x-minX)/(maxX-minX)*(geometry.size.width-40),y: 55 + (p.z-minZ)/(maxZ-minZ)*(geometry.size.height-135))
            }
            Canvas { ctx, size in
                for n in 0...12 {
                    let x = Double(n)/12*size.width, y = Double(n)/12*size.height
                    var grid = Path(); grid.move(to: CGPoint(x: x,y: 0)); grid.addLine(to: CGPoint(x: x,y: size.height)); grid.move(to: CGPoint(x: 0,y: y)); grid.addLine(to: CGPoint(x: size.width,y: y))
                    ctx.stroke(grid,with: .color(Ink.rule.opacity(n%3 == 0 ? 0.45 : 0.2)),lineWidth: 0.5)
                }
                if !vertices.isEmpty && (outline.mode == .testPlan || savedProjection != nil) {
                    var shape = Path(); shape.move(to: map(vertices[0])); for p in vertices.dropFirst() { shape.addLine(to: map(p)) }; if vertices.count >= 3 { shape.closeSubpath() }
                    ctx.fill(shape,with: .color(Ink.lime.opacity(0.08)))
                    ctx.stroke(shape,with: .color(Ink.lime),style: StrokeStyle(lineWidth: 1.5,dash: outline.mode == .testPlan ? [] : [4,5]))
                    for (index,p) in vertices.enumerated() {
                        let point = map(p)
                        ctx.fill(Path(ellipseIn: CGRect(x: point.x-3,y: point.y-3,width: 6,height: 6)),with: .color(Ink.lime))
                        ctx.draw(Text(String(format: "%02d",index+1)).font(.system(size: 10,design: .monospaced)).foregroundColor(Ink.ivory),at: CGPoint(x: point.x+12,y: point.y-12))
                    }
                    // Only an explicitly tagged backend TEST actor, in this exact saved frame.
                    if outline.mode != .testPlan, let actor, actor.synthetic, actor.sourceMode == "TEST", actor.frameId == saved?.frameId {
                        let p = map(Vertex(x:actor.x,z:actor.z))
                        let visiblePlot = CGRect(x: 8, y: 46, width: size.width - 16, height: size.height - 124)
                        if p.x.isFinite && p.y.isFinite && visiblePlot.insetBy(dx: 9, dy: 9).contains(p) {
                            ctx.stroke(Path(ellipseIn: CGRect(x:p.x-8,y:p.y-8,width:16,height:16)),with:.color(Ink.amber),lineWidth:1)
                            ctx.fill(Path(ellipseIn:CGRect(x:p.x-3,y:p.y-3,width:6,height:6)),with:.color(Ink.amber))
                            ctx.draw(Text("TEST ACTOR").font(.system(size:10,design:.monospaced)).foregroundColor(Ink.amber),at:CGPoint(x:p.x,y:p.y+18))
                        } else {
                            // Do not clamp an off-plan actor into a false on-plan location.
                            ctx.draw(Text("TEST ACTOR OUTSIDE VIEW").font(.system(size:10,design:.monospaced)).foregroundColor(Ink.amber),at:CGPoint(x:size.width/2,y:(savedProjection?.plotRect.minY ?? 58) + 11))
                        }
                    }
                }
            }
            .contentShape(Rectangle())
            .onTapGesture { p in
                guard outline.mode == .testPlan, p.y > 50, p.y < geometry.size.height-75 else { return }
                outline.addTest(Vertex(x: minX+(p.x-20)/(geometry.size.width-40)*(maxX-minX),z:minZ+(p.y-55)/(geometry.size.height-135)*(maxZ-minZ)))
            }
        }.accessibilityElement(children: .ignore).accessibilityLabel(outline.mode == .testPlan ? "TEST plan editor. \(outline.draft.vertices.count) corners. Use Outline and target to enter coordinates accessibly." : "Saved geometry plan. Not AR registration, RF coverage, or person tracking.")
    }
}

private struct PairingView: View {
    @ObservedObject var store: InstrumentStore
    @State private var token = ""
    @State private var approveLAN = false
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading,spacing: 22) {
                    Micro(text: "PRIVATE CONNECTION / BEARER AUTH")
                    Text("One signal.\nOne shared watch.").font(.system(.largeTitle, design: .serif)).tracking(-1)
                    Text("Use the server origin and control token supplied by your local THRESHOLD operator. The token stays in memory and is cleared on disconnect or app exit.").font(.body).foregroundStyle(Ink.muted)
                    VStack(alignment: .leading,spacing: 8) {
                        Text("Server origin").font(.subheadline.weight(.semibold))
                        TextField("https://192.168.1.20:8765",text:$store.endpointText).textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL).padding(14).background(Ink.field).overlay(Rectangle().stroke(Ink.rule)).accessibilityIdentifier("server-origin")
                        Text("Literal private IPv4 only. On a real phone, 127.0.0.1 is the phone, not your Mac.").font(.footnote).foregroundStyle(Ink.muted)
                    }
                    VStack(alignment: .leading,spacing: 8) {
                        Text("Pair token").font(.subheadline.weight(.semibold))
                        SecureField("Never stored in preferences",text:$token).textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive().padding(14).background(Ink.field).overlay(Rectangle().stroke(Ink.rule)).accessibilityIdentifier("pair-token")
                    }
                    Toggle(isOn:$approveLAN) { Text("I approve plaintext HTTP on this trusted private LAN. Other network users may see the token.").font(.footnote).foregroundStyle(Ink.amber) }.tint(Ink.lime)
                    Text("This does not expose the Mac server or change trust settings. LAN binding needs operator approval. HTTPS needs a certificate already trusted by iOS. ATS may reject LAN HTTP; see IPHONE-SETUP.md.").font(.footnote).foregroundStyle(Ink.muted)
                    if let fault = store.fault { Text(fault).foregroundStyle(Ink.amber).font(.subheadline).accessibilityIdentifier("pair-error") }
                    Button(store.pending ? "Connecting…" : "Connect private server") {
                        let candidate = token; token = ""
                        Task { await store.connect(token:candidate,approveLAN:approveLAN); if store.paired { dismiss() } }
                    }.buttonStyle(InstrumentButton(primary:true)).disabled(store.pending || token.isEmpty).accessibilityIdentifier("pair-submit")
                    if store.paired {
                        Text("Disconnecting does not disarm the backend. Disarm first if you want to stop the watch.").font(.footnote).foregroundStyle(Ink.amber)
                        Button("Disconnect this iPhone") { store.disconnect(); token = ""; dismiss() }.buttonStyle(InstrumentButton(danger:true)).disabled(store.pending).accessibilityIdentifier("disconnect")
                    }
                }.padding(24)
            }.background(Ink.background).navigationTitle("Connection").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement:.topBarTrailing) { Button("Done") { token = ""; dismiss() }.foregroundStyle(Ink.lime) } }
        }.preferredColorScheme(.dark).presentationDragIndicator(.visible)
    }
}

private struct OutlineEditor: View {
    @ObservedObject var store: InstrumentStore
    @ObservedObject var outline: FloorOutline
    @State private var x = ""
    @State private var z = ""
    @State private var error: String?
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment:.leading,spacing:20) {
                    Micro(text:"GEOMETRY / NOT RADIO COVERAGE")
                    Text("Name the boundary.").font(.system(.title, design: .serif)).tracking(-0.8)
                    TextField("Boundary name",text:$outline.name).padding(14).background(Ink.field).overlay(Rectangle().stroke(Ink.rule)).accessibilityIdentifier("zone-name")
                    Text("\(outline.draft.vertices.count) / 16 corners · frame \(outline.frameLabel)\n\(outline.coordinateSpace == "test-plan" ? "TEST plan · unmeasured units" : "ARKit · estimated metres")").font(.footnote.monospaced()).foregroundStyle(Ink.muted)
                    if outline.mode == .testPlan {
                        HStack {
                            TextField("X",text:$x).keyboardType(.numbersAndPunctuation).accessibilityLabel("TEST corner X").padding(12).background(Ink.field)
                            TextField("Z",text:$z).keyboardType(.numbersAndPunctuation).accessibilityLabel("TEST corner Z").padding(12).background(Ink.field)
                            Button("Add") { if let a = Double(x),let b = Double(z),a.isFinite,b.isFinite { outline.addTest(Vertex(x:a,z:b)); x="";z="" } else { error="Enter finite X and Z coordinates." } }.frame(minWidth:44,minHeight:44).foregroundStyle(Ink.lime)
                        }
                        Button("Use 4-corner TEST example") { outline.exampleTestRectangle() }.buttonStyle(InstrumentButton()).accessibilityIdentifier("test-example")
                    }
                    HStack(spacing:8) {
                        Button("Undo corner") { outline.undo() }.buttonStyle(InstrumentButton()).disabled(outline.draft.vertices.isEmpty).accessibilityIdentifier("undo-corner")
                        Button("Discard local") { outline.stop(reason:"Local draft discarded. Backend outline unchanged.") }.buttonStyle(InstrumentButton()).accessibilityIdentifier("discard-draft")
                    }
                    if let error { Text(error).foregroundStyle(Ink.amber).font(.footnote) }
                    Button("Publish outline") {
                        Task {
                            do { if try await outline.publish(to: store) { dismiss() } else { error = outline.message } }
                            catch { self.error = error.localizedDescription }
                        }
                    }.buttonStyle(InstrumentButton(primary:true)).disabled(!outline.canPublish || !store.allowed(.setZone)).accessibilityIdentifier("publish-zone")
                    Text(store.notice).font(.footnote).foregroundStyle(Ink.muted).accessibilityIdentifier("command-notice")
                    Divider().overlay(Ink.rule)
                    Micro(text:"DETECTION TARGET")
                    Text("\(store.state?.spatial.target.rawValue ?? "Unavailable")").font(.system(.title2, design: .serif)).accessibilityIdentifier("target-value")
                    Button("Radio motion · broad activity") { Task { await store.command(.setTarget,extra:["target":"radio-motion"]) } }.buttonStyle(InstrumentButton()).disabled(!store.allowed(.setTarget)).accessibilityIdentifier("target-radio")
                    Button("TEST zone entry · simulated") { Task { await store.command(.setTarget,extra:["target":"zone-entry"]) } }.buttonStyle(InstrumentButton()).disabled(store.state?.sourceMode != .test || !store.allowed(.setTarget)).accessibilityIdentifier("target-zone")
                    Text("LIVE and REPLAY exact-zone detection is unavailable. A floor outline never validates radio coverage. Changing geometry or target invalidates calibration.").font(.footnote).foregroundStyle(Ink.amber)
                    Button("Clear saved backend outline") { Task { await store.command(.clearZone) } }.buttonStyle(InstrumentButton(danger:true)).disabled(!store.allowed(.clearZone)).accessibilityIdentifier("clear-zone")
                }.padding(24).disabled(outline.publishPending || store.pending)
            }.background(Ink.background).navigationTitle("Outline & target").navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement:.topBarTrailing) { Button("Done") { dismiss() }.foregroundStyle(Ink.lime).disabled(outline.publishPending || store.pending).accessibilityIdentifier("editor-done") } }
        }.preferredColorScheme(.dark).presentationDragIndicator(.visible).interactiveDismissDisabled(outline.publishPending || store.pending)
    }
}

private struct SignalView: View {
    @ScaledMetric(relativeTo: .largeTitle) private var scoreSize = 56.0
    @ObservedObject var store: InstrumentStore
    @State private var threshold = ""
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            Micro(text:"02 / ACTIVITY TRACE")
            HStack(alignment:.firstTextBaseline) {
                Text(store.state?.features.activityScore.map { String(format:"%.2f",$0) } ?? "—").font(.system(size:scoreSize,weight:.ultraLight,design:.monospaced)).minimumScaleFactor(0.5).lineLimit(1).accessibilityIdentifier("activity-score")
                Spacer()
                Text("ACTIVITY\nNOT OCCUPANCY").font(.caption2.monospaced()).foregroundStyle(Ink.muted).lineSpacing(4)
            }
            HStack {
                Micro(text: "RECENT SAMPLES")
                Spacer()
                Micro(text: "THRESHOLD \(String(format: "%.2f", store.state?.features.threshold ?? 0))", color: Ink.amber)
            }
            ActivityTrace(points:store.state?.graph ?? [],threshold:store.state?.features.threshold ?? 5).frame(height:120)
                .accessibilityLabel("Recent activity graph. Threshold \(store.state?.features.threshold ?? 0). Not occupancy or a probability.")
            line("Stream",store.healthLabel)
            line("Rate",String(format:"%.1f Hz",store.state?.health.packetRateHz ?? 0))
            line("Sample age",store.state?.health.sampleAgeS.map { String(format:"%.2f s",$0) } ?? "Unavailable")
            line("Valid / rejected","\(store.state?.health.validPackets ?? 0) / \(store.state?.health.invalidPackets ?? 0)")
            Text(store.state?.health.detail ?? "No backend response. Nothing is known about the entrance.").font(.footnote).foregroundStyle(Ink.amber)
            Divider().overlay(Ink.rule)
            Micro(text:"CALIBRATION / EMPTY AREA")
            Text(store.state?.calibration.detail ?? "Pair before calibration.").font(.subheadline).foregroundStyle(Ink.muted)
            Text(store.notice).font(.footnote).foregroundStyle(Ink.amber).accessibilityIdentifier("signal-notice")
            HStack {
                TextField("Threshold",text:$threshold).keyboardType(.decimalPad).padding(12).background(Ink.field).accessibilityIdentifier("threshold-input")
                Button("Set threshold") { if let number=Double(threshold),number.isFinite,(0.1...100).contains(number) { Task { await store.command(.setThreshold,extra:["threshold":number]) } } }.font(.footnote).foregroundStyle(Ink.lime).frame(minHeight:44).disabled(!store.allowed(.setThreshold)).accessibilityIdentifier("set-threshold")
            }
            Text("Current threshold: \(String(format:"%.2f",store.state?.features.threshold ?? 0)). Tuning value, not probability.").font(.footnote).foregroundStyle(Ink.muted)
            if store.state?.sourceMode == .test { Button("TEST radio disturbance") { Task { await store.command(.testMotion) } }.buttonStyle(InstrumentButton()).disabled(!store.allowed(.testMotion)).accessibilityIdentifier("test-motion") }
            SoundPanel(sound:store.sound,eventID:store.state?.activeEventId)
            Divider().overlay(Ink.rule)
            Micro(text:"PHONE CALLS / SERVER POLICY")
            Text((store.state?.calls.status ?? "Unavailable").uppercased()).font(.headline.monospaced()).foregroundStyle(Ink.amber).accessibilityIdentifier("call-status")
            Text(store.state?.calls.detail ?? "No provider is configured here.").font(.footnote).foregroundStyle(Ink.muted)
            Text("No call enable control. No background, lock-screen, or emergency alert guarantee. Keep this app open for local sound.").font(.footnote).foregroundStyle(Ink.amber)
        }
    }
    private func line(_ name:String,_ value:String)->some View {
        LabeledContent { Text(value).font(.footnote.monospaced()).multilineTextAlignment(.trailing).textSelection(.enabled) }
        label: { Text(name).font(.footnote).foregroundStyle(Ink.muted) }
            .fixedSize(horizontal: false, vertical: true)
    }
}
private struct SoundPanel: View {
    @ObservedObject var sound: AlarmSound
    let eventID: String?
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            Micro(text:"LOCAL IPHONE SOUND")
            Text(sound.status).font(.footnote).foregroundStyle(Ink.muted).accessibilityIdentifier("sound-status")
            HStack(spacing:8) {
                Button(sound.enabled ? "Disable sound" : "Enable sound") { sound.toggle() }.buttonStyle(InstrumentButton()).accessibilityIdentifier("sound-enable")
                Button("Test sound") { sound.test() }.buttonStyle(InstrumentButton()).accessibilityIdentifier("sound-test")
            }
            if eventID != nil { Button("Mute this event on this iPhone") { sound.mute(eventID:eventID) }.font(.footnote).frame(minHeight:44).foregroundStyle(Ink.amber) }
        }
    }
}
private struct ActivityTrace: View {
    let points:[GraphPoint]
    let threshold:Double
    var body:some View {
        Canvas { ctx,size in
            let values = Array(points.suffix(180)).compactMap(\.activityScore).filter { $0.isFinite && $0 >= 0 }
            let maxY=max(threshold*1.5,values.max() ?? 1,1)
            let y: (Double)->Double = { size.height-8-($0/maxY)*(size.height-16) }
            var limit=Path(); limit.move(to:CGPoint(x:0,y:y(threshold)));limit.addLine(to:CGPoint(x:size.width,y:y(threshold)))
            ctx.stroke(limit,with:.color(Ink.amber.opacity(0.6)),style:StrokeStyle(lineWidth:1,dash:[3,5]))
            guard values.count>1 else { return }
            var path=Path()
            for (i,v) in values.enumerated() { let p=CGPoint(x:Double(i)/Double(values.count-1)*size.width,y:y(v)); if i == 0 { path.move(to:p) } else { path.addLine(to:p) } }
            ctx.stroke(path,with:.color(Ink.lime),lineWidth:1.5)
        }.background(Ink.field)
    }
}
private struct JournalView: View {
    @ObservedObject var store: InstrumentStore
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Micro(text: "03 / SHARED EVENT JOURNAL")
            Text("Event journal").font(.system(.title2, design: .serif)).tracking(-0.4)
            if store.state?.events.isEmpty != false {
                Text("No events received. This is not evidence that the area is empty.")
                    .font(.body).foregroundStyle(Ink.muted).padding(.vertical, 16).accessibilityIdentifier("history-empty")
            }
            ForEach(Array((store.state?.events ?? []).prefix(100))) { event in JournalEventRow(event: event) }
            Text("The backend owns this history and alarm latch. Acknowledge or disarm affects every connected view.")
                .font(.footnote).foregroundStyle(Ink.muted).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct EventTimeDisplay {
    static func date(from raw: String) -> Date? {
        try? Date(raw, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: raw.contains(".")))
    }
}

private struct JournalEventRow: View {
    let event: AlarmEvent
    @State private var details = false
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                Micro(text: event.sourceMode.rawValue, color: Ink.amber)
                Spacer(minLength: 8)
                Text(event.acknowledged ? "Acknowledged" : "Unacknowledged")
                    .font(.caption.weight(.medium)).foregroundStyle(event.acknowledged ? Ink.muted : Ink.alarm)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Text(event.type == "zone_entry" ? (event.sourceMode == .test ? "Synthetic zone entry" : "Unverified zone entry") : "Motion near entrance")
                .font(.system(.title3, design: .serif)).fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("event-title")
            Text(event.areaName).font(.subheadline).fixedSize(horizontal: false, vertical: true)
            if let date = EventTimeDisplay.date(from: event.occurredAt) {
                Text(date, format: .dateTime.month(.abbreviated).day().hour().minute()).font(.footnote.monospaced()).foregroundStyle(Ink.muted)
            } else { Text(event.occurredAt).font(.footnote.monospaced()).foregroundStyle(Ink.muted).textSelection(.enabled) }
            Text(event.sourceMode == .test ? "TEST · synthetic event, not a measured person. Exact-zone not verified." : event.sourceMode == .replay ? "Recorded event · not live. Exact-zone not verified." : "Radio motion only · not a confirmed crossing.")
                .font(.footnote).foregroundStyle(Ink.amber).fixedSize(horizontal: false, vertical: true)
            DisclosureGroup("Record details", isExpanded: $details) {
                VStack(alignment: .leading, spacing: 8) {
                    Text("UTC \(event.occurredAt)")
                    Text("Event \(event.eventId)")
                    Text("Score \(String(format: "%.2f", event.activityScore)) / \(String(format: "%.2f", event.threshold)) · Call \(event.callStatus)")
                    if let spatial = event.spatial {
                        Text("Zone \(spatial.zoneId ?? "unknown") · revision \(spatial.zoneRevision ?? 0)")
                        Text("Frame \(spatial.frameId ?? "unknown")")
                        Text("Provenance \(spatial.provenance ?? "unavailable")")
                    }
                    if let detail = event.callDetail { Text(detail) }
                }.font(.footnote.monospaced()).foregroundStyle(Ink.muted).fixedSize(horizontal: false, vertical: true).textSelection(.enabled).padding(.top, 8)
            }.font(.footnote.weight(.medium)).tint(Ink.lime).accessibilityIdentifier("event-details")
        }.padding(16).background(Ink.field).overlay(Rectangle().stroke(Ink.rule, lineWidth: 1)).accessibilityElement(children: .contain)
    }
}
