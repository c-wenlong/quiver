import Foundation
import NIOConcurrencyHelpers
import NIOCore
import NIOPosix
import NIOSSH

/// Why the console couldn't open, or closed, in words for the phone.
enum SSHConsoleError: LocalizedError, Equatable {
    /// sshd showed a host key the hub never vouched for.
    case hostKeyMismatch
    /// sshd turned the phone's key down.
    case keyRejected(user: String)
    case unreachable(String)
    case shellRefused
    /// TCP connected, but login or shell setup didn't finish in time.
    case timedOut
    /// The connection broke after the shell was open.
    case dropped(String)

    var errorDescription: String? {
        switch self {
        case .hostKeyMismatch:
            "The machine showed an SSH host key the hub didn't list. Open the console again while the hub is up to refresh it; if it still fails, don't trust this connection."
        case .keyRejected(let user):
            "The machine turned down this phone's key for \(user). Run swe remote ssh --on there, and check Remote Login lets \(user) in."
        case .unreachable(let detail):
            "Can't reach the machine's SSH port. Check that Tailscale is on. (\(detail))"
        case .shellRefused:
            "The machine logged this phone in but wouldn't start a shell."
        case .timedOut:
            "The machine's SSH port answered, but logging in didn't finish within \(Int(SSHShell.setupDeadline.nanoseconds / 1_000_000_000)) seconds."
        case .dropped(let detail):
            "The connection dropped: \(detail)"
        }
    }
}

/// Only the host keys the hub listed get through.
struct HostKeyPin {
    let allowed: Set<String>

    init(_ keys: [String]) {
        // Pinned on type and key, whatever comment a key line carried.
        allowed = Set(keys.map { $0.split(separator: " ").prefix(2).joined(separator: " ") })
    }

    func admits(_ openSSHKey: String) -> Bool { allowed.contains(openSSHKey) }
}

/// One interactive shell on a machine's sshd: a pty, a login shell, raw
/// bytes each way. Events arrive on a NIO thread; callers hop to main.
final class SSHShell: @unchecked Sendable {
    enum Event {
        case output([UInt8])
        /// The shell ended. A nil error is a plain logout.
        case closed(SSHConsoleError?)
    }

    private let channel: Channel
    private let child: Channel

    private init(channel: Channel, child: Channel) {
        self.channel = channel
        self.child = child
    }

    static let setupDeadline = TimeAmount.seconds(20)

    static func open(target: SSHTarget, key: NIOSSHPrivateKey, cols: Int, rows: Int,
                     onEvent: @escaping @Sendable (Event) -> Void) async throws -> SSHShell {
        let auth = PublicKeyAuth(user: target.user, key: key)
        let pin = PinnedHostKeys(pin: HostKeyPin(target.hostKeys))
        let bootstrap = ClientBootstrap(group: MultiThreadedEventLoopGroup.singleton)
            .connectTimeout(.seconds(10))
            .channelOption(ChannelOptions.socket(SocketOptionLevel(IPPROTO_TCP), TCP_NODELAY), value: 1)
            .channelInitializer { channel in
                channel.eventLoop.makeCompletedFuture {
                    let config = SSHClientConfiguration(userAuthDelegate: auth, serverAuthDelegate: pin)
                    try channel.pipeline.syncOperations.addHandlers(
                        NIOSSHHandler(role: .client(config), allocator: channel.allocator, inboundChildChannelInitializer: nil),
                        CloseOnError())
                }
            }

        let channel: Channel
        do {
            channel = try await bootstrap.connect(host: target.host, port: target.port).get()
        } catch {
            throw SSHConsoleError.unreachable(error.localizedDescription)
        }

        let shell = ShellHandler(cols: cols, rows: rows, onEvent: onEvent)
        // The connect timeout covers TCP only; this one covers key
        // exchange, login and the shell, so a machine that goes quiet
        // mid-setup can't leave the console connecting forever.
        let expired = NIOLockedValueBox(false)
        let deadline = channel.eventLoop.scheduleTask(in: setupDeadline) {
            expired.withLockedValue { $0 = true }
            channel.close(promise: nil)
        }
        defer { deadline.cancel() }
        do {
            let child: Channel = try await channel.pipeline.handler(type: NIOSSHHandler.self).flatMap { ssh in
                let promise = channel.eventLoop.makePromise(of: Channel.self)
                ssh.createChannel(promise) { child, kind in
                    guard kind == .session else {
                        return child.eventLoop.makeFailedFuture(SSHConsoleError.shellRefused)
                    }
                    return child.eventLoop.makeCompletedFuture {
                        try child.pipeline.syncOperations.addHandler(shell)
                    }
                }
                return promise.futureResult
            }.get()
            try await shell.ready.futureResult.get()
            return SSHShell(channel: channel, child: child)
        } catch {
            // A promise left open trips NIO's leak check.
            shell.ready.fail(error)
            channel.close(promise: nil)
            if expired.withLockedValue({ $0 }) { throw SSHConsoleError.timedOut }
            if pin.mismatched { throw SSHConsoleError.hostKeyMismatch }
            if auth.rejected { throw SSHConsoleError.keyRejected(user: target.user) }
            if let known = error as? SSHConsoleError { throw known }
            throw SSHConsoleError.unreachable(error.localizedDescription)
        }
    }

    func write(_ bytes: [UInt8]) {
        var buffer = child.allocator.buffer(capacity: bytes.count)
        buffer.writeBytes(bytes)
        child.writeAndFlush(SSHChannelData(type: .channel, data: .byteBuffer(buffer)), promise: nil)
    }

    func resize(cols: Int, rows: Int) {
        let change = SSHChannelRequestEvent.WindowChangeRequest(
            terminalCharacterWidth: cols, terminalRowHeight: rows, terminalPixelWidth: 0, terminalPixelHeight: 0)
        child.triggerUserOutboundEvent(change, promise: nil)
    }

    func close() {
        channel.close(promise: nil)
    }

    /// Returns once the SSH connection itself has closed.
    func connectionClosed() async {
        try? await channel.closeFuture.get()
    }
}

/// A refused host key or login surfaces as an error on the connection,
/// which otherwise stays open until sshd's own login timeout (two
/// minutes); closing it fails the pending shell at once.
private final class CloseOnError: ChannelInboundHandler, Sendable {
    typealias InboundIn = Any

    func errorCaught(context: ChannelHandlerContext, error: Error) {
        context.close(promise: nil)
    }
}

/// Offers the phone's key once; a second ask means sshd refused it.
private final class PublicKeyAuth: NIOSSHClientUserAuthenticationDelegate, @unchecked Sendable {
    let user: String
    let key: NIOSSHPrivateKey
    private(set) var rejected = false
    private var offered = false

    init(user: String, key: NIOSSHPrivateKey) {
        self.user = user
        self.key = key
    }

    func nextAuthenticationType(availableMethods: NIOSSHAvailableUserAuthenticationMethods,
                                nextChallengePromise: EventLoopPromise<NIOSSHUserAuthenticationOffer?>) {
        guard !offered, availableMethods.contains(.publicKey) else {
            rejected = true
            // Failing, not succeeding with nil: a nil offer leaves NIO
            // waiting on sshd's login timeout, while an error reaches
            // CloseOnError and ends the connection now.
            nextChallengePromise.fail(SSHConsoleError.keyRejected(user: user))
            return
        }
        offered = true
        nextChallengePromise.succeed(NIOSSHUserAuthenticationOffer(
            username: user, serviceName: "", offer: .privateKey(.init(privateKey: key))))
    }
}

private final class PinnedHostKeys: NIOSSHClientServerAuthenticationDelegate, @unchecked Sendable {
    let pin: HostKeyPin
    private(set) var mismatched = false

    init(pin: HostKeyPin) { self.pin = pin }

    func validateHostKey(hostKey: NIOSSHPublicKey, validationCompletePromise: EventLoopPromise<Void>) {
        if pin.admits(String(openSSHPublicKey: hostKey)) {
            validationCompletePromise.succeed(())
        } else {
            mismatched = true
            validationCompletePromise.fail(SSHConsoleError.hostKeyMismatch)
        }
    }
}

/// Asks for a pty and a shell, then passes bytes through. `ready`
/// completes once both requests are granted.
private final class ShellHandler: ChannelInboundHandler, @unchecked Sendable {
    typealias InboundIn = SSHChannelData

    let ready: EventLoopPromise<Void>
    private let cols: Int
    private let rows: Int
    private let onEvent: @Sendable (SSHShell.Event) -> Void
    /// Replies come back in request order: the pty's, then the shell's.
    private var granted = 0
    /// Why the channel is closing, when it isn't a plain logout.
    private var failure: Error?

    init(cols: Int, rows: Int, onEvent: @escaping @Sendable (SSHShell.Event) -> Void) {
        self.cols = cols
        self.rows = rows
        self.onEvent = onEvent
        // The handler is made before its channel exists, so the promise
        // takes any loop of the shared group; a promise completes from
        // any thread, the channel's included.
        self.ready = MultiThreadedEventLoopGroup.singleton.next().makePromise()
    }

    func handlerAdded(context: ChannelHandlerContext) {
        context.channel.setOption(ChannelOptions.allowRemoteHalfClosure, value: true).whenFailure { error in
            context.fireErrorCaught(error)
        }
    }

    func channelActive(context: ChannelHandlerContext) {
        let pty = SSHChannelRequestEvent.PseudoTerminalRequest(
            wantReply: true, term: "xterm-256color",
            terminalCharacterWidth: cols, terminalRowHeight: rows,
            terminalPixelWidth: 0, terminalPixelHeight: 0,
            terminalModes: SSHTerminalModes([:]))
        context.triggerUserOutboundEvent(pty, promise: nil)
        context.triggerUserOutboundEvent(SSHChannelRequestEvent.ShellRequest(wantReply: true), promise: nil)
        context.fireChannelActive()
    }

    func userInboundEventTriggered(context: ChannelHandlerContext, event: Any) {
        switch event {
        case is ChannelSuccessEvent:
            granted += 1
            if granted == 2 { ready.succeed(()) }
        case is ChannelFailureEvent:
            ready.fail(SSHConsoleError.shellRefused)
            context.close(promise: nil)
        default:
            context.fireUserInboundEventTriggered(event)
        }
    }

    func channelRead(context: ChannelHandlerContext, data: NIOAny) {
        let message = unwrapInboundIn(data)
        guard case .byteBuffer(var buffer) = message.data,
              let bytes = buffer.readBytes(length: buffer.readableBytes) else { return }
        // stderr goes to the same screen, as in any terminal.
        onEvent(.output(bytes))
    }

    func channelInactive(context: ChannelHandlerContext) {
        ready.fail(failure ?? SSHConsoleError.shellRefused)
        onEvent(.closed(failure.map { SSHConsoleError.dropped(($0 as? LocalizedError)?.errorDescription ?? "\($0)") }))
        // One shell per connection: once it ends, the connection has no
        // other use, and left open it would hold a login on the machine.
        context.channel.parent?.close(promise: nil)
        context.fireChannelInactive()
    }

    func errorCaught(context: ChannelHandlerContext, error: Error) {
        failure = failure ?? error
        ready.fail(error)
        context.close(promise: nil)
    }
}
