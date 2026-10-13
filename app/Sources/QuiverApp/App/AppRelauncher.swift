import AppKit
import Foundation

/// Replaces the running Quiver.app with a freshly built copy, then quits.
///
/// The swap has to happen after this process exits, so the work lives in a
/// detached shell script: it waits for our pid to die, moves the installed
/// app to the Trash (recoverable), copies the build in, unregisters the
/// Trash copy with LaunchServices, and opens the new app. Output lands in
/// ~/.quiver/cache/relaunch.log.
enum AppRelauncher {

    /// The detached script. $1 = our pid, $2 = built app path, $3 = the
    /// installed app's path (Bundle.main.bundleURL).
    private static let script = """
        #!/bin/sh
        pid="$1"; src="$2"; dest="$3"
        i=0
        while kill -0 "$pid" 2>/dev/null; do
          i=$((i + 1))
          if [ "$i" -gt 600 ]; then
            echo "quiver relauncher: pid $pid still alive after 60s, giving up" >&2
            exit 1
          fi
          sleep 0.1 2>/dev/null || sleep 1
        done
        if [ "$src" = "$dest" ]; then
          open "$dest"
          exit 0
        fi
        stamp="$(date +%Y%m%d-%H%M%S)"
        trash="$HOME/.Trash/$(basename "$dest" .app) $stamp.app"
        if ! mv "$dest" "$trash"; then
          echo "quiver relauncher: could not move $dest to Trash" >&2
          exit 1
        fi
        if ! ditto "$src" "$dest"; then
          echo "quiver relauncher: ditto failed, restoring the old app" >&2
          mv "$trash" "$dest"
          exit 1
        fi
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$trash"
        open "$dest"
        """

    /// Start the detached swap and quit. Call only after a build exists at
    /// `builtAppURL`; nothing here verifies it.
    @MainActor
    static func relaunch(into builtAppURL: URL) {
        let fm = FileManager.default
        let log = fm.homeDirectoryForCurrentUser
            .appending(path: ".quiver/cache/relaunch.log")
        try? fm.createDirectory(at: log.deletingLastPathComponent(),
                                withIntermediateDirectories: true)
        let scriptURL = fm.temporaryDirectory
            .appending(path: "quiver-relaunch-\(UUID().uuidString).sh")
        do {
            try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        } catch {
            NSLog("quiver relauncher: could not write script: \(error)")
            return
        }
        let pid = ProcessInfo.processInfo.processIdentifier
        let args = [String(pid), builtAppURL.path, Bundle.main.bundleURL.path]
        // nohup plus & detaches the script from our process group so it
        // outlives the terminate below.
        let command = "nohup /bin/sh \"$0\" \"$@\" >>\"\(log.path)\" 2>&1 &"
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", command, scriptURL.path] + args
        do {
            try task.run()
        } catch {
            NSLog("quiver relauncher: could not start script: \(error)")
            return
        }
        NSApp.terminate(nil)
    }
}
