// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "Quiver",
    platforms: [.macOS(.v26)],
    products: [
        .executable(name: "Quiver", targets: ["QuiverApp"]),
        .executable(name: "QuiverWidgets", targets: ["QuiverWidgets"]),
    ],
    targets: [
        // Everything testable: models, the swe client, scanners, view models.
        .target(name: "QuiverCore", resources: [.process("Resources")]),
        // The SwiftUI shell. Kept thin so behaviour lives in QuiverCore.
        .executableTarget(name: "QuiverApp", dependencies: ["QuiverCore"]),
        // The WidgetKit extension. SwiftPM builds it as a plain executable;
        // script/build-app.sh wraps it in an .appex inside Quiver.app.
        .executableTarget(name: "QuiverWidgets", dependencies: ["QuiverCore"]),
        .testTarget(
            name: "QuiverCoreTests",
            dependencies: ["QuiverCore"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
