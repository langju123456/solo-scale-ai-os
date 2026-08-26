import AppKit
import Darwin
import Foundation
import SoloScaleDesktopCore
import SwiftUI
import UniformTypeIdentifiers
import WebKit

private struct DesktopSession {
    let rootURL: URL
    let cookie: HTTPCookie
}

private enum BackendState { case starting, ready(DesktopSession), failed(String) }
private enum StartupDestination {
    case home
    case workProjectConnected
    case workChatGPTSelected
    case aiSettings(String?)

    var path: String {
        switch self {
        case .home: "/"
        case .workProjectConnected, .workChatGPTSelected: "/work"
        case .aiSettings: "/settings/ai/openai"
        }
    }

    var notice: String? {
        switch self {
        case .home: nil
        case .workProjectConnected: "project-connected"
        case .workChatGPTSelected: "chatgpt-selected"
        case .aiSettings: nil
        }
    }

    var queryItems: [URLQueryItem] {
        guard case let .aiSettings(returnPath) = self,
              let returnPath,
              let components = URLComponents(string: returnPath)
        else { return [] }
        return components.queryItems ?? []
    }
}
private let repositoryPreferenceKey = "SoloScaleRepositoryRoot"
private let workspacePreferenceKey = "SoloScaleWorkspaceRoot"
private let localePreferenceKey = "SoloScaleUILocale"
private let releasesURL = URL(
    string: "https://github.com/langju123456/solo-scale-ai-os/releases/latest"
)!

private final class BootstrapRedirectBlocker: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

private final class BackendController: NSObject, ObservableObject {
    @Published private(set) var state: BackendState = .starting
    private var process: Process?
    private var readinessFile: URL?
    private var pollTimer: Timer?
    private var deadline: Date?
    private var expectedOrigin: URL?
    private var bootstrapSession: URLSession?
    private var pendingChatGPTExport: URL?
    private var nextDestination: StartupDestination = .home

    func start() {
        let destination = nextDestination
        nextDestination = .home
        stop()
        state = .starting
        do {
            let support = try applicationSupportDirectory()
            let readiness = support.appendingPathComponent("backend-ready-\(UUID().uuidString).json")
            let token = randomDesktopSecret()
            readinessFile = readiness
            let executable = try backendExecutable()
            let process = Process()
            process.executableURL = executable
            let credentialPipe = Pipe()
            process.standardInput = credentialPipe
            var arguments = [
                "--desktop-mode", "--host", "127.0.0.1", "--port", "0",
                "--data-root", dataDirectory(applicationSupport: support).path,
                "--readiness-file", readiness.path,
                "--resource-root", try backendResourceRoot().path,
            ]
            if let repositoryRoot = configuredRepositoryRoot() {
                arguments.append(contentsOf: ["--repository-root", repositoryRoot])
            }
            if let workspaceRoot = configuredWorkspaceRoot() {
                arguments.append(contentsOf: ["--workspace-root", workspaceRoot])
            }
            process.arguments = arguments
            var desktopEnvironment = [
                "SOLOSCALE_DESKTOP_SESSION_TOKEN": token,
                "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            ]
            if let selectedExport = pendingChatGPTExport {
                desktopEnvironment["SOLOSCALE_PENDING_CHATGPT_EXPORT"] = selectedExport.path
            }
            process.environment = ProcessInfo.processInfo.environment.merging(
                desktopEnvironment
            ) { _, new in new }
            process.terminationHandler = { [weak self] terminated in
                DispatchQueue.main.async {
                    guard self?.process === terminated else { return }
                    self?.stopPolling()
                    self?.state = .failed("The local service stopped (exit \(terminated.terminationStatus)).")
                }
            }
            try process.run()
            do {
                try credentialPipe.fileHandleForWriting.write(
                    desktopCredentialFrame(try DesktopOpenAIKeychain.read())
                )
                try credentialPipe.fileHandleForWriting.close()
            } catch {
                credentialPipe.fileHandleForWriting.closeFile()
                process.terminate()
                throw error
            }
            pendingChatGPTExport = nil
            self.process = process
            deadline = Date().addingTimeInterval(45)
            pollTimer = Timer.scheduledTimer(withTimeInterval: 0.2, repeats: true) { [weak self] _ in self?.readReadiness(token: token, destination: destination) }
            readReadiness(token: token, destination: destination)
        } catch { state = .failed("Could not start the local service: \(error.localizedDescription)") }
    }

    func restart(destination: StartupDestination = .home) {
        nextDestination = destination
        start()
    }
    func saveOpenAIKey(_ apiKey: String, returnPath: String?) throws {
        try DesktopOpenAIKeychain.save(apiKey)
        restart(destination: .aiSettings(whitelistedAISettingsReturnPath(returnPath)))
    }
    func deleteOpenAIKey(returnPath: String?) throws {
        try DesktopOpenAIKeychain.delete()
        restart(destination: .aiSettings(whitelistedAISettingsReturnPath(returnPath)))
    }
    func failWebSession(_ message: String) { failStart(message) }
    func chooseRepository() {
        let panel = NSOpenPanel()
        panel.title = "Choose a SoloScale source checkout"
        panel.prompt = "Choose"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let selected = panel.url else { return }
        guard isSupportedRepository(selected) else {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "This is not a supported SoloScale checkout"
            alert.informativeText = "Choose a Git checkout containing pyproject.toml and src/soloscale/knowledge_store.py."
            alert.runModal()
            return
        }
        UserDefaults.standard.set(selected.path, forKey: repositoryPreferenceKey)
        restart()
    }
    func forgetRepository() {
        UserDefaults.standard.removeObject(forKey: repositoryPreferenceKey)
        restart()
    }
    func chooseWorkRepository() {
        let panel = NSOpenPanel()
        panel.title = "Choose a local Git project"
        panel.prompt = "Choose"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        guard panel.runModal() == .OK, let selected = panel.url else { return }
        guard isRegularLocalGitRepository(selected) else {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "This is not a supported local Git project"
            alert.informativeText = "Choose a regular local folder containing a .git directory or worktree file."
            alert.runModal()
            return
        }
        UserDefaults.standard.set(selected.path, forKey: workspacePreferenceKey)
        restart(destination: .workProjectConnected)
    }
    func forgetWorkRepository() {
        UserDefaults.standard.removeObject(forKey: workspacePreferenceKey)
        restart()
    }
    func chooseChatGPTExport() {
        let panel = NSOpenPanel()
        panel.title = "Choose a ChatGPT data export"
        panel.prompt = "Choose"
        panel.canChooseDirectories = false
        panel.canChooseFiles = true
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [
            UTType.json,
            UTType.zip,
        ]
        guard panel.runModal() == .OK, let selected = panel.url else { return }
        guard isRegularChatGPTExport(selected) else {
            let alert = NSAlert()
            alert.alertStyle = .warning
            alert.messageText = "Choose a regular JSON or ZIP export"
            alert.informativeText = "SoloScale will read only the file you explicitly choose after you confirm the import in the app."
            alert.runModal()
            return
        }
        pendingChatGPTExport = selected
        restart(destination: .workChatGPTSelected)
    }
    func stop() {
        stopPolling()
        bootstrapSession?.invalidateAndCancel()
        bootstrapSession = nil
        let runningProcess = process
        process = nil
        if let runningProcess, runningProcess.isRunning {
            runningProcess.terminate()
            for _ in 0..<40 where runningProcess.isRunning {
                Thread.sleep(forTimeInterval: 0.05)
            }
            if runningProcess.isRunning {
                Darwin.kill(runningProcess.processIdentifier, SIGKILL)
            }
        }
        if let readinessFile { try? FileManager.default.removeItem(at: readinessFile) }
        readinessFile = nil
        expectedOrigin = nil
    }

    func isAllowed(_ url: URL) -> Bool {
        guard let expectedOrigin, let actual = URLComponents(url: url, resolvingAgainstBaseURL: false), let expected = URLComponents(url: expectedOrigin, resolvingAgainstBaseURL: false) else { return false }
        return actual.scheme == expected.scheme && actual.host == expected.host && actual.port == expected.port && actual.user == nil && actual.password == nil
    }

    private func applicationSupportDirectory() throws -> URL {
        let root = try FileManager.default.url(for: .applicationSupportDirectory, in: .userDomainMask, appropriateFor: nil, create: true).appendingPathComponent("SoloScale AI OS", isDirectory: true)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }

    private func dataDirectory(applicationSupport: URL) -> URL {
        return applicationSupport
    }

    private func backendExecutable() throws -> URL {
        if let override = ProcessInfo.processInfo.environment["SOLOSCALE_BACKEND_EXECUTABLE"], !override.isEmpty {
            let candidate = URL(fileURLWithPath: override)
            guard FileManager.default.isExecutableFile(atPath: candidate.path) else { throw CocoaError(.fileNoSuchFile) }
            return candidate
        }
        guard let candidate = Bundle.main.url(forResource: "SoloScaleBackend", withExtension: nil, subdirectory: "SoloScaleBackend"), FileManager.default.isExecutableFile(atPath: candidate.path) else {
            throw NSError(domain: "SoloScaleDesktop", code: 1, userInfo: [NSLocalizedDescriptionKey: "Bundled SoloScaleBackend sidecar is missing."])
        }
        return candidate
    }

    private func backendResourceRoot() throws -> URL {
        guard let resources = Bundle.main.resourceURL else {
            throw NSError(domain: "SoloScaleDesktop", code: 2, userInfo: [NSLocalizedDescriptionKey: "Application resources are unavailable."])
        }
        return resources
            .appendingPathComponent("SoloScaleBackend", isDirectory: true)
            .appendingPathComponent("_internal", isDirectory: true)
    }

    private func readReadiness(token: String, destination: StartupDestination) {
        guard let readinessFile else { return }
        guard let data = try? Data(contentsOf: readinessFile) else {
            if let deadline, Date() > deadline { failStart("The local service did not become ready within 45 seconds.") }
            return
        }
        guard
            let launchedPID = process?.processIdentifier,
            let url = authenticatedReadinessURL(
                data: data,
                token: token,
                launchedPID: launchedPID
            )
        else {
            failStart("The local service wrote an invalid readiness receipt."); return
        }
        expectedOrigin = url
        stopPolling()
        beginBootstrap(origin: url, token: token, pid: launchedPID, destination: destination)
    }

    private func beginBootstrap(
        origin: URL,
        token: String,
        pid: Int32,
        destination: StartupDestination
    ) {
        let nonce = randomDesktopSecret()
        guard
            let bootstrapURL = URL(string: desktopBootstrapPath, relativeTo: origin)?.absoluteURL,
            let requestProof = desktopBootstrapRequestProof(
                token: token,
                origin: origin,
                pid: pid,
                nonce: nonce
            ),
            let expectedCookie = desktopSessionCookie(
                token: token,
                origin: origin,
                pid: pid,
                nonce: nonce
            )
        else { failStart("Could not prepare the secure local session."); return }

        var request = URLRequest(url: bootstrapURL)
        request.httpMethod = "POST"
        request.httpBody = Data()
        request.setValue("0", forHTTPHeaderField: "Content-Length")
        request.setValue(nonce, forHTTPHeaderField: desktopNonceHeader)
        request.setValue(requestProof, forHTTPHeaderField: desktopProofHeader)

        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.timeoutIntervalForRequest = 10
        configuration.timeoutIntervalForResource = 10
        let session = URLSession(
            configuration: configuration,
            delegate: BootstrapRedirectBlocker(),
            delegateQueue: nil
        )
        bootstrapSession = session
        session.dataTask(with: request) { [weak self] data, response, error in
            DispatchQueue.main.async {
                guard let self else { return }
                defer {
                    self.bootstrapSession?.finishTasksAndInvalidate()
                    self.bootstrapSession = nil
                }
                guard
                    error == nil,
                    data?.isEmpty == true,
                    self.process?.isRunning == true,
                    self.process?.processIdentifier == pid,
                    let http = response as? HTTPURLResponse,
                    http.url == bootstrapURL,
                    let responseNonce = http.value(forHTTPHeaderField: desktopNonceHeader),
                    let responseProof = http.value(forHTTPHeaderField: desktopProofHeader),
                    let setCookie = http.value(forHTTPHeaderField: "Set-Cookie")
                else { self.failStart("The secure local session could not be established."); return }

                let cookies = HTTPCookie.cookies(
                    withResponseHeaderFields: ["Set-Cookie": setCookie],
                    for: bootstrapURL
                )
                guard
                    cookies.count == 1,
                    let cookie = cookies.first,
                    cookie.name == desktopCookieName,
                    cookie.value == expectedCookie,
                    cookie.path == "/",
                    cookie.isHTTPOnly,
                    isValidDesktopBootstrapResponse(
                        token: token,
                        origin: origin,
                        pid: pid,
                        nonce: nonce,
                        status: http.statusCode,
                        responseNonce: responseNonce,
                        responseProof: responseProof,
                        cookie: cookie.value
                    )
                else { self.failStart("The local service returned an invalid session proof."); return }

                var components = URLComponents(url: origin, resolvingAgainstBaseURL: false)!
                components.path = destination.path
                let storedLocale = UserDefaults.standard.string(forKey: localePreferenceKey)
                let locale = storedLocale == "en" ? "en" : "zh-CN"
                var queryItems = [URLQueryItem(name: "lang", value: locale)]
                queryItems.append(contentsOf: destination.queryItems)
                if let notice = destination.notice {
                    queryItems.append(URLQueryItem(name: "notice", value: notice))
                }
                components.queryItems = queryItems
                guard let rootURL = components.url else {
                    self.failStart("The local application address is invalid."); return
                }
                self.state = .ready(DesktopSession(rootURL: rootURL, cookie: cookie))
            }
        }.resume()
    }

    private func configuredRepositoryRoot() -> String? {
        if let environmentRoot = ProcessInfo.processInfo.environment["SOLOSCALE_REPOSITORY_ROOT"],
           !environmentRoot.isEmpty {
            return environmentRoot
        }
        guard let stored = UserDefaults.standard.string(forKey: repositoryPreferenceKey),
              isSupportedRepository(URL(fileURLWithPath: stored)) else { return nil }
        return stored
    }
    private func configuredWorkspaceRoot() -> String? {
        if let environmentRoot = ProcessInfo.processInfo.environment["SOLOSCALE_WORKSPACE_ROOT"],
           !environmentRoot.isEmpty,
           isRegularLocalGitRepository(URL(fileURLWithPath: environmentRoot)) {
            return environmentRoot
        }
        guard let stored = UserDefaults.standard.string(forKey: workspacePreferenceKey),
              isRegularLocalGitRepository(URL(fileURLWithPath: stored)) else { return nil }
        return stored
    }
    private func isSupportedRepository(_ root: URL) -> Bool {
        let manager = FileManager.default
        return manager.fileExists(atPath: root.appendingPathComponent(".git").path)
            && manager.fileExists(atPath: root.appendingPathComponent("pyproject.toml").path)
            && manager.fileExists(
                atPath: root
                    .appendingPathComponent("src")
                    .appendingPathComponent("soloscale")
                    .appendingPathComponent("knowledge_store.py").path
            )
    }
    private func isRegularLocalGitRepository(_ root: URL) -> Bool {
        let keys: Set<URLResourceKey> = [.isDirectoryKey, .isSymbolicLinkKey]
        guard let values = try? root.resourceValues(forKeys: keys),
              values.isDirectory == true,
              values.isSymbolicLink != true else { return false }
        let gitEntry = root.appendingPathComponent(".git")
        guard FileManager.default.fileExists(atPath: gitEntry.path),
              let gitValues = try? gitEntry.resourceValues(
                  forKeys: [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey]
              ),
              gitValues.isSymbolicLink != true else { return false }
        return gitValues.isDirectory == true || gitValues.isRegularFile == true
    }
    private func isRegularChatGPTExport(_ file: URL) -> Bool {
        let allowed = ["json", "zip"]
        guard allowed.contains(file.pathExtension.lowercased()),
              let values = try? file.resourceValues(
                  forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
              ) else { return false }
        return values.isRegularFile == true && values.isSymbolicLink != true
    }
    private func failStart(_ message: String) {
        stop()
        state = .failed(message)
    }
    private func whitelistedAISettingsReturnPath(_ returnPath: String?) -> String? {
        guard let returnPath,
              let components = URLComponents(string: returnPath),
              components.scheme == nil,
              components.host == nil,
              components.user == nil,
              components.password == nil,
              components.path == "/settings/ai/openai"
        else { return nil }
        return returnPath
    }
    private func stopPolling() { pollTimer?.invalidate(); pollTimer = nil; deadline = nil }
}

private struct ContentView: View {
    @ObservedObject var backend: BackendController
    var body: some View {
        switch backend.state {
        case .ready(let session): LocalWebView(session: session, backend: backend)
        case .starting: ProgressView("Starting SoloScale locally…").frame(minWidth: 480, minHeight: 320)
        case .failed(let message): VStack(spacing: 14) {
            Text("SoloScale could not start").font(.headline)
            Text(message).multilineTextAlignment(.center).foregroundStyle(.secondary)
            Button("Restart local service") { backend.restart() }
        }.padding(32).frame(minWidth: 480, minHeight: 320)
        }
    }
}

private struct LocalWebView: NSViewRepresentable {
    let session: DesktopSession; let backend: BackendController
    func makeCoordinator() -> Coordinator { Coordinator(backend: backend) }
    func makeNSView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        configuration.userContentController.add(
            context.coordinator,
            name: "soloscaleCredentials"
        )
        let view = WKWebView(frame: .zero, configuration: configuration)
        context.coordinator.webView = view
        view.navigationDelegate = context.coordinator
        view.uiDelegate = context.coordinator
        configuration.websiteDataStore.httpCookieStore.setCookie(session.cookie) {
            configuration.websiteDataStore.httpCookieStore.getAllCookies { cookies in
                DispatchQueue.main.async {
                    let installed = cookies.contains {
                        $0.name == session.cookie.name
                            && $0.value == session.cookie.value
                            && $0.isHTTPOnly
                    }
                    if installed {
                        view.load(URLRequest(url: session.rootURL))
                    } else {
                        backend.failWebSession("The secure local session cookie could not be installed.")
                    }
                }
            }
        }
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}
    final class Coordinator: NSObject, WKDownloadDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
        let backend: BackendController
        weak var webView: WKWebView?
        init(backend: BackendController) { self.backend = backend }
        func userContentController(
            _ userContentController: WKUserContentController,
            didReceive message: WKScriptMessage
        ) {
            guard
                message.name == "soloscaleCredentials",
                message.frameInfo.isMainFrame,
                let webView,
                let url = webView.url,
                backend.isAllowed(url),
                let body = message.body as? [String: Any],
                let action = body["action"] as? String
            else { return }
            let returnPath = body["returnPath"] as? String
            do {
                switch action {
                case "saveOpenAIKey":
                    guard let apiKey = body["apiKey"] as? String else { return }
                    try backend.saveOpenAIKey(apiKey, returnPath: returnPath)
                case "deleteOpenAIKey":
                    try backend.deleteOpenAIKey(returnPath: returnPath)
                default:
                    return
                }
            } catch {
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "SoloScale could not update the API key"
                alert.informativeText = error.localizedDescription
                alert.runModal()
            }
        }
        private func openExternal(_ url: URL) {
            guard let scheme = url.scheme?.lowercased(), ["http", "https"].contains(scheme) else { return }
            NSWorkspace.shared.open(url)
        }
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let target = action.request.url else { decisionHandler(.cancel); return }
            if action.navigationType == .linkActivated,
               action.targetFrame?.isMainFrame == true,
               let components = URLComponents(url: target, resolvingAgainstBaseURL: false),
               components.scheme?.lowercased() == "soloscale",
               components.path.isEmpty,
               components.query == nil,
               components.fragment == nil,
               components.user == nil,
               components.password == nil {
                decisionHandler(.cancel)
                DispatchQueue.main.async { [weak self] in
                    switch components.host {
                    case "choose-source-checkout":
                        self?.backend.chooseRepository()
                    case "choose-work-repository":
                        self?.backend.chooseWorkRepository()
                    case "choose-chatgpt-export":
                        self?.backend.chooseChatGPTExport()
                    default:
                        break
                    }
                }
                return
            }
            if backend.isAllowed(target) {
                if action.shouldPerformDownload {
                    decisionHandler(.download)
                    return
                }
                if let components = URLComponents(url: target, resolvingAgainstBaseURL: false),
                   let locale = components.queryItems?.first(where: { $0.name == "lang" })?.value,
                   locale == "zh-CN" || locale == "en" {
                    UserDefaults.standard.set(locale, forKey: localePreferenceKey)
                }
                decisionHandler(.allow); return
            }
            if action.navigationType == .linkActivated { openExternal(target) }
            decisionHandler(.cancel)
        }
        func webView(
            _ webView: WKWebView,
            decidePolicyFor response: WKNavigationResponse,
            decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void
        ) {
            let disposition = (response.response as? HTTPURLResponse)?
                .value(forHTTPHeaderField: "Content-Disposition")?
                .lowercased()
            decisionHandler(disposition?.contains("attachment") == true ? .download : .allow)
        }
        func webView(
            _ webView: WKWebView,
            navigationAction: WKNavigationAction,
            didBecome download: WKDownload
        ) {
            download.delegate = self
        }
        func webView(
            _ webView: WKWebView,
            navigationResponse: WKNavigationResponse,
            didBecome download: WKDownload
        ) {
            download.delegate = self
        }
        func download(
            _ download: WKDownload,
            decideDestinationUsing response: URLResponse,
            suggestedFilename: String,
            completionHandler: @escaping (URL?) -> Void
        ) {
            guard let downloads = FileManager.default.urls(
                for: .downloadsDirectory,
                in: .userDomainMask
            ).first else {
                completionHandler(nil)
                return
            }
            let filename = URL(fileURLWithPath: suggestedFilename).lastPathComponent
            let requested = downloads.appendingPathComponent(
                filename.isEmpty ? "SoloScale-export" : filename,
                isDirectory: false
            )
            guard FileManager.default.fileExists(atPath: requested.path) else {
                completionHandler(requested)
                return
            }
            let stem = requested.deletingPathExtension().lastPathComponent
            let suffix = requested.pathExtension
            let uniqueName = suffix.isEmpty
                ? "\(stem)-\(UUID().uuidString)"
                : "\(stem)-\(UUID().uuidString).\(suffix)"
            completionHandler(downloads.appendingPathComponent(uniqueName))
        }
        func download(
            _ download: WKDownload,
            didFailWithError error: Error,
            resumeData: Data?
        ) {
            DispatchQueue.main.async {
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "SoloScale could not save the download"
                alert.informativeText = error.localizedDescription
                alert.runModal()
            }
        }
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let target = action.request.url, !backend.isAllowed(target) { openExternal(target) }
            return nil
        }
        func webView(
            _ webView: WKWebView,
            runOpenPanelWith parameters: WKOpenPanelParameters,
            initiatedByFrame frame: WKFrameInfo,
            completionHandler: @escaping ([URL]?) -> Void
        ) {
            let panel = NSOpenPanel()
            panel.title = "Choose a Resume or Job Document"
            panel.prompt = "Choose"
            panel.allowsMultipleSelection = parameters.allowsMultipleSelection
            panel.canChooseDirectories = parameters.allowsDirectories
            panel.canChooseFiles = !parameters.allowsDirectories
            if panel.canChooseFiles {
                panel.allowedContentTypes = [
                    UTType(filenameExtension: "docx"),
                    UTType.pdf,
                    UTType.plainText,
                    UTType(filenameExtension: "md"),
                ].compactMap { $0 }
            }
            completionHandler(panel.runModal() == .OK ? panel.urls : nil)
        }
    }
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    let backend = BackendController()
    func applicationDidFinishLaunching(_ notification: Notification) { backend.start() }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply { backend.stop(); return .terminateNow }
}

@main private struct SoloScaleDesktopApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    var body: some Scene {
        WindowGroup { ContentView(backend: appDelegate.backend) }
            .commands {
                CommandGroup(after: .appInfo) {
                    Button("Check for Updates…") {
                        NSWorkspace.shared.open(releasesURL)
                    }
                }
                CommandMenu("Project") {
                    Button("Choose SoloScale Source Checkout…") {
                        appDelegate.backend.chooseRepository()
                    }
                    Button("Forget Source Checkout") {
                        appDelegate.backend.forgetRepository()
                    }
                    Divider()
                    Button("Choose Work Project…") {
                        appDelegate.backend.chooseWorkRepository()
                    }
                    Button("Forget Work Project") {
                        appDelegate.backend.forgetWorkRepository()
                    }
                    Button("Choose ChatGPT Export…") {
                        appDelegate.backend.chooseChatGPTExport()
                    }
                }
            }
    }
}
