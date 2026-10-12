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
        // The widget views, shared by the extension that draws them and the
        // app's Desktop widgets previews.
        .target(name: "QuiverWidgetsUI", dependencies: ["QuiverCore"]),
        // The SwiftUI shell. Kept thin so behaviour lives in QuiverCore.
        .executableTarget(name: "QuiverApp", dependencies: ["QuiverCore", "QuiverWidgetsUI"]),
        // The WidgetKit extension. SwiftPM builds it as a plain executable;
        // script/build-app.sh wraps it in an .appex inside Quiver.app. The
        // entry point must be _NSExtensionMain (which Xcode sets on extension
        // targets): without it the Swift main returns at launch and the
        // extension exits before chronod can read its widgets.
        .executableTarget(
            name: "QuiverWidgets",
            dependencies: ["QuiverCore", "QuiverWidgetsUI"],
            linkerSettings: [.unsafeFlags(["-Xlinker", "-e", "-Xlinker", "_NSExtensionMain"])]
        ),
        .testTarget(
            name: "QuiverCoreTests",
            dependencies: ["QuiverCore"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
