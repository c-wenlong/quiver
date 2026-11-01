import Darwin
import SWECore
import SwiftUI

// `SWE --fan-daemon …` is the root fan helper (see FanHelper), started
// through the administrator prompt, and `SWE --fan-survey` prints the
// SMC's fan keys; neither reaches the app.
if let status = FanHelper.runIfRequested() { exit(status) }
SWEApp.main()
