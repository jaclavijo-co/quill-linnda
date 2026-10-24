import AppKit
import CoreAudio
import Foundation

/// Detecta si hay una llamada en curso: alguna app de llamadas (navegador con
/// Meet, Zoom, Teams, Slack, WhatsApp…) está capturando del micrófono. Usa los
/// objetos de proceso de Core Audio (macOS 14+), sin permisos extra.
enum CallDetector {
    /// Bundle IDs (o "pid N") de todos los procesos, salvo quill, que están
    /// capturando audio de entrada en este momento.
    static func micUsers() -> [String] {
        let me = ProcessInfo.processInfo.processIdentifier
        var out: [String] = []
        for obj in processObjects() {
            let p = pid(of: obj)
            if p == me || !isRunningInput(obj) { continue }
            out.append(bundleID(of: obj, pid: p))
        }
        return out
    }

    /// Solo las apps de llamadas configuradas (Config.callApps, por subcadena).
    static func callAppsUsingMic() -> [String] {
        let apps = Config.callApps()
        return micUsers().filter { id in
            let low = id.lowercased()
            return apps.contains { low.contains($0) }
        }
    }

    private static func processObjects() -> [AudioObjectID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyProcessObjectList,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        ) == noErr, size > 0 else { return [] }
        var list = [AudioObjectID](repeating: 0, count: Int(size) / MemoryLayout<AudioObjectID>.size)
        guard AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &list
        ) == noErr else { return [] }
        return list
    }

    private static func pid(of obj: AudioObjectID) -> pid_t {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyPID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value: pid_t = -1
        var size = UInt32(MemoryLayout<pid_t>.size)
        _ = AudioObjectGetPropertyData(obj, &address, 0, nil, &size, &value)
        return value
    }

    private static func isRunningInput(_ obj: AudioObjectID) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyIsRunningInput,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var value: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        guard AudioObjectGetPropertyData(obj, &address, 0, nil, &size, &value) == noErr
        else { return false }
        return value != 0
    }

    private static func bundleID(of obj: AudioObjectID, pid: pid_t) -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioProcessPropertyBundleID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var name: CFString = "" as CFString
        var size = UInt32(MemoryLayout<CFString>.size)
        if AudioObjectGetPropertyData(obj, &address, 0, nil, &size, &name) == noErr {
            let s = name as String
            if !s.isEmpty { return s }
        }
        if let id = NSRunningApplication(processIdentifier: pid)?.bundleIdentifier {
            return id
        }
        return "pid \(pid)"
    }
}
