import SwiftUI
import Vision
import VisionKit

/// Scans a `swe remote pair --app` QR in the app, so pairing doesn't
/// go through the Camera app and a deep link.
struct QRScannerSheet: View {
    var onScan: (URL) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var rejected = false

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    ScannerView { payload in
                        if let url = URL(string: payload), PairLink(url) != nil {
                            onScan(url)
                        } else {
                            rejected = true
                        }
                    }
                    .ignoresSafeArea(edges: .bottom)
                    .overlay(alignment: .bottom) {
                        Text(rejected ? "That QR isn't a SWE pair code." : "Point at the pair code from SWE › Settings › Remote")
                            .font(Theme.caption)
                            .foregroundStyle(.white)
                            .padding(.horizontal, 14).padding(.vertical, 8)
                            .background(.black.opacity(0.6), in: .capsule)
                            .padding(.bottom, 32)
                    }
                } else {
                    StateCard(icon: "camera", title: "Camera unavailable",
                              message: "Allow camera access in Settings, or pair with Tailscale instead.")
                        .padding(Theme.gutter)
                    Spacer()
                }
            }
            .background(Theme.wash)
            .navigationTitle("Scan pair QR")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
        }
    }
}

private struct ScannerView: UIViewControllerRepresentable {
    var onPayload: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                           qualityLevel: .balanced, isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        try? vc.startScanning()
        return vc
    }

    func updateUIViewController(_ vc: DataScannerViewController, context: Context) {}

    func makeCoordinator() -> Coordinator { Coordinator(onPayload: onPayload) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onPayload: (String) -> Void
        init(onPayload: @escaping (String) -> Void) { self.onPayload = onPayload }

        func dataScanner(_ scanner: DataScannerViewController, didAdd items: [RecognizedItem], allItems: [RecognizedItem]) {
            for item in items {
                if case .barcode(let code) = item, let payload = code.payloadStringValue {
                    onPayload(payload)
                    return
                }
            }
        }
    }
}
