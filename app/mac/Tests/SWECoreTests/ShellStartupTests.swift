import Foundation
import Testing
@testable import SWECore

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

    /// The home-manager pattern: the rc loops over a drop-in folder and
    /// sources each match, and one drop-in sources the keys file.
    @Test func aForLoopOverDropInFilesCounts() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(
            atPath: root.appending(path: ".api_keys").path,
            withDestinationPath: keys.path)
        let dropins = root.appending(path: ".config/zshrc.d")
        try FileManager.default.createDirectory(at: dropins.appending(path: "aliases"),
                                                withIntermediateDirectories: true)
        try "[ -f \"$HOME/.api_keys\" ] && source \"$HOME/.api_keys\"\n"
            .write(to: dropins.appending(path: "secrets.zsh"), atomically: true, encoding: .utf8)
        try "export OTHER=1\n"
            .write(to: dropins.appending(path: "misc.zsh"), atomically: true, encoding: .utf8)
        try "source ~/.api_keys\n"
            .write(to: dropins.appending(path: "aliases/git.zsh"), atomically: true, encoding: .utf8)
        let rc = "for f in \"$HOME/.config/zshrc.d\"/**/*.zsh(N); do\n  source \"$f\"\ndone\n"
        #expect(ShellStartup.sources(rc, keysFile: keys, home: root))
        // `${f}` works the same; a folder that never sources it doesn't.
        #expect(ShellStartup.sources("for f in ~/.config/zshrc.d/*.zsh\n  source \"${f}\"\n",
                                     keysFile: keys, home: root))
        let empty = root.appending(path: "empty")
        try FileManager.default.createDirectory(at: empty, withIntermediateDirectories: true)
        #expect(!ShellStartup.sources("for f in \(empty.path)/*.zsh; do source $f; done\n",
                                      keysFile: keys, home: root))
    }

    /// `source dir/*.zsh` globs on disk, and a file a sourced file
    /// sources counts the same (the chain is followed, depth-capped).
    @Test func aGlobOrChainedSourceCounts() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        let dir = root.appending(path: "rc.d")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try "source \(keys.path)\n"
            .write(to: dir.appending(path: "10-keys.zsh"), atomically: true, encoding: .utf8)
        try "export X=1\n"
            .write(to: dir.appending(path: "20-env.zsh"), atomically: true, encoding: .utf8)
        #expect(ShellStartup.sources("source \(dir.path)/*.zsh\n", keysFile: keys, home: root))
        // A literal file that itself sources the keys file counts too.
        try "source \(dir.path)/10-keys.zsh\n"
            .write(to: root.appending(path: "middle.zsh"), atomically: true, encoding: .utf8)
        #expect(ShellStartup.sources("source \(root.path)/middle.zsh\n", keysFile: keys, home: root))
        // A `for f (list)` shorthand binds the same way.
        #expect(ShellStartup.sources("for f (\(dir.path)/10-keys.zsh) source $f\n",
                                     keysFile: keys, home: root))
        // `source glob` reads only the first expansion; a later match is
        // an argument to that file, not another sourced file.
        let later = root.appending(path: "later.d")
        try FileManager.default.createDirectory(at: later, withIntermediateDirectories: true)
        try "export X=1\n"
            .write(to: later.appending(path: "00-env.zsh"), atomically: true, encoding: .utf8)
        try "source \(keys.path)\n"
            .write(to: later.appending(path: "90-keys.zsh"), atomically: true, encoding: .utf8)
        #expect(!ShellStartup.sources("source \(later.path)/*.zsh\n", keysFile: keys, home: root))
        // ...but a `for` variable iterates every word, so it sees it.
        #expect(ShellStartup.sources("for f in \(later.path)/*.zsh; do source $f; done\n",
                                     keysFile: keys, home: root))
    }

    /// A sourced file reached through a symlink is still read — resource
    /// values describe the link, so the check resolves first.
    @Test func aSymlinkedDropInCounts() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        let dir = root.appending(path: "zshrc.d")
        let real = root.appending(path: "dotfiles/secrets.zsh")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: real.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "source \(keys.path)\n".write(to: real, atomically: true, encoding: .utf8)
        try FileManager.default.createSymbolicLink(
            atPath: dir.appending(path: "secrets.zsh").path, withDestinationPath: real.path)
        #expect(ShellStartup.sources("for f in \(dir.path)/*.zsh; do source $f; done\n",
                                     keysFile: keys, home: root))
        #expect(ShellStartup.sources("source \(dir.path)/secrets.zsh\n",
                                     keysFile: keys, home: root))
    }

    /// A wildcard the shell wouldn't expand — quoted or escaped — stays
    /// a literal path character, so it doesn't sweep up matching files.
    @Test func quotedWildcardsStayLiteral() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        let dir = root.appending(path: "rc.d")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try "source \(keys.path)\n"
            .write(to: dir.appending(path: "a.zsh"), atomically: true, encoding: .utf8)
        for rc in ["source \"\(dir.path)/*.zsh\"\n",
                   "source '\(dir.path)/*.zsh'\n",
                   "source \(dir.path)/\\*.zsh\n",
                   "for f in \"\(dir.path)/*.zsh\"; do source $f; done\n"] {
            #expect(!ShellStartup.sources(rc, keysFile: keys, home: root), "\(rc)")
        }
        // A literal `*`/`[` in a real file's name still reaches it.
        let odd = dir.appending(path: "literal*[1]/k.zsh")
        try FileManager.default.createDirectory(at: odd.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "source \(keys.path)\n".write(to: odd, atomically: true, encoding: .utf8)
        #expect(ShellStartup.sources("source \(dir.path)/literal\\*\\[1\\]/k.zsh\n",
                                     keysFile: keys, home: root))
        #expect(ShellStartup.sources("source \"\(dir.path)/literal*[1]/k.zsh\"\n",
                                     keysFile: keys, home: root))
    }

    /// After a `for` body ends the variable keeps only the last word, so
    /// an earlier list entry stops counting as a source.
    @Test func aFinishedForKeepsOnlyTheLastWord() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        let other = root.appending(path: "other.zsh")
        try "export X=1\n".write(to: other, atomically: true, encoding: .utf8)
        // Inside the body both words count; after `done` only `other`.
        #expect(!ShellStartup.sources(
            "for f in \(keys.path) \(other.path); do true; done\nsource $f\n",
            keysFile: keys, home: root))
        #expect(!ShellStartup.sources(
            "for f in \(keys.path) \(other.path)\ndo\n  true\ndone\nsource $f\n",
            keysFile: keys, home: root))
        #expect(!ShellStartup.sources(
            "for f in \(keys.path) \(other.path) { true }\nsource $f\n",
            keysFile: keys, home: root))
        #expect(!ShellStartup.sources(
            "for f in \(keys.path) \(other.path); true\nsource $f\n",
            keysFile: keys, home: root))
        // The same `source` inside the body still sees every word.
        #expect(ShellStartup.sources(
            "for f in \(other.path) \(keys.path); do source $f; done\n",
            keysFile: keys, home: root))
        #expect(ShellStartup.sources(
            "for f in \(other.path) \(keys.path)\ndo\n  source $f\ndone\n",
            keysFile: keys, home: root))
        // Last word wins in shell order — the last one is what `source`
        // sees after the loop.
        #expect(ShellStartup.sources(
            "for f in \(other.path) \(keys.path); do true; done\nsource $f\n",
            keysFile: keys, home: root))
    }

    /// Two files sourcing each other can't loop forever; missing files
    /// and non-source mentions are skipped.
    @Test func cyclesAndMissingFilesDoNotCount() throws {
        let temp = try TempDir()
        defer { withExtendedLifetime(temp) {} }
        let root = temp.url
        let keys = root.appending(path: ".swe/secrets/providers/.api_keys")
        try FileManager.default.createDirectory(at: keys.deletingLastPathComponent(),
                                                withIntermediateDirectories: true)
        try "K=1".write(to: keys, atomically: true, encoding: .utf8)
        try "source \(root.path)/b.zsh\n"
            .write(to: root.appending(path: "a.zsh"), atomically: true, encoding: .utf8)
        try "source \(root.path)/a.zsh\n"
            .write(to: root.appending(path: "b.zsh"), atomically: true, encoding: .utf8)
        #expect(!ShellStartup.sources("source \(root.path)/a.zsh\n", keysFile: keys, home: root))
        #expect(!ShellStartup.sources("source \(root.path)/gone/*.zsh\n", keysFile: keys, home: root))
    }

    @Test func appendingKeepsTheExistingTextWhole() {
        let keys = home.appending(path: ".api_keys")
        let line = ShellStartup.sourceLine(for: keys, home: home)
        let marker = "# Added by SWE"
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

    /// A read-only rc (a Nix store path, a read-only checkout) is refused
    /// before the backup, so a doomed attempt leaves nothing behind.
    @Test(skippedAsRoot) func anUnwritableRcFailsBeforeTheBackup() throws {
        let (home, backups, keys, rc) = try home()
        try "x\n".write(to: rc, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o444], ofItemAtPath: rc.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: rc.path) }
        #expect(throws: ShellStartupWriter.Error.unwritable(rc.resolvingSymlinksInPath())) {
            try ShellStartupWriter(home: home, backupDirectory: backups)
                .addSource(keysFile: keys, shell: "/bin/zsh")
        }
        #expect((try? FileManager.default.contentsOfDirectory(atPath: backups.path)) ?? [] == [])
        #expect(try String(contentsOf: rc, encoding: .utf8) == "x\n")
    }

    /// A writable file in a folder the rename can't touch is refused too.
    @Test(skippedAsRoot) func anUnwritableFolderFailsBeforeTheBackup() throws {
        let (home, backups, keys, rc) = try home()
        try "x\n".write(to: rc, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: home.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: home.path) }
        #expect(throws: ShellStartupWriter.Error.unwritable(rc.resolvingSymlinksInPath())) {
            try ShellStartupWriter(home: home, backupDirectory: backups)
                .addSource(keysFile: keys, shell: "/bin/zsh")
        }
    }

    @Test(skippedAsRoot) func writableChecksTheFileAndItsFolder() throws {
        let (home, _, _, rc) = try home()
        // Missing file, writable folder: the write would create it.
        #expect(ShellStartup.writable(rc))
        try "x\n".write(to: rc, atomically: true, encoding: .utf8)
        #expect(ShellStartup.writable(rc))
        try FileManager.default.setAttributes([.posixPermissions: 0o444], ofItemAtPath: rc.path)
        #expect(!ShellStartup.writable(rc))
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: rc.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: home.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: home.path) }
        #expect(!ShellStartup.writable(rc))
    }
}
