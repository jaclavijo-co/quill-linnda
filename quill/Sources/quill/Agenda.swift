import Foundation

/// Lee la agenda que deja ~/.notetaker/calendario.py (agenda.json) para saber
/// si en este momento hay una reunión en el calendario del usuario.
enum Agenda {
    struct Meeting {
        let uid: String
        let title: String
        let start: Date
        let end: Date
    }

    static var path: URL {
        URL(fileURLWithPath: (Config.agendaPath() as NSString).expandingTildeInPath)
    }

    private static func parse(_ s: String) -> Date? {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        // calendario.py escribe "2026-10-23T13:00-05:00" (sin segundos)
        if let d = iso.date(from: s) { return d }
        if s.count == 22 {
            var t = s
            t.insert(contentsOf: ":00", at: t.index(t.startIndex, offsetBy: 16))
            return iso.date(from: t)
        }
        return nil
    }

    /// Reunión en curso (o que empieza en los próximos `before` segundos).
    static func meetingNow(_ now: Date = Date(), before: TimeInterval = 600, after: TimeInterval = 300) -> Meeting? {
        guard let data = try? Data(contentsOf: path),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let events = json["eventos"] as? [[String: Any]]
        else { return nil }
        var best: Meeting?
        for e in events {
            guard (e["es_reunion"] as? Bool) == true,
                  let s = (e["inicio"] as? String).flatMap(parse),
                  let f = (e["fin"] as? String).flatMap(parse)
            else { continue }
            if now >= s.addingTimeInterval(-before) && now <= f.addingTimeInterval(after) {
                let m = Meeting(uid: "\(e["uid"] as? String ?? "")|\(e["inicio"] as? String ?? "")",
                                title: e["titulo"] as? String ?? "reunión", start: s, end: f)
                // si se cruzan dos, la que empezó más cerca de ahora
                if best == nil || abs(m.start.timeIntervalSince(now)) < abs(best!.start.timeIntervalSince(now)) {
                    best = m
                }
            }
        }
        return best
    }

    /// Lanza el sincronizador del calendario sin bloquear (lo configura calendar_sync).
    static func sync(fresh: Bool = false) {
        guard let cmd = Config.calendarSync() else { return }
        run(cmd + (fresh ? " --fresco" : ""))
    }

    /// Revisa si llegaron grabaciones del teléfono (Telegram o iCloud Drive).
    static func phoneScan() {
        guard let cmd = Config.phoneScan() else { return }
        run(cmd)
    }

    private static func run(_ cmd: String) {
        let task = Process()
        task.launchPath = "/bin/sh"
        task.arguments = ["-c", cmd + " >/dev/null 2>&1"]
        try? task.run()
    }
}
