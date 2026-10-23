import Darwin
import QuiverCore
import SwiftUI

// `Quiver --fan-daemon …` is the root fan helper (see FanHelper), started
// through the administrator prompt, and `Quiver --fan-survey` prints the
// SMC's fan keys; neither reaches the app.
if let status = FanHelper.runIfRequested() { exit(status) }
QuiverApp.main()
