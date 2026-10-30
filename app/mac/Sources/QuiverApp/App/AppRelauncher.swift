import AppKit
import Foundation

/// Replaces the running Quiver.app with a freshly built copy, then quits.
///
/// The swap has to happen after this process exits, so the work lives in a
/// detached shell script: it waits for our pid to die, copies the build
/// next to the installed app, swaps them only when the copy is good,
/// unregisters the Trash copy with LaunchServices, and opens the new app.
/// Every failure path reopens the old app. Output lands in
/// ~/.quiver/cache/relaunch.log.
enum AppRelauncher {

    /// The detached script. $1 = our pid, $2 = built app path, $3 = the
    /// installed app's path (Bundle.main.bundleURL).
    private static let script = """
        #!/bin/sh
        pid="$1"; src="$2"; dest="$3"
        # sh already has this file open; nothing reads it again.
        rm -f "$0"
        i=0
        while kill -0 "$pid" 2>/dev/null; do
          i=$((i + 1))
          if [ "$i" -gt 600 ]; then
            echo "quiver relauncher: pid $pid still alive after 60s, giving up" >&2
            exit 1
          fi
          sleep 0.1 2>/dev/null || sleep 1
        done
        # Take ownership of the build folder while we copy: a prune in
        # another running copy must not delete it under us.
        src_dir="$(dirname "$src")"
        case "$src_dir" in
          "$HOME/.quiver/cache/rebuild/"*)
            case "${src_dir#"$HOME/.quiver/cache/rebuild/"}" in
              ""|*/*) ;;
              *) echo $$ > "$src_dir/owner.pid" ;;
            esac ;;
        esac
        if [ "$src" = "$dest" ]; then
          open "$dest"
          exit 0
        fi
        stamp="$(date +%Y%m%d-%H%M%S)"
        trash="$HOME/.Trash/$(basename "$dest" .app) $stamp.app"
        new="$(dirname "$dest")/$(basename "$dest" .app).new-$stamp.app"
        # Copy next to the destination first: a failed copy leaves the
        # installed app untouched.
        if ! ditto "$src" "$new"; then
          echo "quiver relauncher: could not copy $src" >&2
          rm -rf "$new"
          open "$dest"
          exit 1
        fi
        if ! mv "$dest" "$trash"; then
          echo "quiver relauncher: could not move $dest to Trash" >&2
          rm -rf "$new"
          open "$dest"
          exit 1
        fi
        if ! mv "$new" "$dest"; then
          echo "quiver relauncher: could not place the new app, restoring" >&2
          mv "$trash" "$dest"
          rm -rf "$new"
          open "$dest"
          exit 1
        fi
        /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -u "$trash"
        # The build came from a per-build folder under the rebuild cache;
        # remove it, but only ever a direct child of that cache.
        case "$src_dir" in
          "$HOME/.quiver/cache/rebuild/"*)
            case "${src_dir#"$HOME/.quiver/cache/rebuild/"}" in
              ""|*/*) ;;
              *) rm -rf "$src_dir" ;;
            esac ;;
        esac
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
        // Every path is an argument, never part of the -c text, so a quote
        // or $ in one can't run as shell. nohup plus & detaches the script
        // from our process group so it outlives the terminate below; the
        // echo hands back its pid.
        let command = #"script="$1"; log="$2"; shift 2; nohup /bin/sh "$script" "$@" >>"$log" 2>&1 & echo $!"#
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/bin/sh")
        task.arguments = ["-c", command, "sh", scriptURL.path, log.path,
                          String(pid), builtAppURL.path, Bundle.main.bundleURL.path]
        let out = Pipe()
        task.standardOutput = out
        do {
            try task.run()
        } catch {
            NSLog("quiver relauncher: could not start script: \(error)")
            try? fm.removeItem(at: scriptURL)
            return
        }
        let watcher = pid_t(String(decoding: out.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
            .trimmingCharacters(in: .whitespacesAndNewlines))
        task.waitUntilExit()
        NSApp.terminate(nil)
        // Back here only when the quit was cancelled (unsaved drafts).
        // The watcher would otherwise swap the app under a later quit
        // nobody meant as a relaunch.
        if let watcher, watcher > 0 { kill(watcher, SIGTERM) }
    }
}
