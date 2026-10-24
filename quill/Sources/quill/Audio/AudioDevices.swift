import CoreAudio
import Foundation

/// Core Audio helpers to pin capture to a specific input device.
///
/// Why this exists: AVAudioEngine records the *system default* input device.
/// When AirPods (or any Bluetooth headset) are connected and a meeting app
/// (Meet/Zoom) is in a call, macOS switches the default input to the Bluetooth
/// HFP mic — which the meeting app holds exclusively. quill, recording "the
/// default input", then captures digital silence. Pinning the built-in mic
/// sidesteps the contention entirely: the meeting keeps the Bluetooth mic,
/// quill records the always-available built-in one.
enum AudioDevices {
    /// The built-in microphone's device ID, or nil if none is found (e.g. a
    /// Mac with no internal mic and only external inputs).
    static func builtInInputDeviceID() -> AudioDeviceID? {
        for dev in allDeviceIDs() where hasInputChannels(dev) {
            if transportType(dev) == kAudioDeviceTransportTypeBuiltIn {
                return dev
            }
        }
        return nil
    }

    private static func allDeviceIDs() -> [AudioDeviceID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDevices,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        ) == noErr else { return [] }

        let count = Int(size) / MemoryLayout<AudioDeviceID>.size
        var devices = [AudioDeviceID](repeating: 0, count: count)
        guard AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &devices
        ) == noErr else { return [] }
        return devices
    }

    private static func hasInputChannels(_ dev: AudioDeviceID) -> Bool {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyStreamConfiguration,
            mScope: kAudioObjectPropertyScopeInput,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(dev, &address, 0, nil, &size) == noErr, size > 0
        else { return false }

        let raw = UnsafeMutableRawPointer.allocate(
            byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment
        )
        defer { raw.deallocate() }
        let listPtr = raw.assumingMemoryBound(to: AudioBufferList.self)
        guard AudioObjectGetPropertyData(dev, &address, 0, nil, &size, listPtr) == noErr
        else { return false }

        let buffers = UnsafeMutableAudioBufferListPointer(listPtr)
        var channels: UInt32 = 0
        for buf in buffers { channels += buf.mNumberChannels }
        return channels > 0
    }

    private static func transportType(_ dev: AudioDeviceID) -> UInt32 {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyTransportType,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var transport: UInt32 = 0
        var size = UInt32(MemoryLayout<UInt32>.size)
        _ = AudioObjectGetPropertyData(dev, &address, 0, nil, &size, &transport)
        return transport
    }

    /// A human-readable device name for logging.
    static func name(of dev: AudioDeviceID) -> String {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var name: CFString = "" as CFString
        var size = UInt32(MemoryLayout<CFString>.size)
        guard AudioObjectGetPropertyData(dev, &address, 0, nil, &size, &name) == noErr
        else { return "device \(dev)" }
        return name as String
    }
}
