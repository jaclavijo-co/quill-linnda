import AppKit
import ArgumentParser
import Foundation

@main
struct Quill: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "quill",
        abstract: "Local meeting recorder + transcriber. Records mic and system audio as two tracks, then transcribes on-device.",
        subcommands: [Run.self, Doctor.self, Install.self, Mic.self],
        defaultSubcommand: Run.self
    )
}

struct Run: ParsableCommand {
    static let configuration = CommandConfiguration(
        commandName: "run",
        abstract: "Run the menu-bar daemon (default)."
    )

    @Option(name: .long, help: "Recordings root directory (overrides the config file).")
    var out: String?

    func run() throws {
        // ArgumentParser invokes run() on the main thread; promote that fact
        // to the type system so AppKit calls are cleanly isolated.
        try MainActor.assumeIsolated { try runMain() }
    }

    @MainActor
    private func runMain() throws {
        let root = Config.resolveRoot(cliOverride: out)

        // Non-blocking: permissions prompt on first recording, so warnings at
        // startup are informational, not fatal.
        let checks = DoctorReport.run(recordingsRoot: root)
        if !DoctorReport.allOK(checks) {
            FileHandle.standardError.write(Data("startup checks failed:\n".utf8))
            DoctorReport.print(checks)
            throw ExitCode(1)
        }

        let app = NSApplication.shared
        app.setActivationPolicy(.accessory)

        let controller = AppController(root: root)

        let sigint = DispatchSource.makeSignalSource(signal: SIGINT, queue: .main)
        sigint.setEventHandler {
            FileHandle.standardError.write(Data("\nshutting down\n".utf8))
            MainActor.assumeIsolated { controller.shutdown() }
        }
        sigint.resume()
        signal(SIGINT, SIG_IGN)

        FileHandle.standardError.write(Data(
            "quill up · recordings → \(root.path) · ^C to quit\n".utf8
        ))
        app.run()
    }
}

struct Doctor: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Check microphone, system audio, and recordings folder."
    )

    func run() throws {
        let checks = DoctorReport.run(recordingsRoot: Config.resolveRoot(cliOverride: nil))
        DoctorReport.print(checks)
        if !DoctorReport.allOK(checks) {
            throw ExitCode(1)
        }
    }
}

struct Mic: ParsableCommand {
    static let configuration = CommandConfiguration(
        abstract: "Muestra qué apps están usando el micrófono (para probar la parada automática)."
    )

    func run() throws {
        let all = CallDetector.micUsers()
        let calls = CallDetector.callAppsUsingMic()
        print("usando el micrófono: \(all.isEmpty ? "(nadie)" : all.joined(separator: ", "))")
        print("cuentan como llamada: \(calls.isEmpty ? "(ninguna)" : calls.joined(separator: ", "))")
    }
}

/// Owns the menu bar, the current recording session, and the elapsed-time
/// ticker. All state transitions happen on the main actor.
@MainActor
final class AppController {
    private let root: URL
    private let menuBar = MenuBarController()
    private let transcription = TranscriptionCoordinator()
    private var session: RecordingSession?
    private var ticker: Timer?
    private var monitor: Timer?
    // Estado de la detección de llamadas
    private var callSeen = false
    private var lastCallActivity: Date?
    private var callActiveSince: Date?
    private var reminded = false
    // Arranque automático por calendario
    private var currentEventUID: String?
    private var suppressedEvents = Set<String>()  // detenidas a mano: no volver a arrancar
    private var calendarTimer: Timer?
    private var lastFreshSync = Date.distantPast

    init(root: URL) {
        self.root = root
        menuBar.onToggle = { [weak self] in self?.toggle() }
        menuBar.onOpenFolder = { [weak self] in self?.openFolder() }
        menuBar.onQuit = { [weak self] in self?.shutdown() }
        menuBar.update(recording: false, elapsed: nil)

        monitor = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.checkCall() }
        }
        Agenda.sync()
        Agenda.phoneScan()
        calendarTimer = Timer.scheduledTimer(withTimeInterval: 180, repeats: true) { _ in
            Agenda.sync()
            Agenda.phoneScan()
        }

        Task { [transcription, root] in
            await transcription.setStatusHandler { status in
                Task { @MainActor [weak self] in
                    self?.showTranscription(status)
                }
            }
            await transcription.resumePending(root: root)
        }
    }

    /// Stop any live session cleanly (finalizing files) and exit.
    func shutdown() {
        stopSession()
        NSApp.terminate(nil)
    }

    private func toggle() {
        if session == nil {
            currentEventUID = Agenda.meetingNow()?.uid
            startSession()
        } else {
            // Detenida a mano: no volver a arrancar sola en esta misma reunión.
            if let uid = currentEventUID { suppressedEvents.insert(uid) }
            stopSession()
        }
    }

    private func startSession() {
        do {
            let newSession = try RecordingSession(root: root)
            try newSession.start()
            session = newSession
            callSeen = false
            lastCallActivity = nil
            FileHandle.standardError.write(Data("● recording → \(newSession.dir.path)\n".utf8))
        } catch {
            FileHandle.standardError.write(Data("recording start failed: \(error)\n".utf8))
            notifyUser(title: "quill — recording failed", body: "\(error)")
            return
        }

        menuBar.update(recording: true, elapsed: "0:00")
        ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
    }

    private func stopSession() {
        guard let session else { return }
        session.stop()
        let elapsed = Self.format(Date().timeIntervalSince(session.startedAt))
        FileHandle.standardError.write(Data(
            "○ stopped · \(elapsed) · \(session.dir.path)\n".utf8
        ))
        self.session = nil
        currentEventUID = nil
        reminded = true  // no volver a preguntar por esta misma llamada
        ticker?.invalidate()
        ticker = nil
        menuBar.update(recording: false, elapsed: nil)

        let dir = session.dir
        Task { [transcription] in await transcription.enqueue(dir) }
    }

    /// Cada 5 s: si se está grabando, para sola cuando la llamada termina (o al
    /// llegar al tope de horas). Si no se está grabando y empieza una llamada,
    /// avisa una vez para que el usuario le dé clic a la pluma.
    private func checkCall() {
        let calls = CallDetector.callAppsUsingMic()
        let active = !calls.isEmpty
        let now = Date()

        if let session {
            if active {
                if !callSeen {
                    FileHandle.standardError.write(Data("llamada detectada: \(calls.joined(separator: ", "))\n".utf8))
                }
                callSeen = true
                lastCallActivity = now
            }
            if Config.autoStop(), callSeen, let last = lastCallActivity,
               now.timeIntervalSince(last) >= Config.autoStopGraceSeconds() {
                autoStop(reason: "La llamada terminó")
                return
            }
            let maxH = Config.maxRecordingHours()
            if now.timeIntervalSince(session.startedAt) >= maxH * 3600 {
                autoStop(reason: "Llegó al tope de \(Int(maxH)) h")
            }
            return
        }

        if active {
            if callActiveSince == nil { callActiveSince = now }
            // Arranque automático: llamada activa 5 s + reunión en el calendario.
            let mode = Config.autoStart()
            if mode != "off", let since = callActiveSince, now.timeIntervalSince(since) >= 5 {
                let meeting = Agenda.meetingNow(now)
                // ¿Reunión agendada a última hora? Releer el calendario ya (máx. cada 2 min).
                if meeting == nil && mode == "calendar" && now.timeIntervalSince(lastFreshSync) > 120 {
                    lastFreshSync = now
                    Agenda.sync(fresh: true)
                }
                let uid = meeting?.uid ?? "llamada|\(Int(since.timeIntervalSince1970))"
                if (mode == "any" || meeting != nil) && !suppressedEvents.contains(uid) {
                    FileHandle.standardError.write(Data("arranque automático: \(meeting?.title ?? "llamada") · \(calls.joined(separator: ", "))\n".utf8))
                    currentEventUID = uid
                    menuBar.showCallWaiting(false)
                    startSession()
                    if session != nil {
                        NSSound(named: NSSound.Name("Hero"))?.play()
                        notifyUser(title: "Grabando: \(meeting?.title ?? "llamada")",
                                   body: "Arrancó sola. Para detenerla: pluma → Detener grabación")
                    }
                    return
                }
            }
            if Config.callReminder(), !reminded,
               let since = callActiveSince, now.timeIntervalSince(since) >= 10 {
                reminded = true
                FileHandle.standardError.write(Data("llamada sin grabar: \(calls.joined(separator: ", "))\n".utf8))
                menuBar.showCallWaiting(true)
                NSSound(named: NSSound.Name("Glass"))?.play()
                notifyUser(
                    title: "¿Grabo esta llamada?",
                    body: "Clic en la pluma de la barra de menú → Empezar a grabar"
                )
            }
        } else {
            if callActiveSince != nil { menuBar.showCallWaiting(false) }
            callActiveSince = nil
            reminded = false
        }
    }

    private func autoStop(reason: String) {
        guard let session else { return }
        let elapsed = Self.format(Date().timeIntervalSince(session.startedAt))
        stopSession()
        NSSound(named: NSSound.Name("Submarine"))?.play()
        notifyUser(title: "Grabación detenida (\(elapsed))", body: "\(reason). Estoy armando la nota.")
    }

    private func showTranscription(_ status: TranscriptionCoordinator.Status) {
        switch status {
        case .idle:
            menuBar.updateTranscription(nil)
        case .transcribing(let name, let queued):
            menuBar.updateTranscription(
                queued > 0 ? "transcribing \(name) · \(queued) queued" : "transcribing \(name)"
            )
        case .failed(let name):
            menuBar.updateTranscription("transcription failed · \(name)")
        }
    }

    private func tick() {
        guard let session else { return }
        menuBar.update(
            recording: true,
            elapsed: Self.format(Date().timeIntervalSince(session.startedAt))
        )
    }

    private func openFolder() {
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        NSWorkspace.shared.open(root)
    }

    private static func format(_ interval: TimeInterval) -> String {
        let total = Int(interval)
        let h = total / 3600, m = (total % 3600) / 60, s = total % 60
        return h > 0
            ? String(format: "%d:%02d:%02d", h, m, s)
            : String(format: "%d:%02d", m, s)
    }
}
