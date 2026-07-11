import AppKit
import CoreMedia
import CoreVideo
import Foundation
import Network
import ScreenCaptureKit

private let codexBundleIdentifier = "com.openai.codex"
private let probePort = NWEndpoint.Port(rawValue: 17683)!

struct ScrubTrigger: Decodable {
    let seq: Int
    let head: String
    let steps: Int
    let monotonic_ns: UInt64
}

struct PendingTrigger {
    let event: ScrubTrigger
    let baseline: [UInt8]
}

final class ScrubLatencyProbe: NSObject, SCStreamOutput, SCStreamDelegate {
    private let stallThresholdNanoseconds: UInt64 = 40_000_000
    private let lock = NSLock()
    private var latestSignature: [UInt8] = []
    private var pending: PendingTrigger?
    private var lastFrameAt: UInt64?
    private var listener: NWListener?

    func startTriggerListener() throws {
        let listener = try NWListener(using: .udp, on: probePort)
        listener.newConnectionHandler = { [weak self] connection in
            connection.start(queue: .global(qos: .userInteractive))
            self?.receive(on: connection)
        }
        listener.start(queue: .global(qos: .userInteractive))
        self.listener = listener
    }

    private func receive(on connection: NWConnection) {
        connection.receiveMessage { [weak self] data, _, _, error in
            if let data,
               let event = try? JSONDecoder().decode(ScrubTrigger.self, from: data),
               event.head == "opus" {
                self?.lock.lock()
                if self?.pending == nil {
                    let baseline = self?.latestSignature ?? []
                    self?.pending = PendingTrigger(
                        event: event,
                        baseline: baseline
                    )
                }
                self?.lock.unlock()
            }
            if error == nil {
                self?.receive(on: connection)
            }
        }
    }

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of outputType: SCStreamOutputType
    ) {
        guard
            outputType == .screen,
            sampleBuffer.isValid,
            let pixelBuffer = sampleBuffer.imageBuffer
        else {
            return
        }

        let now = DispatchTime.now().uptimeNanoseconds
        let signature = sampleSignature(pixelBuffer)

        lock.lock()
        let previousFrameAt = lastFrameAt
        lastFrameAt = now
        latestSignature = signature
        guard let pending else {
            lock.unlock()
            return
        }

        let difference = signatureDifference(
            before: pending.baseline,
            after: signature
        )
        let latencyNanoseconds = now - pending.event.monotonic_ns
        let changed = difference.changedSamples >= 24 &&
            difference.totalDelta >= 1_000
        let timedOut = latencyNanoseconds >= stallThresholdNanoseconds
        guard changed || timedOut else {
            lock.unlock()
            return
        }
        self.pending = nil
        lock.unlock()

        let latency = Double(latencyNanoseconds) / 1_000_000
        let frameInterval = previousFrameAt.map {
            Double(now - $0) / 1_000_000
        }
        let record: [String: Any] = [
            "status": changed ? "observed" : "stalled",
            "seq": pending.event.seq,
            "steps": pending.event.steps,
            "capture_callback_ms": round(latency * 1_000) / 1_000,
            "frame_interval_ms": frameInterval.map {
                round($0 * 1_000) / 1_000
            } as Any,
            "changed_samples": difference.changedSamples,
            "total_delta": difference.totalDelta,
            "measured_at": Date().timeIntervalSince1970,
        ]
        emit(record)
    }

    private func emit(_ record: [String: Any]) {
        guard
            let data = try? JSONSerialization.data(withJSONObject: record),
            let line = String(data: data, encoding: .utf8)
        else {
            return
        }
        print(line)
        fflush(stdout)
        try? data.write(
            to: URL(fileURLWithPath: "/tmp/dj-scrub-verifier.json"),
            options: .atomic
        )
    }

    private func sampleSignature(_ pixelBuffer: CVPixelBuffer) -> [UInt8] {
        CVPixelBufferLockBaseAddress(pixelBuffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pixelBuffer, .readOnly) }

        guard let base = CVPixelBufferGetBaseAddress(pixelBuffer) else {
            return []
        }
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(pixelBuffer)
        let pixels = base.assumingMemoryBound(to: UInt8.self)

        // Central transcript only: exclude the sidebar, titlebar, composer,
        // cursor, and the tape. A scroll changes most samples in this region.
        let x0 = width * 30 / 100
        let x1 = width * 96 / 100
        let y0 = height * 8 / 100
        let y1 = height * 76 / 100
        let columns = 32
        let rows = 20

        var signature: [UInt8] = []
        signature.reserveCapacity(columns * rows)
        for row in 0..<rows {
            let y = y0 + (y1 - y0) * row / max(1, rows - 1)
            for column in 0..<columns {
                let x = x0 + (x1 - x0) * column / max(1, columns - 1)
                let offset = y * bytesPerRow + x * 4
                let blue = UInt16(pixels[offset])
                let green = UInt16(pixels[offset + 1])
                let red = UInt16(pixels[offset + 2])
                signature.append(UInt8((red + green * 2 + blue) / 4))
            }
        }
        return signature
    }

    private func signatureDifference(
        before: [UInt8],
        after: [UInt8]
    ) -> (changedSamples: Int, totalDelta: Int) {
        guard before.count == after.count, !before.isEmpty else {
            return (0, 0)
        }
        var changedSamples = 0
        var totalDelta = 0
        for (left, right) in zip(before, after) {
            let delta = abs(Int(left) - Int(right))
            totalDelta += delta
            if delta >= 6 {
                changedSamples += 1
            }
        }
        return (changedSamples, totalDelta)
    }
}

@main
struct ScrubLatencyProbeMain {
    static func main() async throws {
        _ = NSApplication.shared
        NSApplication.shared.setActivationPolicy(.prohibited)
        let content = try await SCShareableContent.excludingDesktopWindows(
            false,
            onScreenWindowsOnly: true
        )
        guard let window = content.windows
            .filter({ window in
                window.owningApplication?.bundleIdentifier == codexBundleIdentifier
            })
            .max(by: { left, right in
                left.frame.width * left.frame.height < right.frame.width * right.frame.height
            })
        else {
            throw NSError(
                domain: "ScrubLatencyProbe",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "Codex window not found"]
            )
        }

        let configuration = SCStreamConfiguration()
        configuration.width = 688
        configuration.height = 960
        configuration.minimumFrameInterval = CMTime(value: 1, timescale: 175)
        configuration.queueDepth = 8
        configuration.pixelFormat = kCVPixelFormatType_32BGRA
        configuration.showsCursor = false
        configuration.capturesAudio = false

        let probe = ScrubLatencyProbe()
        try probe.startTriggerListener()
        let filter = SCContentFilter(desktopIndependentWindow: window)
        let stream = SCStream(
            filter: filter,
            configuration: configuration,
            delegate: probe
        )
        try stream.addStreamOutput(
            probe,
            type: .screen,
            sampleHandlerQueue: .global(qos: .userInteractive)
        )
        try await stream.startCapture()

        FileManager.default.createFile(
            atPath: "/tmp/dj-scrub-probe.enabled",
            contents: Data()
        )
        print("scrub_probe ready port=17683 requested_fps=175")
        fflush(stdout)

        while true {
            try await Task.sleep(nanoseconds: 3_600_000_000_000)
        }
    }
}
