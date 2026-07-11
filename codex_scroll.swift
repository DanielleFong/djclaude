import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

private let defaultBundleIdentifier = "com.openai.codex"
private let defaultThreadTitle = ProcessInfo.processInfo.environment[
    "CODEX_THREAD_TITLE"
] ?? "Better Cal Sol"
private let configuredThreadIdentifier = ProcessInfo.processInfo.environment[
    "CODEX_THREAD_ID"
] ?? "unknown"
private let defaultHorizontalPosition = 0.62
private let defaultVerticalPosition = 0.48
private let domClassListAttribute = "AXDOMClassList" as CFString
private let threadScrollContainerClass = "thread-scroll-container"
private let enhancedUserInterfaceAttribute = "AXEnhancedUserInterface" as CFString
private let scrollToVisibleAction = "AXScrollToVisible" as CFString
private let cancelAction = "AXCancel" as CFString

struct ScrollTarget {
    let pid: pid_t
    let point: CGPoint
    let windowIdentifier: CGWindowID?
}

struct SemanticScrollReceipt {
    let index: Int
    let count: Int
    let summary: String
    let observed: Bool
}

struct EffortReceipt {
    let status: String
    let requested: String
    let current: String
    let reason: String
}

enum ScrollBridgeError: Error, CustomStringConvertible {
    case applicationNotRunning(String)
    case composerNotFound
    case composerWriteFailed(AXError)
    case eventAccessDenied
    case windowNotFound
    case windowFrameUnavailable
    case eventCreationFailed
    case semanticScrollFailed(AXError)
    case submitButtonNotFound
    case submitFailed(AXError)

    var description: String {
        switch self {
        case .applicationNotRunning(let bundleIdentifier):
            return "application is not running: \(bundleIdentifier)"
        case .composerNotFound:
            return "Codex composer text area was not found"
        case .composerWriteFailed(let error):
            return "could not write the Codex composer: AX error \(error.rawValue)"
        case .eventAccessDenied:
            return "macOS event access is not available to the DJ daemon"
        case .windowNotFound:
            return "Codex window not found through Accessibility"
        case .windowFrameUnavailable:
            return "Codex window frame is unavailable"
        case .eventCreationFailed:
            return "could not create a CoreGraphics scroll event"
        case .semanticScrollFailed(let error):
            return "could not navigate the Codex transcript: AX error \(error.rawValue)"
        case .submitButtonNotFound:
            return "Codex submit button was not found beside the composer"
        case .submitFailed(let error):
            return "could not press the Codex submit button: AX error \(error.rawValue)"
        }
    }
}

func copyAttribute<T>(_ element: AXUIElement, _ attribute: CFString) -> T? {
    var value: CFTypeRef?
    guard AXUIElementCopyAttributeValue(element, attribute, &value) == .success else {
        return nil
    }
    return value as? T
}

func copyPoint(_ element: AXUIElement, _ attribute: CFString) -> CGPoint? {
    guard let value: AXValue = copyAttribute(element, attribute) else {
        return nil
    }
    var point = CGPoint.zero
    guard AXValueGetValue(value, .cgPoint, &point) else {
        return nil
    }
    return point
}

func copySize(_ element: AXUIElement, _ attribute: CFString) -> CGSize? {
    guard let value: AXValue = copyAttribute(element, attribute) else {
        return nil
    }
    var size = CGSize.zero
    guard AXValueGetValue(value, .cgSize, &size) else {
        return nil
    }
    return size
}

func enableEnhancedUserInterface(_ application: AXUIElement) {
    AXUIElementSetAttributeValue(
        application,
        enhancedUserInterfaceAttribute,
        kCFBooleanTrue
    )
}

func findWindow(in application: AXUIElement) -> AXUIElement? {
    if let focusedWindow: AXUIElement = copyAttribute(
        application,
        kAXFocusedWindowAttribute as CFString
    ) {
        return focusedWindow
    }

    let windows: [AXUIElement] = copyAttribute(
        application,
        kAXWindowsAttribute as CFString
    ) ?? []
    return windows.first
}

func findThreadHeader(_ expected: String, in window: AXUIElement) -> AXUIElement? {
    guard
        let windowOrigin = copyPoint(window, kAXPositionAttribute as CFString),
        let windowSize = copySize(window, kAXSizeAttribute as CFString)
    else {
        return nil
    }
    let windowFrame = CGRect(origin: windowOrigin, size: windowSize)
    return findElements(withRole: kAXStaticTextRole as String, in: window)
        .first { text in
            let value: String = copyAttribute(
                text,
                kAXValueAttribute as CFString
            ) ?? ""
            guard
                value == expected,
                let origin = copyPoint(text, kAXPositionAttribute as CFString),
                let size = copySize(text, kAXSizeAttribute as CFString),
                size.height > 2
            else {
                return false
            }
            // The task title lives in the toolbar row. Transcript text begins
            // below it and must never be allowed to impersonate task identity.
            return origin.y <= windowFrame.minY + 48 &&
                origin.x >= windowFrame.minX + 250
        }
}

func containsThreadHeader(_ expected: String, in window: AXUIElement) -> Bool {
    findThreadHeader(expected, in: window) != nil
}

func findThreadWindow(
    in application: AXUIElement,
    title: String = defaultThreadTitle
) -> AXUIElement? {
    let windows: [AXUIElement] = copyAttribute(
        application,
        kAXWindowsAttribute as CFString
    ) ?? []
    let matches = windows.filter { window in
        containsThreadHeader(title, in: window)
    }
    return matches.count == 1 ? matches[0] : nil
}

func findTargetWindow(in application: AXUIElement) -> AXUIElement? {
    findThreadWindow(in: application)
}

func hasThreadScrollContainerClass(_ element: AXUIElement) -> Bool {
    if let classes: [String] = copyAttribute(element, domClassListAttribute) {
        return classes.contains(threadScrollContainerClass)
    }
    if let classes: String = copyAttribute(element, domClassListAttribute) {
        return classes.split(separator: " ").contains {
            $0 == Substring(threadScrollContainerClass)
        }
    }
    return false
}

func findThreadScrollContainer(
    in element: AXUIElement,
    remainingDepth: Int = 32
) -> AXUIElement? {
    if hasThreadScrollContainerClass(element) {
        return element
    }
    guard remainingDepth > 0 else {
        return nil
    }

    let children: [AXUIElement] = copyAttribute(
        element,
        kAXChildrenAttribute as CFString
    ) ?? []
    for child in children {
        if let match = findThreadScrollContainer(
            in: child,
            remainingDepth: remainingDepth - 1
        ) {
            return match
        }
    }
    return nil
}

func findTextAreas(
    in element: AXUIElement,
    remainingDepth: Int = 32
) -> [AXUIElement] {
    guard remainingDepth > 0 else {
        return []
    }

    let role: String? = copyAttribute(element, kAXRoleAttribute as CFString)
    var matches = role == (kAXTextAreaRole as String) ? [element] : []
    let children: [AXUIElement] = copyAttribute(
        element,
        kAXChildrenAttribute as CFString
    ) ?? []
    for child in children {
        matches.append(contentsOf: findTextAreas(
            in: child,
            remainingDepth: remainingDepth - 1
        ))
    }
    return matches
}

func findElements(
    withRole expectedRole: String,
    in element: AXUIElement,
    remainingDepth: Int = 32
) -> [AXUIElement] {
    guard remainingDepth > 0 else {
        return []
    }

    let role: String? = copyAttribute(element, kAXRoleAttribute as CFString)
    var matches = role == expectedRole ? [element] : []
    let children: [AXUIElement] = copyAttribute(
        element,
        kAXChildrenAttribute as CFString
    ) ?? []
    for child in children {
        matches.append(contentsOf: findElements(
            withRole: expectedRole,
            in: child,
            remainingDepth: remainingDepth - 1
        ))
    }
    return matches
}

func textAnchors(in element: AXUIElement) -> [AXUIElement] {
    findElements(withRole: kAXStaticTextRole as String, in: element)
        .filter { anchor in
            let value: String = copyAttribute(
                anchor,
                kAXValueAttribute as CFString
            ) ?? ""
            return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        }
}

func semanticAnchors(in thread: AXUIElement) -> [AXUIElement] {
    textAnchors(in: thread).filter { anchor in
        let value: String = copyAttribute(
            anchor,
            kAXValueAttribute as CFString
        ) ?? ""
        let size = copySize(anchor, kAXSizeAttribute as CFString) ?? .zero
        return value.count >= 24 && size.width >= 100
    }
}

struct ResolvedThread {
    let application: AXUIElement
    let window: AXUIElement
    let header: AXUIElement
    let thread: AXUIElement
}

func resolveThreadElements() throws -> ResolvedThread {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)

    let windows: [AXUIElement] = copyAttribute(
        applicationElement,
        kAXWindowsAttribute as CFString
    ) ?? []
    let matches = windows.compactMap { window -> (AXUIElement, AXUIElement)? in
        guard let header = findThreadHeader(defaultThreadTitle, in: window) else {
            return nil
        }
        return (window, header)
    }
    guard matches.count == 1 else {
        throw ScrollBridgeError.windowNotFound
    }

    let (window, header) = matches[0]
    guard let thread = findThreadScrollContainer(in: window) else {
        throw ScrollBridgeError.windowNotFound
    }
    return ResolvedThread(
        application: applicationElement,
        window: window,
        header: header,
        thread: thread
    )
}

func resolveThread() throws -> AXUIElement {
    try resolveThreadElements().thread
}

func currentAnchorIndex(
    anchors: [AXUIElement],
    thread: AXUIElement
) -> Int {
    guard
        let threadOrigin = copyPoint(thread, kAXPositionAttribute as CFString),
        let threadSize = copySize(thread, kAXSizeAttribute as CFString)
    else {
        return max(0, anchors.count - 1)
    }

    let threadFrame = CGRect(origin: threadOrigin, size: threadSize)
    let centerY = threadFrame.midY
    let visible = anchors.enumerated().compactMap { index, anchor -> (Int, CGFloat)? in
        guard
            let origin = copyPoint(anchor, kAXPositionAttribute as CFString),
            let size = copySize(anchor, kAXSizeAttribute as CFString)
        else {
            return nil
        }
        let frame = CGRect(origin: origin, size: size)
        guard size.height > 2, frame.intersects(threadFrame) else {
            return nil
        }
        return (index, abs(frame.midY - centerY))
    }
    return visible.min { $0.1 < $1.1 }?.0 ?? max(0, anchors.count - 1)
}

func visibleAnchorValues(
    anchors: [AXUIElement],
    thread: AXUIElement
) -> [String] {
    guard
        let threadOrigin = copyPoint(thread, kAXPositionAttribute as CFString),
        let threadSize = copySize(thread, kAXSizeAttribute as CFString)
    else {
        return []
    }
    let threadFrame = CGRect(origin: threadOrigin, size: threadSize)
    return anchors.compactMap { anchor in
        guard
            let origin = copyPoint(anchor, kAXPositionAttribute as CFString),
            let size = copySize(anchor, kAXSizeAttribute as CFString),
            size.height > 2,
            CGRect(origin: origin, size: size).intersects(threadFrame)
        else {
            return nil
        }
        let value: String = copyAttribute(
            anchor,
            kAXValueAttribute as CFString
        ) ?? ""
        return value
    }
}

func scrollToAnchor(
    index: Int,
    anchors: [AXUIElement],
    thread: AXUIElement
) throws -> SemanticScrollReceipt {
    guard !anchors.isEmpty else {
        throw ScrollBridgeError.windowNotFound
    }
    let targetIndex = max(0, min(anchors.count - 1, index))
    let target = anchors[targetIndex]
    let beforeValues = visibleAnchorValues(anchors: anchors, thread: thread)
    let status = AXUIElementPerformAction(
        target,
        scrollToVisibleAction
    )
    guard status == .success else {
        throw ScrollBridgeError.semanticScrollFailed(status)
    }

    let value: String = copyAttribute(target, kAXValueAttribute as CFString) ?? ""
    Thread.sleep(forTimeInterval: 0.08)

    let refreshedThread = try resolveThread()
    let refreshedAnchors = semanticAnchors(in: refreshedThread)
    let afterValues = visibleAnchorValues(
        anchors: refreshedAnchors,
        thread: refreshedThread
    )
    let visibleMatch = refreshedAnchors.enumerated().first { _, anchor in
        let candidateValue: String = copyAttribute(
            anchor,
            kAXValueAttribute as CFString
        ) ?? ""
        guard candidateValue == value else {
            return false
        }
        let threadOrigin = copyPoint(
            refreshedThread,
            kAXPositionAttribute as CFString
        ) ?? .zero
        let threadSize = copySize(
            refreshedThread,
            kAXSizeAttribute as CFString
        ) ?? .zero
        let origin = copyPoint(anchor, kAXPositionAttribute as CFString) ?? .zero
        let size = copySize(anchor, kAXSizeAttribute as CFString) ?? .zero
        return size.height > 2 && CGRect(origin: origin, size: size).intersects(
            CGRect(origin: threadOrigin, size: threadSize)
        )
    }
    let summary = String(
        value.replacingOccurrences(of: "\n", with: " ").prefix(80)
    )
    return SemanticScrollReceipt(
        index: visibleMatch?.offset ?? targetIndex,
        count: refreshedAnchors.count,
        summary: summary,
        observed: beforeValues != afterValues
    )
}

func semanticScroll(
    step: Int? = nil,
    fraction: Double? = nil
) throws -> SemanticScrollReceipt {
    let thread = try resolveThread()
    let anchors = semanticAnchors(in: thread)
    guard !anchors.isEmpty else {
        throw ScrollBridgeError.windowNotFound
    }

    let targetIndex: Int
    if let fraction {
        let bounded = max(0, min(1, fraction))
        targetIndex = Int((Double(anchors.count - 1) * bounded).rounded())
    } else {
        let current = currentAnchorIndex(anchors: anchors, thread: thread)
        targetIndex = current + (step ?? 0)
    }
    return try scrollToAnchor(index: targetIndex, anchors: anchors, thread: thread)
}

func printSemanticReceipt(_ receipt: SemanticScrollReceipt) {
    print(
        "semantic_scroll status=\(receipt.observed ? "observed" : "attempted") " +
        "index=\(receipt.index) count=\(receipt.count) " +
        "configured_thread_id=\(configuredThreadIdentifier) " +
        "summary=\(receipt.summary.debugDescription)"
    )
    fflush(stdout)
}

struct FastSemanticScrollReceipt {
    let status: String
    let requestedStep: Int?
    let previousIndex: Int
    let index: Int
    let count: Int
    let summary: String
    let dispatchMilliseconds: Double
}

/// A long-lived, fail-closed Accessibility session for the platter hot path.
///
/// Resolving Chromium's tree and enumerating every transcript anchor costs
/// hundreds of milliseconds. Those operations belong at startup, on explicit
/// refresh, or after a stale AX element error — never between a platter tick
/// and its visual response.
final class FastSemanticScroller {
    private var header: AXUIElement
    private var thread: AXUIElement
    private var anchors: [AXUIElement]
    private var summaries: [String]
    private var currentIndex: Int

    init() throws {
        let resolved = try resolveThreadElements()
        let anchors = semanticAnchors(in: resolved.thread)
        guard !anchors.isEmpty else {
            throw ScrollBridgeError.windowNotFound
        }

        self.header = resolved.header
        self.thread = resolved.thread
        self.anchors = anchors
        self.summaries = FastSemanticScroller.summaries(for: anchors)
        self.currentIndex = currentAnchorIndex(
            anchors: anchors,
            thread: resolved.thread
        )
    }

    var index: Int { currentIndex }
    var count: Int { anchors.count }

    func refresh() throws {
        let resolved = try resolveThreadElements()
        let refreshedAnchors = semanticAnchors(in: resolved.thread)
        guard !refreshedAnchors.isEmpty else {
            throw ScrollBridgeError.windowNotFound
        }

        header = resolved.header
        thread = resolved.thread
        anchors = refreshedAnchors
        summaries = FastSemanticScroller.summaries(for: refreshedAnchors)
        currentIndex = currentAnchorIndex(
            anchors: refreshedAnchors,
            thread: resolved.thread
        )
    }

    func scroll(
        step: Int? = nil,
        fraction: Double? = nil
    ) throws -> FastSemanticScrollReceipt {
        let started = DispatchTime.now().uptimeNanoseconds
        if !identityIsCurrent() {
            try refresh()
        }

        let previousIndex = currentIndex
        var targetIndex = boundedTargetIndex(step: step, fraction: fraction)
        var status = AXUIElementPerformAction(
            anchors[targetIndex],
            scrollToVisibleAction
        )

        // DOM replacement can invalidate cached nodes while leaving the exact
        // task open. Re-resolve once, preserving the task-title identity gate.
        if status != .success {
            try refresh()
            targetIndex = boundedTargetIndex(step: step, fraction: fraction)
            status = AXUIElementPerformAction(
                anchors[targetIndex],
                scrollToVisibleAction
            )
        }
        guard status == .success else {
            throw ScrollBridgeError.semanticScrollFailed(status)
        }

        currentIndex = targetIndex
        let elapsed = DispatchTime.now().uptimeNanoseconds - started
        let actualDelta = targetIndex - previousIndex
        let receiptStatus: String
        if let step, step != 0, actualDelta == 0 {
            receiptStatus = "stalled"
        } else if let step,
                  step != 0,
                  (actualDelta > 0) != (step > 0) {
            receiptStatus = "direction_mismatch"
        } else {
            receiptStatus = "dispatched"
        }
        return FastSemanticScrollReceipt(
            status: receiptStatus,
            requestedStep: step,
            previousIndex: previousIndex,
            index: targetIndex,
            count: anchors.count,
            summary: summaries[targetIndex],
            dispatchMilliseconds: Double(elapsed) / 1_000_000
        )
    }

    private func identityIsCurrent() -> Bool {
        let title: String? = copyAttribute(
            header,
            kAXValueAttribute as CFString
        )
        return title == defaultThreadTitle
    }

    private func boundedTargetIndex(
        step: Int?,
        fraction: Double?
    ) -> Int {
        if let fraction {
            let boundedFraction = max(0, min(1, fraction))
            return Int(
                (Double(anchors.count - 1) * boundedFraction).rounded()
            )
        }
        return max(0, min(anchors.count - 1, currentIndex + (step ?? 0)))
    }

    private static func summaries(for anchors: [AXUIElement]) -> [String] {
        anchors.map { anchor in
            let value: String = copyAttribute(
                anchor,
                kAXValueAttribute as CFString
            ) ?? ""
            return String(
                value.replacingOccurrences(of: "\n", with: " ").prefix(80)
            )
        }
    }
}

func printFastSemanticReceipt(_ receipt: FastSemanticScrollReceipt) {
    let requested = receipt.requestedStep.map(String.init) ?? "fraction"
    let actualDelta = receipt.index - receipt.previousIndex
    print(
        "semantic_scroll status=\(receipt.status) " +
        "requested=\(requested) previous_index=\(receipt.previousIndex) " +
        "index=\(receipt.index) delta=\(actualDelta) count=\(receipt.count) " +
        "dispatch_ms=\(String(format: "%.3f", receipt.dispatchMilliseconds)) " +
        "configured_thread_id=\(configuredThreadIdentifier) " +
        "summary=\(receipt.summary.debugDescription)"
    )
    fflush(stdout)
}

private let codexEffortLevels = [
    "low",
    "medium",
    "high",
    "xhigh",
    "ultra",
]

func effortFromPickerTitle(_ title: String) -> String? {
    let normalized = title.lowercased()
    if normalized.contains("extra high") || normalized.contains("xhigh") {
        return "xhigh"
    }
    let words = normalized.split(separator: " ")
    if words.contains("ultra") { return "ultra" }
    if words.contains("high") { return "high" }
    if words.contains("medium") { return "medium" }
    if words.contains("light") || words.contains("low") { return "low" }
    return nil
}

func effortOptionMatches(_ item: AXUIElement, requested: String) -> Bool {
    let title: String = copyAttribute(item, kAXTitleAttribute as CFString) ?? ""
    switch requested {
    case "low":
        return title == "Light"
    case "medium":
        return title == "Medium"
    case "high":
        return title == "High"
    case "xhigh":
        return title == "Extra High"
    case "ultra":
        return title == "Ultra" || title.hasPrefix("Ultra ")
    default:
        return false
    }
}

func setCodexEffort(_ requested: String) throws -> EffortReceipt {
    guard codexEffortLevels.contains(requested) else {
        return EffortReceipt(
            status: "unsupported",
            requested: requested,
            current: "unknown",
            reason: "unsupported_request"
        )
    }
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)

    guard
        let window = findTargetWindow(in: applicationElement),
        let picker = findEffortPicker(in: window)
    else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: "unknown",
            reason: "target_unavailable"
        )
    }
    let pickerTitle: String = copyAttribute(
        picker,
        kAXTitleAttribute as CFString
    ) ?? ""
    guard let current = effortFromPickerTitle(pickerTitle) else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: "unknown",
            reason: "picker_unreadable"
        )
    }
    if current == requested {
        return EffortReceipt(
            status: "observed",
            requested: requested,
            current: current,
            reason: "already_set"
        )
    }
    if taskIsRunning(in: window) {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: current,
            reason: "task_running"
        )
    }

    let focusedBefore: AXUIElement? = copyAttribute(
        applicationElement,
        kAXFocusedUIElementAttribute as CFString
    )
    let powerItemsBefore = findPowerMenuItems(in: applicationElement)
    let openStatus = AXUIElementPerformAction(picker, kAXPressAction as CFString)
    guard openStatus == .success else {
        throw ScrollBridgeError.semanticScrollFailed(openStatus)
    }
    var menuOpen = true
    var powerForCleanup: AXUIElement?
    defer {
        if menuOpen {
            _ = closeEffortMenu(picker: picker, power: powerForCleanup)
        }
        if let focusedBefore {
            AXUIElementSetAttributeValue(
                focusedBefore,
                kAXFocusedAttribute as CFString,
                kCFBooleanTrue
            )
        }
    }

    Thread.sleep(forTimeInterval: 0.12)
    let newPowerItems = findPowerMenuItems(in: applicationElement).filter { item in
        !powerItemsBefore.contains { existing in
            CFEqual(existing, item)
        } && element(item, isNear: picker)
    }
    guard newPowerItems.count == 1 else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: current,
            reason: "menu_unresolved"
        )
    }
    let power = newPowerItems[0]
    powerForCleanup = power
    let optionsBefore = findElements(
        withRole: kAXMenuItemRole as String,
        in: applicationElement
    )
    let submenuStatus = AXUIElementPerformAction(
        power,
        kAXPressAction as CFString
    )
    guard submenuStatus == .success else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: current,
            reason: "submenu_unavailable"
        )
    }
    Thread.sleep(forTimeInterval: 0.08)
    let requestedOptions = findElements(
        withRole: kAXMenuItemRole as String,
        in: applicationElement
    ).filter { item in
        !optionsBefore.contains { existing in
            CFEqual(existing, item)
        } && effortOptionMatches(item, requested: requested)
    }
    guard requestedOptions.count == 1 else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: current,
            reason: "effort_option_unresolved"
        )
    }
    let selectStatus = AXUIElementPerformAction(
        requestedOptions[0],
        kAXPressAction as CFString
    )
    guard selectStatus == .success else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: current,
            reason: "effort_option_rejected"
        )
    }
    Thread.sleep(forTimeInterval: 0.12)
    let effortMenuStillOpen = findPowerMenuItems(in: applicationElement).contains {
        item in element(item, isNear: picker)
    }
    if effortMenuStillOpen {
        let closed = closeEffortMenu(picker: picker, power: power)
        menuOpen = !closed
    } else {
        menuOpen = false
    }
    guard
        let refreshedWindow = findTargetWindow(in: applicationElement),
        let refreshedPicker = findEffortPicker(in: refreshedWindow)
    else {
        return EffortReceipt(
            status: "deferred",
            requested: requested,
            current: current,
            reason: "verify_target_lost"
        )
    }
    let appliedTitle: String = copyAttribute(
        refreshedPicker,
        kAXTitleAttribute as CFString
    ) ?? ""
    let applied = effortFromPickerTitle(appliedTitle) ?? current
    let status = applied == requested ? "observed" : "deferred"
    return EffortReceipt(
        status: status,
        requested: requested,
        current: applied,
        reason: status == "observed" ? "verified_after_close" : "verify_mismatch"
    )
}

func printEffortReceipt(_ receipt: EffortReceipt) {
    print(
        "effort_control status=\(receipt.status) " +
        "requested=\(receipt.requested) current=\(receipt.current) " +
        "reason=\(receipt.reason) " +
        "configured_thread_id=\(configuredThreadIdentifier)"
    )
    fflush(stdout)
}

func findComposer(in window: AXUIElement) -> AXUIElement? {
    findTextAreas(in: window).max { left, right in
        let leftY = copyPoint(left, kAXPositionAttribute as CFString)?.y ?? 0
        let rightY = copyPoint(right, kAXPositionAttribute as CFString)?.y ?? 0
        return leftY < rightY
    }
}

func findSubmitButton(
    in window: AXUIElement,
    beside composer: AXUIElement
) -> AXUIElement? {
    guard
        let composerOrigin = copyPoint(composer, kAXPositionAttribute as CFString),
        let composerSize = copySize(composer, kAXSizeAttribute as CFString)
    else {
        return nil
    }

    let composerFrame = CGRect(origin: composerOrigin, size: composerSize)
    let candidates = findElements(withRole: kAXButtonRole as String, in: window)
        .filter { button in
            let description: String = copyAttribute(
                button,
                kAXDescriptionAttribute as CFString
            ) ?? ""
            guard let center = frameCenter(of: button) else {
                return false
            }
            return ["send", "submit"].contains(description.lowercased()) &&
                composerFrame.insetBy(dx: -48, dy: -48).contains(center)
        }
    return candidates.max { left, right in
        let leftX = frameCenter(of: left)?.x ?? 0
        let rightX = frameCenter(of: right)?.x ?? 0
        return leftX < rightX
    }
}

func findEffortPicker(in window: AXUIElement) -> AXUIElement? {
    let pickers = findElements(withRole: kAXPopUpButtonRole as String, in: window)
    let matches = pickers.filter { picker in
        let title: String = copyAttribute(
            picker,
            kAXTitleAttribute as CFString
        ) ?? ""
        let words = title.lowercased().split(separator: " ")
        return words.contains("sol") && effortFromPickerTitle(title) != nil
    }
    return matches.count == 1 ? matches[0] : nil
}

func taskIsRunning(in window: AXUIElement) -> Bool {
    findElements(withRole: kAXButtonRole as String, in: window).contains { button in
        let description: String = copyAttribute(
            button,
            kAXDescriptionAttribute as CFString
        ) ?? ""
        return description == "Stop"
    }
}

func findPowerMenuItems(in application: AXUIElement) -> [AXUIElement] {
    findElements(withRole: kAXMenuItemRole as String, in: application)
        .filter { item in
            let description: String = copyAttribute(
                item,
                kAXDescriptionAttribute as CFString
            ) ?? ""
            return description == "Power" || description.hasPrefix("Effort ")
        }
}

func element(_ element: AXUIElement, isNear anchor: AXUIElement) -> Bool {
    guard
        let elementCenter = frameCenter(of: element),
        let anchorOrigin = copyPoint(anchor, kAXPositionAttribute as CFString),
        let anchorSize = copySize(anchor, kAXSizeAttribute as CFString)
    else {
        return false
    }
    let anchorFrame = CGRect(origin: anchorOrigin, size: anchorSize)
    return elementCenter.x >= anchorFrame.minX - 20 &&
        elementCenter.x <= anchorFrame.maxX + 20 &&
        elementCenter.y >= anchorFrame.minY - 180 &&
        elementCenter.y <= anchorFrame.maxY + 30
}

@discardableResult
func closeEffortMenu(
    picker: AXUIElement,
    power: AXUIElement?
) -> Bool {
    if let power,
       AXUIElementPerformAction(power, cancelAction) == .success {
        return true
    }
    return AXUIElementPerformAction(
        picker,
        kAXPressAction as CFString
    ) == .success
}

func postArrowKey(to pid: pid_t, keyCode: CGKeyCode) {
    let source = CGEventSource(stateID: .hidSystemState)
    CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: true)?
        .postToPid(pid)
    CGEvent(keyboardEventSource: source, virtualKey: keyCode, keyDown: false)?
        .postToPid(pid)
}

func focusElement(_ element: AXUIElement) throws {
    let status = AXUIElementSetAttributeValue(
        element,
        kAXFocusedAttribute as CFString,
        kCFBooleanTrue
    )
    guard status == .success else {
        throw ScrollBridgeError.semanticScrollFailed(status)
    }
}

func frameCenter(of element: AXUIElement) -> CGPoint? {
    guard
        let origin = copyPoint(element, kAXPositionAttribute as CFString),
        let size = copySize(element, kAXSizeAttribute as CFString)
    else {
        return nil
    }
    return CGPoint(x: origin.x + size.width / 2, y: origin.y + size.height / 2)
}

func resolveTarget(bundleIdentifier: String) throws -> ScrollTarget {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: bundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(bundleIdentifier)
    }

    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard let window = findTargetWindow(in: applicationElement) else {
        throw ScrollBridgeError.windowNotFound
    }
    if let scrollContainer = findThreadScrollContainer(in: window),
       let point = frameCenter(of: scrollContainer) {
        return ScrollTarget(
            pid: application.processIdentifier,
            point: point,
            windowIdentifier: findWindowIdentifier(pid: application.processIdentifier)
        )
    }

    guard
        let origin = copyPoint(window, kAXPositionAttribute as CFString),
        let size = copySize(window, kAXSizeAttribute as CFString)
    else {
        throw ScrollBridgeError.windowFrameUnavailable
    }

    // Older Codex builds may omit DOM class metadata from Accessibility.
    // This point is a safe fallback inside the central transcript surface.
    let point = CGPoint(
        x: origin.x + size.width * defaultHorizontalPosition,
        y: origin.y + size.height * defaultVerticalPosition
    )
    return ScrollTarget(
        pid: application.processIdentifier,
        point: point,
        windowIdentifier: findWindowIdentifier(pid: application.processIdentifier)
    )
}

func postScroll(
    delta: Int32,
    to target: ScrollTarget,
    globally: Bool = false
) throws {
    guard let event = CGEvent(
        scrollWheelEvent2Source: nil,
        units: .pixel,
        wheelCount: 1,
        wheel1: delta,
        wheel2: 0,
        wheel3: 0
    ) else {
        throw ScrollBridgeError.eventCreationFailed
    }

    // Do not assign event.location here. A global HID event with a synthetic
    // location moves the operator's real mouse pointer on macOS. Route by the
    // Codex window id below and leave pointer position untouched.
    if let windowIdentifier = target.windowIdentifier {
        let value = Int64(windowIdentifier)
        event.setIntegerValueField(.mouseEventWindowUnderMousePointer, value: value)
        event.setIntegerValueField(
            .mouseEventWindowUnderMousePointerThatCanHandleThisEvent,
            value: value
        )
    }
    if globally {
        event.post(tap: .cghidEventTap)
    } else {
        event.postToPid(target.pid)
    }
}

func findWindowIdentifier(pid: pid_t) -> CGWindowID? {
    guard let windowInfo = CGWindowListCopyWindowInfo(
        [.optionOnScreenOnly, .excludeDesktopElements],
        kCGNullWindowID
    ) as? [[CFString: Any]] else {
        return nil
    }

    let matchingWindows = windowInfo.filter { window in
        let ownerPID = window[kCGWindowOwnerPID] as? NSNumber
        let layer = window[kCGWindowLayer] as? NSNumber
        return ownerPID?.int32Value == pid && layer?.intValue == 0
    }
    return matchingWindows.compactMap { window in
        (window[kCGWindowNumber] as? NSNumber).map {
            CGWindowID($0.uint32Value)
        }
    }.first
}

func runOnce(arguments: [String], globally: Bool = false) throws {
    guard let deltaArgument = arguments.first, let delta = Int32(deltaArgument) else {
        fputs("usage: codex-scroll [--check | --once PIXELS]\n", stderr)
        exit(2)
    }
    let target = try resolveTarget(bundleIdentifier: defaultBundleIdentifier)
    try postScroll(delta: delta, to: target, globally: globally)
}

func runUnsafeHIDStream() throws {
    var target = try resolveTarget(bundleIdentifier: defaultBundleIdentifier)
    var lastResolvedAt = Date()

    while let line = readLine() {
        let command = line.trimmingCharacters(in: .whitespacesAndNewlines)
        if command == "refresh" {
            target = try resolveTarget(bundleIdentifier: defaultBundleIdentifier)
            lastResolvedAt = Date()
            print("ready pid=\(target.pid) x=\(Int(target.point.x)) y=\(Int(target.point.y))")
            fflush(stdout)
            continue
        }
        guard let delta = Int32(command) else {
            continue
        }
        if Date().timeIntervalSince(lastResolvedAt) > 2,
           let refreshedTarget = try? resolveTarget(bundleIdentifier: defaultBundleIdentifier) {
            target = refreshedTarget
            lastResolvedAt = Date()
        }
        // Chromium ignores scroll-wheel events posted directly to its PID.
        // HID delivery preserves native wheel semantics and routes through the
        // visible Codex transcript under the resolved target point.
        try postScroll(delta: delta, to: target, globally: true)
    }
}

func runSemanticStream() throws {
    // Best-effort prewarm. If the exact task is not rendered yet, commands
    // continue to fail closed and a later explicit/implicit refresh can bind.
    var scroller = try? FastSemanticScroller()
    if let scroller {
        print(
            "semantic_scroll status=prewarmed " +
            "index=\(scroller.index) count=\(scroller.count) " +
            "configured_thread_id=\(configuredThreadIdentifier)"
        )
        fflush(stdout)
    }

    func currentScroller() throws -> FastSemanticScroller {
        if let scroller {
            return scroller
        }
        let resolved = try FastSemanticScroller()
        scroller = resolved
        return resolved
    }

    while let line = readLine() {
        let command = line.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            if command.hasPrefix("effort ") {
                let effort = String(command.dropFirst("effort ".count))
                printEffortReceipt(try setCodexEffort(effort))
                continue
            }
            let receipt: FastSemanticScrollReceipt
            if command.hasPrefix("step "),
               let step = Int(command.dropFirst("step ".count)) {
                receipt = try currentScroller().scroll(step: step)
            } else if command.hasPrefix("fraction "),
                      let fraction = Double(command.dropFirst("fraction ".count)) {
                receipt = try currentScroller().scroll(fraction: fraction)
            } else if command == "live" {
                receipt = try currentScroller().scroll(fraction: 1)
            } else if command == "start" {
                receipt = try currentScroller().scroll(fraction: 0)
            } else if command == "refresh" {
                let activeScroller = try currentScroller()
                try activeScroller.refresh()
                print(
                    "semantic_scroll status=ready " +
                    "index=\(activeScroller.index) count=\(activeScroller.count)"
                )
                fflush(stdout)
                continue
            } else {
                continue
            }
            printFastSemanticReceipt(receipt)
        } catch {
            fputs("codex-scroll: \(error)\n", stderr)
            fflush(stderr)
        }
    }
}

func runType(arguments: [String]) throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }

    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard
        let window = findTargetWindow(in: applicationElement),
        let composer = findComposer(in: window)
    else {
        throw ScrollBridgeError.composerNotFound
    }

    let data = FileHandle.standardInput.readDataToEndOfFile()
    let text = String(data: data, encoding: .utf8) ?? ""
    let writeStatus = AXUIElementSetAttributeValue(
        composer,
        kAXValueAttribute as CFString,
        text as CFTypeRef
    )
    guard writeStatus == .success else {
        throw ScrollBridgeError.composerWriteFailed(writeStatus)
    }

    guard arguments.contains("--submit") else {
        print("drafted \(text.utf8.count) bytes")
        return
    }

    guard let submitButton = findSubmitButton(in: window, beside: composer) else {
        throw ScrollBridgeError.submitButtonNotFound
    }
    let submitStatus = AXUIElementPerformAction(
        submitButton,
        kAXPressAction as CFString
    )
    guard submitStatus == .success else {
        throw ScrollBridgeError.submitFailed(submitStatus)
    }
    print("submitted \(text.utf8.count) bytes")
}

func inspectComposer() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard
        let window = findTargetWindow(in: applicationElement),
        let composer = findComposer(in: window),
        let composerOrigin = copyPoint(composer, kAXPositionAttribute as CFString),
        let composerSize = copySize(composer, kAXSizeAttribute as CFString)
    else {
        throw ScrollBridgeError.composerNotFound
    }

    print("composer x=\(Int(composerOrigin.x)) y=\(Int(composerOrigin.y)) " +
          "w=\(Int(composerSize.width)) h=\(Int(composerSize.height))")
    let controls = findElements(withRole: kAXButtonRole as String, in: window) +
        findElements(withRole: kAXPopUpButtonRole as String, in: window)
    for button in controls {
        guard
            let origin = copyPoint(button, kAXPositionAttribute as CFString),
            let size = copySize(button, kAXSizeAttribute as CFString),
            origin.y + size.height >= composerOrigin.y - 120
        else {
            continue
        }
        let title: String = copyAttribute(button, kAXTitleAttribute as CFString) ?? ""
        let role: String = copyAttribute(button, kAXRoleAttribute as CFString) ?? ""
        let value: String = copyAttribute(button, kAXValueAttribute as CFString) ?? ""
        let classes: [String] = copyAttribute(button, domClassListAttribute) ?? []
        var valueSettable = DarwinBoolean(false)
        AXUIElementIsAttributeSettable(
            button,
            kAXValueAttribute as CFString,
            &valueSettable
        )
        var actions: CFArray?
        AXUIElementCopyActionNames(button, &actions)
        let description: String = copyAttribute(
            button,
            kAXDescriptionAttribute as CFString
        ) ?? ""
        print("control role=\(role) x=\(Int(origin.x)) y=\(Int(origin.y)) " +
              "w=\(Int(size.width)) h=\(Int(size.height)) " +
              "title=\(title.debugDescription) value=\(value.debugDescription) " +
              "value_settable=\(valueSettable.boolValue) " +
              "description=\(description.debugDescription) " +
              "classes=\(classes) " +
              "actions=\(actions ?? [] as CFArray)")
    }
}

func inspectScrollTarget() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard let window = findTargetWindow(in: applicationElement),
          let target = findThreadScrollContainer(in: window) else {
        throw ScrollBridgeError.windowNotFound
    }
    var actionNames: CFArray?
    let actionError = AXUIElementCopyActionNames(target, &actionNames)
    var attributeNames: CFArray?
    let attributeError = AXUIElementCopyAttributeNames(target, &attributeNames)
    var focusedSettable: DarwinBoolean = false
    let focusedSettableError = AXUIElementIsAttributeSettable(
        target,
        kAXFocusedAttribute as CFString,
        &focusedSettable
    )
    let focused: Bool = copyAttribute(
        target,
        kAXFocusedAttribute as CFString
    ) ?? false
    let customActions: [AXUIElement] = copyAttribute(
        target,
        "AXCustomActions" as CFString
    ) ?? []
    print("actions error=\(actionError.rawValue) \(actionNames ?? [] as CFArray)")
    print("attributes error=\(attributeError.rawValue) \(attributeNames ?? [] as CFArray)")
    print(
        "focused=\(focused) focused_settable=\(focusedSettable.boolValue) " +
        "focused_settable_error=\(focusedSettableError.rawValue) " +
        "custom_actions=\(customActions)"
    )
}

func probePageScroll(keyCode: CGKeyCode) throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(
        application.processIdentifier
    )
    enableEnhancedUserInterface(applicationElement)
    guard
        let window = findTargetWindow(in: applicationElement),
        let scrollContainer = findThreadScrollContainer(in: window)
    else {
        throw ScrollBridgeError.windowNotFound
    }

    let frontmostBefore = NSWorkspace.shared.frontmostApplication?
        .bundleIdentifier ?? ""
    let focusedBefore: AXUIElement? = copyAttribute(
        applicationElement,
        kAXFocusedUIElementAttribute as CFString
    )
    let anchorsBefore = semanticAnchors(in: scrollContainer)
    let visibleBefore = visibleAnchorValues(
        anchors: anchorsBefore,
        thread: scrollContainer
    )

    try focusElement(scrollContainer)
    postArrowKey(to: application.processIdentifier, keyCode: keyCode)
    Thread.sleep(forTimeInterval: 0.18)

    let refreshed = try resolveThread()
    let anchorsAfter = semanticAnchors(in: refreshed)
    let visibleAfter = visibleAnchorValues(
        anchors: anchorsAfter,
        thread: refreshed
    )
    if let focusedBefore {
        AXUIElementSetAttributeValue(
            focusedBefore,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
        )
    }
    let frontmostAfter = NSWorkspace.shared.frontmostApplication?
        .bundleIdentifier ?? ""
    let status = visibleBefore == visibleAfter ? "stalled" : "observed"
    let beforeFirst = String((visibleBefore.first ?? "").prefix(60))
    let afterFirst = String((visibleAfter.first ?? "").prefix(60))
    print(
        "page_scroll status=\(status) " +
        "before_count=\(anchorsBefore.count) after_count=\(anchorsAfter.count) " +
        "before_first=\(beforeFirst.debugDescription) " +
        "after_first=\(afterFirst.debugDescription) " +
        "frontmost_before=\(frontmostBefore) frontmost_after=\(frontmostAfter)"
    )
}

func probeProcessScroll(delta: Int32) throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(
        application.processIdentifier
    )
    enableEnhancedUserInterface(applicationElement)
    guard
        let window = findTargetWindow(in: applicationElement),
        let scrollContainer = findThreadScrollContainer(in: window)
    else {
        throw ScrollBridgeError.windowNotFound
    }

    let frontmostBefore = NSWorkspace.shared.frontmostApplication?
        .bundleIdentifier ?? ""
    let focusedBefore: AXUIElement? = copyAttribute(
        applicationElement,
        kAXFocusedUIElementAttribute as CFString
    )
    let anchorsBefore = semanticAnchors(in: scrollContainer)
    let visibleBefore = visibleAnchorValues(
        anchors: anchorsBefore,
        thread: scrollContainer
    )

    try focusElement(scrollContainer)
    let target = ScrollTarget(
        pid: application.processIdentifier,
        point: frameCenter(of: scrollContainer) ?? .zero,
        windowIdentifier: findWindowIdentifier(pid: application.processIdentifier)
    )
    try postScroll(delta: delta, to: target)
    Thread.sleep(forTimeInterval: 0.18)

    let refreshed = try resolveThread()
    let anchorsAfter = semanticAnchors(in: refreshed)
    let visibleAfter = visibleAnchorValues(
        anchors: anchorsAfter,
        thread: refreshed
    )
    if let focusedBefore {
        AXUIElementSetAttributeValue(
            focusedBefore,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
        )
    }
    let frontmostAfter = NSWorkspace.shared.frontmostApplication?
        .bundleIdentifier ?? ""
    let status = visibleBefore == visibleAfter ? "stalled" : "observed"
    let beforeFirst = String((visibleBefore.first ?? "").prefix(60))
    let afterFirst = String((visibleAfter.first ?? "").prefix(60))
    print(
        "process_scroll status=\(status) delta=\(delta) " +
        "before_count=\(anchorsBefore.count) after_count=\(anchorsAfter.count) " +
        "before_first=\(beforeFirst.debugDescription) " +
        "after_first=\(afterFirst.debugDescription) " +
        "frontmost_before=\(frontmostBefore) frontmost_after=\(frontmostAfter)"
    )
}

func dumpAccessibilityTree(
    _ element: AXUIElement,
    depth: Int = 0,
    remainingDepth: Int = 18
) {
    guard remainingDepth >= 0 else {
        return
    }

    let role: String = copyAttribute(element, kAXRoleAttribute as CFString) ?? ""
    let title: String = copyAttribute(element, kAXTitleAttribute as CFString) ?? ""
    let description: String = copyAttribute(
        element,
        kAXDescriptionAttribute as CFString
    ) ?? ""
    let value: String = copyAttribute(element, kAXValueAttribute as CFString) ?? ""
    let classes: [String] = copyAttribute(element, domClassListAttribute) ?? []
    let origin = copyPoint(element, kAXPositionAttribute as CFString)
    let size = copySize(element, kAXSizeAttribute as CFString)

    var actions: CFArray?
    AXUIElementCopyActionNames(element, &actions)

    let frame = origin.flatMap { origin in
        size.map { size in
            " x=\(Int(origin.x)) y=\(Int(origin.y)) w=\(Int(size.width)) h=\(Int(size.height))"
        }
    } ?? ""
    let indent = String(repeating: "  ", count: depth)
    print(
        "\(indent)role=\(role.debugDescription)" +
        " title=\(title.prefix(100).debugDescription)" +
        " description=\(description.prefix(100).debugDescription)" +
        " value=\(value.prefix(100).debugDescription)" +
        " classes=\(classes) actions=\(actions ?? [] as CFArray)\(frame)"
    )

    let children: [AXUIElement] = copyAttribute(
        element,
        kAXChildrenAttribute as CFString
    ) ?? []
    for child in children {
        dumpAccessibilityTree(
            child,
            depth: depth + 1,
            remainingDepth: remainingDepth - 1
        )
    }
}

func inspectAccessibilityTree() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard let window = findTargetWindow(in: applicationElement) else {
        throw ScrollBridgeError.windowNotFound
    }
    dumpAccessibilityTree(window)
}

func inspectWindows() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    let windows: [AXUIElement] = copyAttribute(
        applicationElement,
        kAXWindowsAttribute as CFString
    ) ?? []
    print("windows=\(windows.count)")
    for (index, window) in windows.enumerated() {
        let origin = copyPoint(window, kAXPositionAttribute as CFString) ?? .zero
        let size = copySize(window, kAXSizeAttribute as CFString) ?? .zero
        let title: String = copyAttribute(window, kAXTitleAttribute as CFString) ?? ""
        print(
            "window=\(index) x=\(Int(origin.x)) y=\(Int(origin.y)) " +
            "w=\(Int(size.width)) h=\(Int(size.height)) title=\(title.debugDescription)"
        )
        for text in findElements(withRole: kAXStaticTextRole as String, in: window) {
            let textOrigin = copyPoint(text, kAXPositionAttribute as CFString) ?? .zero
            let textSize = copySize(text, kAXSizeAttribute as CFString) ?? .zero
            guard textSize.height > 2, textOrigin.y <= origin.y + 180 else {
                continue
            }
            let value: String = copyAttribute(
                text,
                kAXValueAttribute as CFString
            ) ?? ""
            guard !value.isEmpty else {
                continue
            }
            print(
                "  header x=\(Int(textOrigin.x)) y=\(Int(textOrigin.y)) " +
                "w=\(Int(textSize.width)) h=\(Int(textSize.height)) " +
                "value=\(value.prefix(100).debugDescription)"
            )
        }
    }
}

func inspectThreadTree() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard
        let window = findTargetWindow(in: applicationElement),
        let thread = findThreadScrollContainer(in: window)
    else {
        throw ScrollBridgeError.windowNotFound
    }
    dumpAccessibilityTree(thread, remainingDepth: 24)
}

func inspectTextAnchors() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard
        let window = findTargetWindow(in: applicationElement),
        let thread = findThreadScrollContainer(in: window)
    else {
        throw ScrollBridgeError.windowNotFound
    }

    let anchors = textAnchors(in: thread)
    print("anchors=\(anchors.count)")
    for (index, anchor) in anchors.enumerated() {
        let value: String = copyAttribute(anchor, kAXValueAttribute as CFString) ?? ""
        let origin = copyPoint(anchor, kAXPositionAttribute as CFString) ?? .zero
        let size = copySize(anchor, kAXSizeAttribute as CFString) ?? .zero
        let summary = value
            .replacingOccurrences(of: "\n", with: " ")
            .prefix(120)
        print(
            "\(index) x=\(Int(origin.x)) y=\(Int(origin.y)) " +
            "w=\(Int(size.width)) h=\(Int(size.height)) \(summary.debugDescription)"
        )
    }
}

func inspectEffortMenu() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)
    guard let window = findTargetWindow(in: applicationElement) else {
        throw ScrollBridgeError.windowNotFound
    }

    guard let picker = findEffortPicker(in: window) else {
        throw ScrollBridgeError.windowNotFound
    }

    let frontmostBefore = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    let openStatus = AXUIElementPerformAction(picker, kAXPressAction as CFString)
    guard openStatus == .success else {
        throw ScrollBridgeError.semanticScrollFailed(openStatus)
    }
    Thread.sleep(forTimeInterval: 0.12)

    var openedPower: AXUIElement?
    if let power = findPowerMenuItems(in: applicationElement).first {
        openedPower = power
        print("effort_menu_power_tree")
        dumpAccessibilityTree(power, remainingDepth: 10)
        let submenuStatus = AXUIElementPerformAction(
            power,
            kAXPressAction as CFString
        )
        print("effort_submenu_open=\(submenuStatus.rawValue)")
        Thread.sleep(forTimeInterval: 0.12)
    }

    let roles = [
        kAXMenuItemRole as String,
        kAXSliderRole as String,
        kAXButtonRole as String,
        kAXRadioButtonRole as String,
        kAXStaticTextRole as String,
    ]
    for role in roles {
        for control in findElements(withRole: role, in: applicationElement) {
            guard
                let origin = copyPoint(control, kAXPositionAttribute as CFString),
                origin.x >= 700,
                origin.y >= 900
            else {
                continue
            }
            let title: String = copyAttribute(
                control,
                kAXTitleAttribute as CFString
            ) ?? ""
            let value: String = copyAttribute(
                control,
                kAXValueAttribute as CFString
            ) ?? ""
            let description: String = copyAttribute(
                control,
                kAXDescriptionAttribute as CFString
            ) ?? ""
            guard !title.isEmpty || !value.isEmpty || !description.isEmpty else {
                continue
            }
            var actions: CFArray?
            AXUIElementCopyActionNames(control, &actions)
            var focusSettable = DarwinBoolean(false)
            AXUIElementIsAttributeSettable(
                control,
                kAXFocusedAttribute as CFString,
                &focusSettable
            )
            print(
                "effort_menu role=\(role) x=\(Int(origin.x)) y=\(Int(origin.y)) " +
                "title=\(title.debugDescription) value=\(value.debugDescription) " +
                "description=\(description.debugDescription) " +
                "focus_settable=\(focusSettable.boolValue) " +
                "actions=\(actions ?? [] as CFArray)"
            )
        }
    }

    if let openedPower {
        AXUIElementPerformAction(openedPower, cancelAction)
    }
    let closeStatus = AXUIElementPerformAction(picker, kAXPressAction as CFString)
    Thread.sleep(forTimeInterval: 0.08)
    let frontmostAfter = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    print(
        "effort_menu open=\(openStatus.rawValue) close=\(closeStatus.rawValue) " +
        "frontmost_before=\(frontmostBefore) frontmost_after=\(frontmostAfter)"
    )
}

func probeEffortRoundTrip() throws {
    guard let application = NSRunningApplication
        .runningApplications(withBundleIdentifier: defaultBundleIdentifier)
        .first else {
        throw ScrollBridgeError.applicationNotRunning(defaultBundleIdentifier)
    }
    let applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    enableEnhancedUserInterface(applicationElement)

    func pickerAndTitle() throws -> (AXUIElement, String) {
        guard
            let window = findTargetWindow(in: applicationElement),
            let picker = findEffortPicker(in: window)
        else {
            throw ScrollBridgeError.windowNotFound
        }
        let title: String = copyAttribute(
            picker,
            kAXTitleAttribute as CFString
        ) ?? ""
        return (picker, title)
    }

    func openMenu(_ picker: AXUIElement) throws -> AXUIElement {
        let status = AXUIElementPerformAction(picker, kAXPressAction as CFString)
        guard status == .success else {
            throw ScrollBridgeError.semanticScrollFailed(status)
        }
        Thread.sleep(forTimeInterval: 0.12)
        guard let power = findPowerMenuItems(in: applicationElement).first else {
            throw ScrollBridgeError.windowNotFound
        }
        return power
    }

    func closeMenu(_ power: AXUIElement) {
        AXUIElementPerformAction(power, cancelAction)
        Thread.sleep(forTimeInterval: 0.08)
    }

    let frontmostBefore = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    let focusedBefore: AXUIElement? = copyAttribute(
        applicationElement,
        kAXFocusedUIElementAttribute as CFString
    )
    let (originalPicker, originalTitle) = try pickerAndTitle()
    let firstPower = try openMenu(originalPicker)
    try focusElement(firstPower)
    postArrowKey(to: application.processIdentifier, keyCode: 123)
    Thread.sleep(forTimeInterval: 0.15)
    closeMenu(firstPower)
    let (changedPicker, changedTitle) = try pickerAndTitle()

    var restoredTitle = changedTitle
    if changedTitle != originalTitle {
        let secondPower = try openMenu(changedPicker)
        try focusElement(secondPower)
        postArrowKey(to: application.processIdentifier, keyCode: 124)
        Thread.sleep(forTimeInterval: 0.15)
        closeMenu(secondPower)
        restoredTitle = try pickerAndTitle().1
    }

    if let focusedBefore {
        AXUIElementSetAttributeValue(
            focusedBefore,
            kAXFocusedAttribute as CFString,
            kCFBooleanTrue
        )
    }

    let frontmostAfter = NSWorkspace.shared.frontmostApplication?.bundleIdentifier ?? ""
    print(
        "effort_roundtrip original=\(originalTitle.debugDescription) " +
        "changed=\(changedTitle.debugDescription) " +
        "restored=\(restoredTitle.debugDescription) " +
        "frontmost_before=\(frontmostBefore) frontmost_after=\(frontmostAfter)"
    )
}

do {
    let arguments = Array(CommandLine.arguments.dropFirst())
    if arguments == ["--inspect-scroll-target"] {
        try inspectScrollTarget()
    } else if arguments == ["--probe-page-up"] {
        try probePageScroll(keyCode: 116)
    } else if arguments == ["--probe-page-down"] {
        try probePageScroll(keyCode: 121)
    } else if arguments.count == 2,
              arguments.first == "--probe-process-scroll",
              let delta = Int32(arguments[1]) {
        try probeProcessScroll(delta: delta)
    } else if arguments == ["--inspect-tree"] {
        try inspectAccessibilityTree()
    } else if arguments == ["--inspect-windows"] {
        try inspectWindows()
    } else if arguments == ["--inspect-thread"] {
        try inspectThreadTree()
    } else if arguments == ["--inspect-anchors"] {
        try inspectTextAnchors()
    } else if arguments == ["--inspect-effort-menu"] {
        try inspectEffortMenu()
    } else if arguments == ["--probe-effort-roundtrip"] {
        try probeEffortRoundTrip()
    } else if arguments == ["--inspect-composer"] {
        try inspectComposer()
    } else if arguments == ["--window-id"] {
        let target = try resolveTarget(bundleIdentifier: defaultBundleIdentifier)
        guard let windowIdentifier = findWindowIdentifier(pid: target.pid) else {
            throw ScrollBridgeError.windowNotFound
        }
        print(windowIdentifier)
    } else if arguments == ["--check"] {
        let target = try resolveTarget(bundleIdentifier: defaultBundleIdentifier)
        print(
            "event_access=\(CGPreflightPostEventAccess()) " +
            "pid=\(target.pid) x=\(Int(target.point.x)) y=\(Int(target.point.y))"
        )
    } else if arguments.first == "--type" {
        try runType(arguments: Array(arguments.dropFirst()))
    } else if arguments.count == 2,
              arguments.first == "--semantic-step",
              let step = Int(arguments[1]) {
        printSemanticReceipt(try semanticScroll(step: step))
    } else if arguments.count == 2,
              arguments.first == "--semantic-fraction",
              let fraction = Double(arguments[1]) {
        printSemanticReceipt(try semanticScroll(fraction: fraction))
    } else if arguments.count == 2,
              arguments.first == "--set-effort" {
        printEffortReceipt(try setCodexEffort(arguments[1]))
    } else if arguments.first == "--unsafe-once" {
        guard CGPreflightPostEventAccess() else {
            throw ScrollBridgeError.eventAccessDenied
        }
        try runOnce(arguments: Array(arguments.dropFirst()))
    } else if arguments.first == "--unsafe-once-global" {
        guard CGPreflightPostEventAccess() else {
            throw ScrollBridgeError.eventAccessDenied
        }
        try runOnce(arguments: Array(arguments.dropFirst()), globally: true)
    } else if arguments == ["--unsafe-hid-stream"] {
        guard CGPreflightPostEventAccess() else {
            throw ScrollBridgeError.eventAccessDenied
        }
        try runUnsafeHIDStream()
    } else {
        try runSemanticStream()
    }
} catch {
    fputs("codex-scroll: \(error)\n", stderr)
    exit(1)
}
