import Darwin
import QuiverCore
import SwiftUI

// `Quiver --fan-hold …` is the root fan helper (see FanHelper), started
// through the administrator prompt; it never reaches the app.
if let status = FanHelper.runIfRequested() { exit(status) }
QuiverApp.main()
