import Foundation

/// Optional user config at ~/.config/quill/config.json:
///
///     {
///       "recordings_dir": "~/Recordings",
///       "transcription": { "enabled": true, "engine": "parakeet" },
///       "mic_voice_processing": true,
///       "on_stop": "my-hook"
///     }
///
/// Resolution order for the recordings root: --out flag > config file >
/// ~/Recordings. `on_stop` is a shell command spawned with the session
/// directory as its argument — after the transcript is written, or right
/// after recording when transcription is disabled.
enum Config {
    static let path = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".config/quill/config.json")

    static let defaultRoot = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent("Recordings", isDirectory: true)

    /// The configured recordings root, or nil if no config file / no key.
    static func recordingsDir() -> URL? {
        guard let dir = load()?["recordings_dir"] as? String, !dir.isEmpty else { return nil }
        return URL(fileURLWithPath: (dir as NSString).expandingTildeInPath, isDirectory: true)
    }

    /// Shell command to spawn after each session's transcript is written (or
    /// after recording, if transcription is disabled), or nil.
    static func onStop() -> String? {
        guard let cmd = load()?["on_stop"] as? String, !cmd.isEmpty else { return nil }
        return cmd
    }

    /// Whether finished recordings are transcribed automatically. Default on.
    static func transcriptionEnabled() -> Bool {
        transcription()?["enabled"] as? Bool ?? true
    }

    /// Configured engine name. Only "parakeet" ships today; the coordinator
    /// warns and falls back for anything else.
    static func transcriptionEngine() -> String {
        transcription()?["engine"] as? String ?? "parakeet"
    }

    private static func transcription() -> [String: Any]? {
        load()?["transcription"] as? [String: Any]
    }

    /// Apple voice processing (acoustic echo cancellation) on the mic, so
    /// speaker playback doesn't bleed into the mic track and get transcribed
    /// as "me". Default off — the live voice unit ducks all other playback,
    /// and on headphones there's no echo to cancel anyway. Set true when
    /// recording meetings through the speakers.
    static func micVoiceProcessing() -> Bool {
        load()?["mic_voice_processing"] as? Bool ?? false
    }

    /// Pin the mic track to the built-in microphone instead of following the
    /// system default input. Default true: AirPods/Bluetooth headsets switch
    /// the default input to their HFP mic during meetings, which the meeting
    /// app holds exclusively, leaving quill recording silence. Set false for
    /// setups with an external audio interface as the intended mic.
    static func pinBuiltInMic() -> Bool {
        load()?["pin_builtin_mic"] as? Bool ?? true
    }

    // MARK: parada automática / recordatorio (Linnda)

    /// Detener la grabación sola cuando termina la llamada. Default true.
    static func autoStop() -> Bool {
        load()?["auto_stop"] as? Bool ?? true
    }

    /// Segundos sin ninguna app de llamadas usando el micrófono antes de parar.
    static func autoStopGraceSeconds() -> Double {
        (load()?["auto_stop_grace_seconds"] as? NSNumber)?.doubleValue ?? 60
    }

    /// Tope de seguridad: ninguna grabación dura más que esto. Default 3 h.
    static func maxRecordingHours() -> Double {
        (load()?["max_recording_hours"] as? NSNumber)?.doubleValue ?? 3
    }

    /// Notificar "¿Grabo esta llamada?" cuando empieza una llamada sin grabar.
    static func callReminder() -> Bool {
        load()?["call_reminder"] as? Bool ?? true
    }

    /// Arranque automático: "calendar" (llamada + reunión en el calendario),
    /// "any" (cualquier llamada) u "off" (solo recordatorio).
    static func autoStart() -> String {
        load()?["auto_start"] as? String ?? "off"
    }

    /// Comando que actualiza agenda.json (lo corre quill cada 5 min).
    static func calendarSync() -> String? {
        guard let c = load()?["calendar_sync"] as? String, !c.isEmpty else { return nil }
        return c
    }

    /// Comando que procesa grabaciones del teléfono (lo corre quill cada 3 min).
    static func phoneScan() -> String? {
        guard let c = load()?["phone_scan"] as? String, !c.isEmpty else { return nil }
        return c
    }

    static func agendaPath() -> String {
        load()?["agenda_path"] as? String ?? "~/.notetaker/agenda.json"
    }

    /// Subcadenas de bundle ID que cuentan como app de llamadas.
    static let defaultCallApps = [
        "chrome", "safari", "webkit", "zoom", "teams", "edgemac", "firefox",
        "brave", "thebrowser", "slack", "discord", "facetime", "whatsapp",
        "webex", "telegram", "opera", "vivaldi", "comet", "atlas",
    ]

    static func callApps() -> [String] {
        let extra = (load()?["call_apps"] as? [String]) ?? []
        return defaultCallApps + extra.map { $0.lowercased() }
    }

    /// Parse the config file. A malformed config is reported on stderr rather
    /// than silently ignored — recordings landing in an unexpected place is
    /// worse than a warning.
    private static func load() -> [String: Any]? {
        guard FileManager.default.fileExists(atPath: path.path) else { return nil }
        guard
            let data = try? Data(contentsOf: path),
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            FileHandle.standardError.write(Data(
                "warning: \(path.path) is not valid JSON — ignoring config\n".utf8
            ))
            return nil
        }
        return json
    }

    /// Resolve the recordings root from an optional CLI override.
    static func resolveRoot(cliOverride: String?) -> URL {
        if let cliOverride {
            return URL(
                fileURLWithPath: (cliOverride as NSString).expandingTildeInPath,
                isDirectory: true
            )
        }
        return recordingsDir() ?? defaultRoot
    }
}
