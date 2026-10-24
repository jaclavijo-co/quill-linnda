import AVFoundation
import Foundation

/// Motor de transcripción DESHABILITADO.
///
/// El Parakeet original (FluidAudio, Core ML, inglés) se eliminó: este
/// notetaker de Linnda transcribe en español fuera de proceso, en el hook
/// on_stop (faster-whisper large-v3). Quill queda como grabador puro y la
/// transcripción interna está apagada en ~/.config/quill/config.json
/// ("transcription": { "enabled": false }).
///
/// Este stub existe solo para que el TranscriptionCoordinator siga
/// compilando; si alguien reactivara la transcripción interna en el config,
/// prepare() lanza un error claro en lugar de fallar en silencio.
actor ParakeetEngine: TranscriptionEngine {
    enum EngineError: Error, CustomStringConvertible {
        case disabled

        var description: String {
            switch self {
            case .disabled:
                return "transcripción interna deshabilitada — este notetaker transcribe en español vía el hook on_stop (faster-whisper). Deja \"transcription.enabled\": false en el config."
            }
        }
    }

    nonisolated let name = "disabled"
    nonisolated let model = "none (transcripción externa vía on_stop)"

    func prepare() async throws {
        throw EngineError.disabled
    }

    func transcribe(_ audio: URL) async throws -> [TranscriptSegment] {
        throw EngineError.disabled
    }

    func release() async {}
}
