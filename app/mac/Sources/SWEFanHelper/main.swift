import Darwin
import Foundation
import SWECore

// The root fan helper on its own (see FanHelper): only SWECore, so it
// links nothing a copy outside SWE.app can't find. The app's own
// executable needs Sparkle.framework from the bundle, and a copy of it in
// /Library/PrivilegedHelperTools died in dyld before main on every launch.
guard let status = FanHelper.runIfRequested() else {
    FileHandle.standardError.write(Data("usage: SWEFanHelper \(FanHelper.flag) --control <file> | \(FanHelper.surveyFlag)\n".utf8))
    exit(64)
}
exit(status)
