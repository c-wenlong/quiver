import Darwin
import Foundation
import QuiverCore

// The root fan helper on its own (see FanHelper): only QuiverCore, so it
// links nothing a copy outside Quiver.app can't find. The app's own
// executable needs Sparkle.framework from the bundle, and a copy of it in
// /Library/PrivilegedHelperTools died in dyld before main on every launch.
guard let status = FanHelper.runIfRequested() else {
    FileHandle.standardError.write(Data("usage: QuiverFanHelper \(FanHelper.flag) --control <file> | \(FanHelper.surveyFlag)\n".utf8))
    exit(64)
}
exit(status)
