import Foundation
import Security

public enum DesktopAICredentialError: LocalizedError {
    case invalidKey
    case keychain(OSStatus)
    case frameTooLarge

    public var errorDescription: String? {
        switch self {
        case .invalidKey:
            return "The API key is not valid."
        case .keychain:
            return "The API key could not be stored securely in Keychain."
        case .frameTooLarge:
            return "The saved API key is too large for the local service."
        }
    }
}

public enum DesktopOpenAIKeychain {
    public static let service = "local.soloscale.desktop.ai.openai"
    public static let account = "default"

    public static func save(_ apiKey: String) throws {
        let data = try desktopAIKeyPayload(apiKey)
        var query = baseQuery
        query[kSecValueData as String] = data
        query[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        query[kSecAttrSynchronizable as String] = false
        let addStatus = SecItemAdd(query as CFDictionary, nil)
        if addStatus == errSecSuccess { return }
        guard addStatus == errSecDuplicateItem else {
            throw DesktopAICredentialError.keychain(addStatus)
        }
        let update: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecAttrSynchronizable as String: false,
        ]
        let updateStatus = SecItemUpdate(baseQuery as CFDictionary, update as CFDictionary)
        guard updateStatus == errSecSuccess else {
            throw DesktopAICredentialError.keychain(updateStatus)
        }
    }

    public static func delete() throws {
        let status = SecItemDelete(baseQuery as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw DesktopAICredentialError.keychain(status)
        }
    }

    public static func read() throws -> Data? {
        var query = baseQuery
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else {
            throw DesktopAICredentialError.keychain(status)
        }
        guard !data.isEmpty, data.count <= desktopCredentialFrameMaximum else {
            throw DesktopAICredentialError.frameTooLarge
        }
        return data
    }

    private static var baseQuery: [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: false,
        ]
    }
}

public let desktopCredentialFrameMaximum = 512

/// Validates the exact API-key bytes that will be sent to the Python sidecar.
/// Whitespace is rejected rather than trimmed so a saved key cannot differ from
/// the configured key the operator intended to use.
public func desktopAIKeyPayload(_ apiKey: String) throws -> Data {
    guard apiKey == apiKey.trimmingCharacters(in: .whitespacesAndNewlines),
          let data = apiKey.data(using: .utf8),
          !data.isEmpty,
          data.count <= desktopCredentialFrameMaximum
    else {
        throw DesktopAICredentialError.invalidKey
    }
    return data
}

/// Four-byte big-endian payload length followed by opaque credential bytes.
/// A zero length frame explicitly means that no credential is configured.
public func desktopCredentialFrame(_ payload: Data?) throws -> Data {
    let body = payload ?? Data()
    guard body.count <= desktopCredentialFrameMaximum else {
        throw DesktopAICredentialError.frameTooLarge
    }
    var length = UInt32(body.count).bigEndian
    var frame = Data(bytes: &length, count: MemoryLayout<UInt32>.size)
    frame.append(body)
    return frame
}
