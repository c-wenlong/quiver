import Foundation
import Testing
@testable import QuiverCore

/// ShellStartup: which file a shell reads, what the line looks like, and
/// whether an existing rc text already sources the keys file.
@Suite struct ShellStartupTests {
    private let home = URL(fileURLWithPath: "/Users/t")

    @Test func startupFilePicksTheShellsRc() {
        #expect(ShellStartup.startupFile(shell: "/bin/zsh", home: home)
            == home.appending(path: ".zshrc"))
        #expect(ShellStartup.startupFile(shell: "zsh", home: home,
                                         environment: ["ZDOTDIR": "/tmp/dot"])
            == URL(fileURLWithPath: "/tmp/dot/.zshrc"))
        #expect(ShellStartup.startupFile(shell: "/bin/bash", home: home)
            == home.appending(path: ".bash_profile"))
        #expect(ShellStartup.startupFile(shell: "/opt/homebrew/bin/fish", home: home) == nil)
    }

    @Test func bashReadsTheFirstLoginFileThatExists() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let write = { (name: String) throws -> URL in
            let url = root.appending(path: name)
            try "x".write(to: url, atomically: true, encoding: .utf8)
            return url
        }
        #expect(ShellStartup.startupFile(shell: "bash", home: root)
            == root.appending(path: ".bash_profile"))
        let login = try write(".bash_login")
        #expect(ShellStartup.startupFile(shell: "bash", home: root) == login)
        let profile = try write(".bash_profile")
        #expect(ShellStartup.startupFile(shell: "bash", home: root) == profile)
    }

    @Test func sourceLineUsesHomeWhenInsideAndEscapesOtherwise() {
        let inside = home.appending(path: ".swe/secrets/providers/.api_keys")
        #expect(ShellStartup.sourceLine(for: inside, home: home)
            == #"[ -f "$HOME/.swe/secrets/providers/.api_keys" ] && source "$HOME/.swe/secrets/providers/.api_keys""#)
        let odd = URL(fileURLWithPath: "/opt/we$ird/k`.api_keys")
        #expect(ShellStartup.sourceLine(for: odd, home: home)
            == #"[ -f "/opt/we\$ird/k\`.api_keys" ] && source "/opt/we\$ird/k\`.api_keys""#)
    }

    @Test func sourcesRecognisesItsOwnLineForAnAwkwardPath() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        for name in ["My Keys/.api_keys", "a$b \"c\"/k\\eys", "Keys#1/.api_keys"] {
            let keys = root.appending(path: name)
            try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                    withIntermediateDirectories: true)
            try "K=1".write(to: keys, atomically: true, encoding: .utf8)
            for home in [root, URL(fileURLWithPath: "/nonexistent-home")] {
                let line = ShellStartup.sourceLine(for: keys, home: home)
                #expect(ShellStartup.sources(line, keysFile: keys, home: home), "\(line)")
            }
        }
        #expect(ShellStartup.shellWords(#"[ -f "a b" ] && source 'c d';x"#)
                == ["[", "-f", "a b", "]", "&&", "source", "c d", ";", "x"])
    }

    @Test func subshellsAndLiteralDollarsDoNotCount() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        let rel = ".swe/secrets/providers/.api_keys"
        for line in [#"(source "$HOME/\#(rel)")"#,
                     #"source "\$HOME/\#(rel)""#,
                     #"source '$HOME/\#(rel)'"#,
                     #"source \$HOME/\#(rel)"#] {
            #expect(!ShellStartup.sources(line, keysFile: keys, home: root), "\(line)")
        }
        #expect(ShellStartup.sources(#"(true) && source "$HOME/\#(rel)""#, keysFile: keys, home: root))
        #expect(!ShellStartup.sources(#"echo source "$HOME/\#(rel)""#, keysFile: keys, home: root))
        for body in [#"load_keys() {\#n  source "$HOME/\#(rel)"\#n}"#,
                     #"function load_keys {\#n  source "$HOME/\#(rel)"\#n}"#,
                     #"load_keys() { source "$HOME/\#(rel)"; }"#] {
            #expect(!ShellStartup.sources(body, keysFile: keys, home: root), "\(body)")
        }
        #expect(ShellStartup.sources(#"f() {\#n  echo hi\#n}\#nsource "$HOME/\#(rel)""#,
                                     keysFile: keys, home: root))
        #expect(ShellStartup.sources(#"if [ -f "$HOME/\#(rel)" ]; then source "$HOME/\#(rel)"; fi"#,
                                     keysFile: keys, home: root))
        #expect(ShellStartup.sources(#"source "$HOME/\#(rel)" # keys"#, keysFile: keys, home: root))
    }

    @Test func sourcesDetectsEveryForm() throws {
        // A real temp home: resolution only counts paths that exist.
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        // The legacy ~/.api_keys symlinked to the migrated file counts.
        try FileManager.default.createSymbolicLink(
            atPath: root.appending(path: ".api_keys").path,
            withDestinationPath: keys.path)
        for rc in [
            "source ~/.api_keys\n",
            ". \"$HOME/.swe/secrets/providers/.api_keys\"\n",
            ". \"${HOME}/.swe/secrets/providers/.api_keys\"\n",
            "[ -f ~/.api_keys ] && source ~/.api_keys\n",
            "export A=1\nsource \(keys.path)\n",
        ] {
            #expect(ShellStartup.sources(rc, keysFile: keys, home: root), "\(rc)")
        }
        // An unrelated file that merely shares the name does not.
        let other = root.appending(path: "other/.api_keys")
        try FileManager.default.createDirectory(at: other.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "x".write(to: other, atomically: true, encoding: .utf8)
        for rc in [
            "# source ~/.api_keys\n",
            "# x # source ~/.api_keys\n",
            "source ~/.profile\n",
            "source \(other.path)\n",
            "source /absent/.api_keys\n",
            "",
        ] {
            #expect(!ShellStartup.sources(rc, keysFile: keys, home: root), "\(rc)")
        }
    }

    @Test func appendingKeepsTheExistingTextWhole() {
        let keys = home.appending(path: ".api_keys")
        let line = ShellStartup.sourceLine(for: keys, home: home)
        let marker = "# Added by Quiver"
        #expect(ShellStartup.appending("a=1\n", line: line) == "a=1\n\(marker): provider API keys for new terminals\n\(line)\n")
        #expect(ShellStartup.appending("a=1", line: line) == "a=1\n\(marker): provider API keys for new terminals\n\(line)\n")
        #expect(ShellStartup.appending("", line: line) == "\(marker): provider API keys for new terminals\n\(line)\n")
    }
}

@Suite struct ShellStartupWriterTests {
    /// Swift Testing makes a new suite value per test, so each test gets
    /// its own folder, kept for as long as the test runs.
    private let temp: TempDir
    init() throws { temp = try TempDir() }

    private func home() throws -> (URL, URL, URL, URL) {
        let root = temp.url
        let home = root.appending(path: "home")
        let backups = root.appending(path: "backups")
        let keys = home.appending(path: ".swe/secrets/providers/.api_keys")
        let rc = home.appending(path: ".zshrc")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "A=1\n".write(to: keys, atomically: true, encoding: .utf8)
        return (home, backups, keys, rc)
    }

    @Test func createsAMissingRcWithoutABackup() throws {
        let (home, backups, keys, rc) = try home()
        let (file, backup) = try ShellStartupWriter(home: home, backupDirectory: backups)
            .addSource(keysFile: keys, shell: "/bin/zsh")
        #expect(file == rc)
        #expect(backup == nil)
        let text = try String(contentsOf: rc, encoding: .utf8)
        #expect(text.contains(ShellStartup.sourceLine(for: keys, home: home)))
    }

    @Test func anExistingRcIsBackedUpFirst() throws {
        let (home, backups, keys, rc) = try home()
        try "export A=1".write(to: rc, atomically: true, encoding: .utf8)
        let (file, backup) = try ShellStartupWriter(home: home, backupDirectory: backups, now: {
            Date(timeIntervalSince1970: 1_700_000_000)
        }).addSource(keysFile: keys, shell: "/bin/zsh")
        #expect(file == rc)
        let backupURL = try #require(backup)
        #expect(backupURL.lastPathComponent.hasPrefix(".zshrc."))
        #expect(backupURL.lastPathComponent.hasSuffix(".bak"))
        #expect(try String(contentsOf: backupURL, encoding: .utf8) == "export A=1")
        let mode = try FileManager.default.attributesOfItem(atPath: backupURL.path)[.posixPermissions] as? Int
        #expect(mode == 0o600)
        let rcMode = try FileManager.default.attributesOfItem(atPath: rc.path)[.posixPermissions] as? Int
        #expect(rcMode == 0o644)
        #expect(try String(contentsOf: rc, encoding: .utf8).hasPrefix("export A=1\n"))
    }

    @Test func writesThroughASymlinkedRc() throws {
        let (home, backups, keys, rc) = try home()
        let real = home.appending(path: "dotfiles/.zshrc")
        try FileManager.default.createDirectory(at: real.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "x\n".write(to: real, atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(atPath: rc.path, withDestinationPath: real.path)
        _ = try ShellStartupWriter(home: home, backupDirectory: backups)
            .addSource(keysFile: keys, shell: "/bin/zsh")
        #expect((try? FileManager.default.destinationOfSymbolicLink(atPath: rc.path)) != nil)
        #expect(try String(contentsOf: real, encoding: .utf8).contains(".api_keys"))
    }

    @Test func alreadySourcedIsANoOp() throws {
        let (home, backups, keys, rc) = try home()
        let line = ShellStartup.sourceLine(for: keys, home: home)
        try "\(line)\n".write(to: rc, atomically: true, encoding: .utf8)
        _ = try ShellStartupWriter(home: home, backupDirectory: backups)
            .addSource(keysFile: keys, shell: "/bin/zsh")
        #expect(try String(contentsOf: rc, encoding: .utf8) == "\(line)\n")
        #expect((try? FileManager.default.contentsOfDirectory(atPath: backups.path)) ?? [] == [])
    }

    @Test func aNonUtf8RcThrowsAndStaysUntouched() throws {
        let (home, backups, keys, rc) = try home()
        let bad = Data([0xff, 0xfe, 0x61])
        try bad.write(to: rc)
        #expect(throws: ShellStartupWriter.Error.self) {
            try ShellStartupWriter(home: home, backupDirectory: backups)
                .addSource(keysFile: keys, shell: "/bin/zsh")
        }
        #expect(try Data(contentsOf: rc) == bad)
    }

    @Test func aFailedBackupLeavesTheRcAlone() throws {
        let (home, backups, keys, rc) = try home()
        try "x\n".write(to: rc, atomically: true, encoding: .utf8)
        // A file where the backups folder should be can't hold one.
        let blocker = home.appending(path: "blocker")
        try "b".write(to: blocker, atomically: true, encoding: .utf8)
        #expect(throws: Error.self) {
            try ShellStartupWriter(home: home, backupDirectory: blocker)
                .addSource(keysFile: keys, shell: "/bin/zsh")
        }
        #expect(try String(contentsOf: rc, encoding: .utf8) == "x\n")
    }

    @Test(skippedAsRoot) func anUnreadableRcThrowsAndStaysUntouched() throws {
        let (home, backups, keys, rc) = try home()
        try "x\n".write(to: rc, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o000], ofItemAtPath: rc.path)
        #expect(throws: ShellStartupWriter.Error.self) {
            try ShellStartupWriter(home: home, backupDirectory: backups)
                .addSource(keysFile: keys, shell: "/bin/zsh")
        }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: rc.path)
        #expect(try Data(contentsOf: rc) == Data("x\n".utf8))
    }

    @Test func aConcurrentEditAbortsTheWrite() throws {
        let (home, backups, keys, rc) = try home()
        try "x\n".write(to: rc, atomically: true, encoding: .utf8)
        var writer = ShellStartupWriter(home: home, backupDirectory: backups)
        writer.beforeReplace = {
            try? "someone else\n".write(to: rc, atomically: true, encoding: .utf8)
        }
        #expect(throws: ShellStartupWriter.Error.changedOnDisk(rc.resolvingSymlinksInPath())) {
            try writer.addSource(keysFile: keys, shell: "/bin/zsh")
        }
        #expect(try String(contentsOf: rc, encoding: .utf8) == "someone else\n")
    }

    @Test func anUnsupportedShellThrows() throws {
        let (home, backups, keys, _) = try home()
        #expect(throws: ShellStartupWriter.Error.unsupportedShell("/bin/fish")) {
            try ShellStartupWriter(home: home, backupDirectory: backups)
                .addSource(keysFile: keys, shell: "/bin/fish")
        }
    }
}
