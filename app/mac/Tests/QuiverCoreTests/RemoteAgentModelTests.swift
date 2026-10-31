import Foundation
import Testing
@testable import QuiverCore

@MainActor
@Suite struct RemoteAgentModelTests {
    /// `swe remote agent --status --json` as the CLI prints it.
    private static func status(configured: Bool = true, installed: Bool = false,
                               running: Bool = false, hubInstalled: Bool = false,
                               supported: Bool = true) -> CommandResult {
        let json = """
        {"schema": 1, "data": {"configured": \(configured), "hub": "https://studio.tail1.ts.net",
         "supported": \(supported), "problem": null, "installed": \(installed),
         "running": \(running),
         "service": "/Users/k/Library/LaunchAgents/com.cwenlong.quiver.remote-agent.plist",
         "program": null, "log": "/Users/k/.quiver/data/remote/agent.log",
         "hub_installed": \(hubInstalled)}}
        """
        return CommandResult(status: 0, stdout: Data(json.utf8), stderr: "")
    }

    private func model(_ rules: [([String], CommandResult)]) -> (RemoteAgentModel, ScriptedRunner) {
        let runner = ScriptedRunner(rules)
        return (RemoteAgentModel(swe: SweClient(runner: runner)), runner)
    }

    @Test func aPairedMacWithoutItsOwnHubGetsTheSwitch() async {
        let (model, runner) = model([(["remote", "agent", "--status"], Self.status())])
        #expect(model.showsToggle == false, "hidden until swe answers")
        await model.load()
        #expect(runner.calls == [["remote", "agent", "--status", "--json"]])
        #expect(model.showsToggle)
        #expect(model.status?.installed == false)
        #expect(model.status?.detail.contains("so studio.tail1.ts.net keeps this Mac's sessions") == true)
    }

    @Test func theSwitchHidesOnTheHubMachineUnpairedMacsAndOldSwe() async {
        let cases: [(CommandResult, String)] = [
            (Self.status(hubInstalled: true), "this Mac hosts the hub"),
            (Self.status(configured: false), "not a satellite"),
            (Self.status(supported: false), "no launchd"),
            (CommandResult(status: 1, stdout: Data(), stderr: "unknown flag: --status"), "older swe"),
        ]
        for (reply, why) in cases {
            let (model, _) = model([(["remote", "agent"], reply)])
            await model.load()
            #expect(model.showsToggle == false, Comment(rawValue: why))
        }
        // An installed agent keeps its switch so it can be turned off.
        let (model, _) = model([(["remote", "agent"], Self.status(configured: false, installed: true))])
        await model.load()
        #expect(model.showsToggle)
    }

    @Test func turningItOnInstallsAndShowsTheNewState() async {
        let (model, runner) = model([
            (["remote", "agent", "--install"], Self.status(installed: true, running: true)),
            (["remote", "agent", "--status"], Self.status()),
        ])
        await model.load()
        await model.setKeepRunning(true)
        #expect(runner.calls.last == ["remote", "agent", "--install", "--json"])
        #expect(model.status?.installed == true)
        #expect(model.status?.detail.hasSuffix("Running now.") == true)
        #expect(model.error == nil)
        #expect(model.saving == false)
    }

    @Test func turningItOffUninstalls() async {
        let (model, runner) = model([
            (["remote", "agent", "--uninstall"], Self.status()),
            (["remote", "agent", "--status"], Self.status(installed: true, running: true)),
        ])
        await model.load()
        await model.setKeepRunning(false)
        #expect(runner.calls.last == ["remote", "agent", "--uninstall", "--json"])
        #expect(model.status?.installed == false)
    }

    @Test func aRefusalKeepsTheSwitchAndSaysWhy() async {
        let (model, _) = model([
            (["remote", "agent", "--install"],
             CommandResult(status: 1, stdout: Data(), stderr: "this machine's hub is installed")),
            (["remote", "agent", "--status"], Self.status()),
        ])
        await model.load()
        await model.setKeepRunning(true)
        #expect(model.status?.installed == false)
        #expect(model.error?.contains("hub is installed") == true)
    }

    @Test func anInstalledAgentThatIsDownPointsAtItsLog() async {
        let (model, _) = model([(["remote", "agent"], Self.status(installed: true))])
        await model.load()
        #expect(model.status?.detail.hasSuffix("Installed, but not running: see /Users/k/.quiver/data/remote/agent.log.") == true)
    }
}
