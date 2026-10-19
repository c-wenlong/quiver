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

    @Test func sourceLineUsesHomeWhenInsideAndEscapesOtherwise() {
        let inside = home.appending(path: ".quiver/secrets/providers/.api_keys")
        #expect(ShellStartup.sourceLine(for: inside, home: home)
            == #"[ -f "$HOME/.quiver/secrets/providers/.api_keys" ] && source "$HOME/.quiver/secrets/providers/.api_keys""#)
        let odd = URL(fileURLWithPath: "/opt/we$ird/k`.api_keys")
        #expect(ShellStartup.sourceLine(for: odd, home: home)
            == #"[ -f "/opt/we\$ird/k\`.api_keys" ] && source "/opt/we\$ird/k\`.api_keys""#)
    }

    @Test func sourcesDetectsEveryForm() {
        let keys = home.appending(path: ".quiver/secrets/providers/.api_keys")
        let legacy = home.appending(path: ".api_keys")
        for rc in [
            "source ~/.api_keys\n",
            ". \"$HOME/.quiver/secrets/providers/.api_keys\"\n",
            "[ -f ~/.api_keys ] && source ~/.api_keys\n",
            "export A=1\nsource \(legacy.path)\n",
        ] {
            #expect(ShellStartup.sources(rc, keysFile: keys, home: home), "\(rc)")
        }
        for rc in [
            "# source ~/.api_keys\n",
            "export A=1 # source ~/.api_keys\n".replacingOccurrences(of: "export A=1 ", with: "# "),
            "source ~/.profile\n",
            "",
        ] {
            #expect(!ShellStartup.sources(rc, keysFile: keys, home: home), "\(rc)")
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
    private func home() throws -> (URL, URL, URL, URL) {
        let root = FileManager.default.temporaryDirectory
            .appending(path: "ShellStartupWriterTests-\(UUID().uuidString)")
        let home = root.appending(path: "home")
        let backups = root.appending(path: "backups")
        let keys = home.appending(path: ".quiver/secrets/providers/.api_keys")
        let rc = home.appending(path: ".zshrc")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "A=1\n".write(to: keys, atomically: true, encoding: .utf8)
        return (home, backups, keys, rc)
    }

    @Test func createsAMissingRcWithoutABackup() throws {
        let (home, backups, keys, rc) = try home()
        defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
        let (file, backup) = try ShellStartupWriter(home: home, backupDirectory: backups)
            .addSource(keysFile: keys, shell: "/bin/zsh")
        #expect(file == rc)
        #expect(backup == nil)
        let text = try String(contentsOf: rc, encoding: .utf8)
        #expect(text.contains(ShellStartup.sourceLine(for: keys, home: home)))
    }

    @Test func anExistingRcIsBackedUpFirst() throws {
        let (home, backups, keys, rc) = try home()
        defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
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
        defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
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
        defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
        let line = ShellStartup.sourceLine(for: keys, home: home)
        try "\(line)\n".write(to: rc, atomically: true, encoding: .utf8)
        _ = try ShellStartupWriter(home: home, backupDirectory: backups)
            .addSource(keysFile: keys, shell: "/bin/zsh")
        #expect(try String(contentsOf: rc, encoding: .utf8) == "\(line)\n")
        #expect((try? FileManager.default.contentsOfDirectory(atPath: backups.path)) ?? [] == [])
    }

    @Test func anUnsupportedShellThrows() throws {
        let (home, backups, keys, _) = try home()
        defer { try? FileManager.default.removeItem(at: home.deletingLastPathComponent()) }
        #expect(throws: ShellStartupWriter.Error.unsupportedShell("/bin/fish")) {
            try ShellStartupWriter(home: home, backupDirectory: backups)
                .addSource(keysFile: keys, shell: "/bin/fish")
        }
    }
}
