// ============================================================================
// agent.cpp - DeviceHub エージェント
//
// Windows PC の情報を収集して、FastAPI バックエンドに HTTP で送信します。
//
// 動作の流れ:
//   1. Windows API で PC情報を収集
//   2. WinHTTP で HTTP POST / GET / PUT を送信
//   3. FastAPI 側で PostgreSQL に保存される
//   4. その後 30 秒ごとに heartbeat（生存通知）を送る。サーバーは ONLINE / OFFLINE を判定する
//
// コンパイル方法（UCRT64 環境）:
//   g++ -static agent.cpp -o agent.exe -lwinhttp
//
// WinHTTP とは:
//   Windows に標準搭載されている HTTP 通信用 API。
//   追加ライブラリ不要で、-lwinhttp を付けるだけで使えます。
//   Python の requests モジュールに相当します。
// ============================================================================

#include <iostream>
#include <string>
#include <windows.h>

// winhttp.h: WinHTTP API のヘッダ。
// WinHttpOpen, WinHttpSendRequest などが入っています。
#include <winhttp.h>

// コンパイル時に winhttp.lib をリンクするよう指示。
// g++ では -lwinhttp オプションでも同じことができます。
#pragma comment(lib, "winhttp.lib")


// ============================================================================
// 設定
// ============================================================================

// FastAPI バックエンドの URL。
// ローカルで動かす場合は 127.0.0.1、AWS にデプロイした場合はその IP。
const std::wstring API_HOST = L"127.0.0.1";
const int API_PORT = 8000;

// APIキー（FastAPI 側の API_KEY と同じ値）
// ※学習用の固定値。本番では環境変数や設定ファイルで管理します。
const std::string API_KEY = "my-secret-key";

// このエージェントが登録するユーザーの名前
const std::string USER_NAME = "agent";

// heartbeat（生存通知）を送る間隔（秒）。
// サーバーは、最後の heartbeat から 60 秒を超えると OFFLINE と判定する。
// 30 秒にしておけば、1 回送り損ねても ONLINE のままでいられる。
const int HEARTBEAT_INTERVAL_SECONDS = 30;


// ============================================================================
// PC情報をまとめて持つ構造体
// ============================================================================
struct SystemInfo {
    std::string hostname;
    std::string os;
    std::string cpu;
    std::string gpu;
    int memory_gb;
};


// ============================================================================
// PC情報収集（sysinfo.cpp と同じ処理を関数化）
// ============================================================================

std::string get_hostname() {
    char buffer[256];
    DWORD size = sizeof(buffer);
    if (GetComputerNameA(buffer, &size)) {
        return std::string(buffer);
    }
    return "unknown";
}

int get_memory_gb() {
    MEMORYSTATUSEX memInfo;
    memInfo.dwLength = sizeof(memInfo);
    if (GlobalMemoryStatusEx(&memInfo)) {
        return static_cast<int>(memInfo.ullTotalPhys / (1024 * 1024 * 1024));
    }
    return 0;
}

std::string get_cpu_name() {
    HKEY hKey;
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE,
        "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0",
        0, KEY_READ, &hKey) != ERROR_SUCCESS) {
        return "unknown";
    }
    char buffer[256];
    DWORD bufferSize = sizeof(buffer);
    DWORD type;
    LONG result = RegQueryValueExA(hKey, "ProcessorNameString", nullptr, &type,
                                   reinterpret_cast<LPBYTE>(buffer), &bufferSize);
    RegCloseKey(hKey);
    if (result != ERROR_SUCCESS) {
        return "unknown";
    }
    return std::string(buffer);
}

std::string get_gpu_name() {
    HKEY hKey;
    if (RegOpenKeyExA(HKEY_LOCAL_MACHINE,
        "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}",
        0, KEY_READ, &hKey) != ERROR_SUCCESS) {
        return "unknown";
    }

    char subKeyName[256];
    DWORD index = 0;
    DWORD subKeySize;

    while (true) {
        subKeySize = sizeof(subKeyName);
        if (RegEnumKeyExA(hKey, index, subKeyName, &subKeySize,
                          nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS) {
            break;
        }
        index++;

        if (subKeyName[0] >= '0' && subKeyName[0] <= '9') {
            HKEY hSubKey;
            char subKeyPath[512];
            snprintf(subKeyPath, sizeof(subKeyPath),
                "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\%s",
                subKeyName);

            if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, subKeyPath, 0, KEY_READ, &hSubKey) == ERROR_SUCCESS) {
                char buffer[256];
                DWORD bufferSize = sizeof(buffer);
                DWORD type;
                if (RegQueryValueExA(hSubKey, "DriverDesc", nullptr, &type,
                                     reinterpret_cast<LPBYTE>(buffer), &bufferSize) == ERROR_SUCCESS) {
                    RegCloseKey(hSubKey);
                    RegCloseKey(hKey);
                    return std::string(buffer);
                }
                RegCloseKey(hSubKey);
            }
        }
    }
    RegCloseKey(hKey);
    return "unknown";
}

std::string get_os_name() {
    typedef struct _OSVERSIONINFOEXW_LOCAL {
        DWORD dwOSVersionInfoSize;
        DWORD dwMajorVersion;
        DWORD dwMinorVersion;
        DWORD dwBuildNumber;
        DWORD dwPlatformId;
        WCHAR szCSDVersion[128];
        USHORT wServicePackMajor;
        USHORT wServicePackMinor;
        USHORT wSuiteMask;
        BYTE wProductType;
        BYTE wReserved;
    } OSVERSIONINFOEXW_LOCAL;

    typedef LONG(WINAPI* RtlGetVersionPtr)(OSVERSIONINFOEXW_LOCAL*);

    HMODULE hNtdll = GetModuleHandleA("ntdll.dll");
    if (hNtdll == nullptr) return "Windows";

    RtlGetVersionPtr RtlGetVersion =
        reinterpret_cast<RtlGetVersionPtr>(GetProcAddress(hNtdll, "RtlGetVersion"));
    if (RtlGetVersion == nullptr) return "Windows";

    OSVERSIONINFOEXW_LOCAL osInfo = {};
    osInfo.dwOSVersionInfoSize = sizeof(osInfo);
    RtlGetVersion(&osInfo);

    if (osInfo.dwMajorVersion == 10 && osInfo.dwBuildNumber >= 22000) {
        return "Windows 11";
    } else if (osInfo.dwMajorVersion == 10) {
        return "Windows 10";
    }
    return "Windows";
}


// ============================================================================
// 文字コード変換ヘルパー
// ============================================================================
// Windows API は「ワイド文字列（std::wstring, UTF-16）」を使う場面が多い。
// 一方、私たちのプログラムは「std::string（UTF-8）」で扱っている。
// その変換をする関数を用意します。

// std::string (UTF-8) → std::wstring (UTF-16)
std::wstring to_wstring(const std::string& s) {
    if (s.empty()) return L"";
    // 必要なバッファサイズを計算
    int size = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, nullptr, 0);
    std::wstring result(size - 1, 0);
    MultiByteToWideChar(CP_UTF8, 0, s.c_str(), -1, &result[0], size);
    return result;
}


// ============================================================================
// HTTP 通信（WinHTTP）
// ============================================================================
//
// WinHTTP の流れ（Python requests との対応）:
//
//   WinHttpOpen()        → session を作る    = requests.Session()
//   WinHttpConnect()     → サーバーに接続    = （requests は内部で自動）
//   WinHttpOpenRequest() → リクエストを準備  = 準備中
//   WinHttpSendRequest() → 送信             = requests.post()
//   WinHttpReceiveResponse() → レスポンス受信
//   WinHttpReadData()    → ボディを読む      = response.text
// ----------------------------------------------------------------------------

struct HttpResult {
    long status;        // HTTP ステータスコード（200, 404 など）。失敗時 -1
    std::string body;   // レスポンスボディ
};

HttpResult http_request(const std::string& method, const std::string& path,
                        const std::string& body, bool useAuth) {
    HttpResult result = {-1, ""};

    // ----------------------------------------------------------------------
    // Step 1: WinHTTP セッションを開始
    // ----------------------------------------------------------------------
    // Python: session = requests.Session()
    // "DeviceHubAgent/1.0" は User-Agent ヘッダーに使われる
    //
    // WINHTTP_ACCESS_TYPE_NO_PROXY:
    //   プロキシを経由せず直接接続する設定。
    //   WINHTTP_ACCESS_TYPE_DEFAULT_PROXY だと Windows のプロキシ設定を
    //   使うため、社内プロキシ等があると 127.0.0.1 への接続も失敗する。
    HINTERNET hSession = WinHttpOpen(
        L"DeviceHubAgent/1.0",
        WINHTTP_ACCESS_TYPE_NO_PROXY,
        WINHTTP_NO_PROXY_NAME,
        WINHTTP_NO_PROXY_BYPASS,
        0
    );
    if (hSession == nullptr) {
        std::cerr << "WinHttpOpen failed" << std::endl;
        return result;
    }

    // ----------------------------------------------------------------------
    // Step 2: サーバーに接続
    // ----------------------------------------------------------------------
    // ホスト名とポートを指定して接続を確立する
    HINTERNET hConnect = WinHttpConnect(
        hSession,
        API_HOST.c_str(),
        static_cast<INTERNET_PORT>(API_PORT),
        0
    );
    if (hConnect == nullptr) {
        std::cerr << "WinHttpConnect failed" << std::endl;
        WinHttpCloseHandle(hSession);
        return result;
    }

    // ----------------------------------------------------------------------
    // Step 3: リクエストを準備
    // ----------------------------------------------------------------------
    // メソッド（GET/POST/PUT）とパス（/devices など）を指定
    HINTERNET hRequest = WinHttpOpenRequest(
        hConnect,
        to_wstring(method).c_str(),   // "GET", "POST", "PUT"
        to_wstring(path).c_str(),     // "/devices" など
        nullptr,                      // HTTP/1.1（デフォルト）
        WINHTTP_NO_REFERER,
        WINHTTP_DEFAULT_ACCEPT_TYPES,
        0                             // 0=HTTP, WINHTTP_FLAG_SECURE=HTTPS
    );
    if (hRequest == nullptr) {
        std::cerr << "WinHttpOpenRequest failed" << std::endl;
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return result;
    }

    // ----------------------------------------------------------------------
    // Step 4: ヘッダーを設定して送信
    // ----------------------------------------------------------------------
    // Content-Type: application/json は JSON を送るために必須
    std::wstring headers = L"Content-Type: application/json\r\n";

    // 認証が必要な場合は Authorization ヘッダーを追加
    // FastAPI の create_device() でチェックされるヘッダー
    if (useAuth) {
        headers += L"Authorization: Bearer " + to_wstring(API_KEY) + L"\r\n";
    }

    BOOL sent = WinHttpSendRequest(
        hRequest,
        headers.c_str(),                  // 追加ヘッダー
        static_cast<DWORD>(headers.length()),
        body.empty() ? nullptr : const_cast<char*>(body.c_str()),  // ボディ
        static_cast<DWORD>(body.length()),
        static_cast<DWORD>(body.length()),
        0
    );

    if (!sent) {
        // GetLastError() で Windows API のエラーコードを取得できる
        std::cerr << "WinHttpSendRequest failed: " << GetLastError() << std::endl;
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return result;
    }

    if (!WinHttpReceiveResponse(hRequest, nullptr)) {
        std::cerr << "WinHttpReceiveResponse failed: " << GetLastError() << std::endl;
        WinHttpCloseHandle(hRequest);
        WinHttpCloseHandle(hConnect);
        WinHttpCloseHandle(hSession);
        return result;
    }

    // ----------------------------------------------------------------------
    // Step 5: ステータスコードを取得
    // ----------------------------------------------------------------------
    DWORD statusCode = 0;
    DWORD statusSize = sizeof(statusCode);
    WinHttpQueryHeaders(
        hRequest,
        WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER,
        WINHTTP_HEADER_NAME_BY_INDEX,
        &statusCode,
        &statusSize,
        WINHTTP_NO_HEADER_INDEX
    );
    result.status = statusCode;

    // ----------------------------------------------------------------------
    // Step 6: レスポンスボディを読む
    // ----------------------------------------------------------------------
    // WinHttpQueryDataAvailable で「読めるバイト数」を確認し、
    // WinHttpReadData で実際に読み込む。全部読み終わるまで繰り返す。
    DWORD available = 0;
    do {
        available = 0;
        WinHttpQueryDataAvailable(hRequest, &available);
        if (available == 0) break;

        char* buffer = new char[available + 1];
        ZeroMemory(buffer, available + 1);

        DWORD read = 0;
        WinHttpReadData(hRequest, buffer, available, &read);
        result.body.append(buffer, read);

        delete[] buffer;
    } while (available > 0);

    // ----------------------------------------------------------------------
    // Step 7: 後片付け（リソースリーク防止）
    // ----------------------------------------------------------------------
    // WinHTTP のハンドルは「使い終わったら必ず閉じる」ルール。
    // Python の with 文や finally: conn.close() と同じ考え方。
    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);

    return result;
}


// ============================================================================
// JSON の組み立て
// ============================================================================
// Python では json.dumps() で辞書→JSON文字列に変換していました。
// C++ には標準の JSON ライブラリがないので、
// 文字列を繋げて JSON を作る簡易的な方法を使います。

// 文字列に含まれる " を \" にエスケープ
std::string json_escape(const std::string& s) {
    std::string result;
    for (char c : s) {
        if (c == '"') result += "\\\"";
        else if (c == '\\') result += "\\\\";
        else result += c;
    }
    return result;
}

// SystemInfo から POST /devices 用の JSON を作る
std::string build_device_json(int user_id, const SystemInfo& info) {
    // Python で言う:
    //   json.dumps({"user_id": user_id, "hostname": info.hostname, ...})
    std::string json = "{";
    json += "\"user_id\":" + std::to_string(user_id) + ",";
    json += "\"hostname\":\"" + json_escape(info.hostname) + "\",";
    json += "\"os\":\"" + json_escape(info.os) + "\",";
    json += "\"cpu\":\"" + json_escape(info.cpu) + "\",";
    json += "\"gpu\":\"" + json_escape(info.gpu) + "\",";
    json += "\"memory_gb\":" + std::to_string(info.memory_gb);
    json += "}";
    return json;
}


// ============================================================================
// JSON の簡易パース
// ============================================================================

// GET /devices のレスポンスから「自分のhostnameのデバイスID」を探す
int find_device_id_by_hostname(const std::string& json, const std::string& hostname) {
    // "\"hostname\":\"YMANOHA\"" のパターンを探す
    std::string pattern = "\"hostname\":\"" + hostname + "\"";
    size_t pos = json.find(pattern);
    if (pos == std::string::npos) {
        return -1;
    }

    // hostname の前にある直近の "id": を探す
    // レスポンス例: [{"id":1,"user_id":1,"hostname":"YMANOHA",...}]
    size_t idPos = json.rfind("\"id\":", pos);
    if (idPos == std::string::npos) {
        return -1;
    }

    // "id": の直後の数字を読む
    size_t numStart = idPos + 5;
    size_t numEnd = json.find_first_not_of("0123456789", numStart);
    return std::stoi(json.substr(numStart, numEnd - numStart));
}


// JSON の中の、最初の "id": の数字を読む（POST のレスポンスは 1 件なので、最初の id が自分）
int parse_first_id(const std::string& json) {
    size_t idPos = json.find("\"id\":");
    if (idPos == std::string::npos) {
        return -1;
    }
    size_t numStart = idPos + 5;
    size_t numEnd = json.find_first_not_of("0123456789", numStart);
    if (numEnd == numStart) {
        return -1;
    }
    return std::stoi(json.substr(numStart, numEnd - numStart));
}

// JSON の中の "status":"..." の値を読む（ONLINE / OFFLINE の表示用）
std::string parse_status(const std::string& json) {
    const std::string key = "\"status\":\"";
    size_t pos = json.find(key);
    if (pos == std::string::npos) {
        return "?";
    }
    size_t start = pos + key.size();
    size_t end = json.find('"', start);
    return json.substr(start, end - start);
}


// ============================================================================
// heartbeat の送信ループ
// ============================================================================
// HEARTBEAT_INTERVAL_SECONDS ごとに POST /devices/{id}/heartbeat を送り続ける。
// Ctrl+C で止める。止めると、サーバー側では 60 秒後に OFFLINE になる。
// 送信に失敗しても（サーバーが落ちている等）止まらずに、次の回で送り直す。
void heartbeat_loop(int deviceId) {
    std::string path = "/devices/" + std::to_string(deviceId) + "/heartbeat";
    std::cout << "Sending heartbeat every " << HEARTBEAT_INTERVAL_SECONDS
              << " s (Ctrl+C to stop)" << std::endl;

    while (true) {
        HttpResult result = http_request("POST", path, "", true);   // 変更系なので認証が必要
        if (result.status == 201) {
            std::cout << "POST " << path << " -> 201  status=" << parse_status(result.body) << std::endl;
        } else {
            std::cerr << "POST " << path << " -> " << result.status << " (retry next time)" << std::endl;
        }
        Sleep(HEARTBEAT_INTERVAL_SECONDS * 1000);   // Windows API。ミリ秒で指定
    }
}


// ============================================================================
// main() - エージェントの処理
// ============================================================================
// 使い方:
//   agent.exe          登録（または更新）したあと、heartbeat を送り続ける
//   agent.exe --once   登録（または更新）だけして終わる（以前の動作）
int main(int argc, char** argv) {
    const bool once = (argc > 1 && std::string(argv[1]) == "--once");

    std::cout << "=== DeviceHub Agent ===" << std::endl;

    // ------------------------------------------------------------------
    // Step 1: PC情報を収集
    // ------------------------------------------------------------------
    SystemInfo info;
    info.hostname  = get_hostname();
    info.os        = get_os_name();
    info.cpu       = get_cpu_name();
    info.gpu       = get_gpu_name();
    info.memory_gb = get_memory_gb();

    std::cout << "hostname:  " << info.hostname  << std::endl;
    std::cout << "os:        " << info.os        << std::endl;
    std::cout << "cpu:       " << info.cpu       << std::endl;
    std::cout << "gpu:       " << info.gpu       << std::endl;
    std::cout << "memory_gb: " << info.memory_gb << std::endl;
    std::cout << std::endl;

    // ------------------------------------------------------------------
    // Step 2: 自分の hostname が登録済みか確認（GET /devices）
    // ------------------------------------------------------------------
    HttpResult result = http_request("GET", "/devices", "", false);

    if (result.status == -1) {
        std::cerr << "Failed to connect to " << "http://127.0.0.1:8000" << std::endl;
        std::cerr << "FastAPI is running?" << std::endl;
        return 1;
    }

    std::cout << "GET /devices -> " << result.status << std::endl;

    int existingDeviceId = find_device_id_by_hostname(result.body, info.hostname);
    int deviceId = existingDeviceId;   // heartbeat を送るデバイスの ID

    if (existingDeviceId > 0) {
        // --------------------------------------------------------------
        // 登録済み → PUT で情報を更新
        // --------------------------------------------------------------
        std::cout << "Device already registered (id=" << existingDeviceId << "). Updating..." << std::endl;

        std::string body = build_device_json(1, info);
        std::string putPath = "/devices/" + std::to_string(existingDeviceId);

        result = http_request("PUT", putPath, body, true);   // 変更系なので認証が必要
        std::cout << "PUT " << putPath << " -> " << result.status << std::endl;
        std::cout << "Response: " << result.body << std::endl;

    } else {
        // --------------------------------------------------------------
        // 未登録 → POST /users → POST /devices
        // --------------------------------------------------------------
        std::cout << "Device not registered. Registering..." << std::endl;

        // Step 2a: ユーザーを作成（POST /users）
        std::string userBody = "{\"name\":\"" + USER_NAME + "\"}";
        result = http_request("POST", "/users", userBody, true);   // 変更系なので認証が必要
        std::cout << "POST /users -> " << result.status << std::endl;
        std::cout << "Response: " << result.body << std::endl;

        if (result.status != 201) {
            std::cerr << "Failed to create user" << std::endl;
            return 1;
        }

        // レスポンスから user_id を取り出す
        // レスポンス例: {"id":1,"name":"agent","created_at":"..."}
        size_t idPos = result.body.find("\"id\":");
        if (idPos == std::string::npos) {
            std::cerr << "Failed to parse user_id" << std::endl;
            return 1;
        }
        size_t numStart = idPos + 5;
        size_t numEnd = result.body.find_first_not_of("0123456789", numStart);
        int user_id = std::stoi(result.body.substr(numStart, numEnd - numStart));

        // Step 2b: デバイスを登録（POST /devices、認証あり）
        std::string deviceBody = build_device_json(user_id, info);
        result = http_request("POST", "/devices", deviceBody, true);
        std::cout << "POST /devices -> " << result.status << std::endl;
        std::cout << "Response: " << result.body << std::endl;

        if (result.status != 201) {
            std::cerr << "Failed to register device" << std::endl;
            return 1;
        }
        deviceId = parse_first_id(result.body);
    }

    if (once) {
        std::cout << "=== Done ===" << std::endl;
        return 0;
    }

    if (deviceId <= 0) {
        std::cerr << "Failed to get device id" << std::endl;
        return 1;
    }

    // ------------------------------------------------------------------
    // Step 3: heartbeat を送り続ける（Ctrl+C で終了）
    // ------------------------------------------------------------------
    heartbeat_loop(deviceId);
    return 0;
}
