// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "ThresholdDomain", platforms: [.macOS(.v13), .iOS(.v17)], products: [.library(name: "ThresholdDomain", targets: ["ThresholdDomain"])], targets: [.target(name: "ThresholdDomain"), .testTarget(name: "ThresholdDomainTests", dependencies: ["ThresholdDomain"])])
