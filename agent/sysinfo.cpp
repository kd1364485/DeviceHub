// ============================================================================
// sysinfo.cpp - Windows PC の情報を取得する
//
// Windows API を使って、DeviceHub に送りたい情報を集めます。
//   - hostname: コンピュータ名
//   - os:       OSバージョン
//   - cpu:      CPU名
//   - gpu:      GPU名
//   - memory_gb: メモリ容量(GB)
//
// Windows API とは:
//   Windows が提供する「OSの機能を呼び出すための関数群」。
//   Python の os モジュールや platform モジュールに相当しますが、
//   C++ では <windows.h> を include して使います。
// ============================================================================

#include <iostream>
#include <string>

// windows.h: Windows API の大元となるヘッダファイル。
// GetComputerName, GlobalMemoryStatusEx などが入っています。
#include <windows.h>


// ----------------------------------------------------------------------------
// get_hostname()
//
// コンピュータ名（hostname）を取得します。
// Python だと socket.gethostname() や platform.node() に相当します。
// ----------------------------------------------------------------------------
std::string get_hostname() {
    // GetComputerNameA: Windows API の関数。
    // 引数: (書き込み先バッファ, バッファサイズを指すポインタ)
    //
    // 「バッファ」とは: 結果を入れてもらうための「入れ物」の配列。
    // C++ では文字列を入れる場所を自分で用意する必要があります。
    char buffer[256];
    DWORD size = sizeof(buffer);  // DWORD は Windows の unsigned long 型

    if (GetComputerNameA(buffer, &size)) {
        // 成功したら buffer に hostname が入っている
        return std::string(buffer);
    }
    return "unknown";
}


// ----------------------------------------------------------------------------
// get_memory_gb()
//
// 物理メモリ（RAM）の合計容量を GB で取得します。
// Python だと psutil.virtual_memory().total に相当します。
// ----------------------------------------------------------------------------
int get_memory_gb() {
    // MEMORYSTATUSEX: メモリ情報を入れる構造体。
    // 「構造体」は「関連するデータをまとめた型」。クラスの簡易版のようなもの。
    MEMORYSTATUSEX memInfo;
    memInfo.dwLength = sizeof(memInfo);  // 構造体のサイズを教える必要がある

    // GlobalMemoryStatusEx: メモリ情報を取得する Windows API。
    // 成功したら memInfo.ullTotalPhys に合計バイト数が入ります。
    if (GlobalMemoryStatusEx(&memInfo)) {
        // バイト数 → GB に変換（1024^3 で割る）
        // ullTotalPhys は unsigned long long（64bit整数）
        return static_cast<int>(memInfo.ullTotalPhys / (1024 * 1024 * 1024));
    }
    return 0;
}


// ----------------------------------------------------------------------------
// get_cpu_name()
//
// CPU名をレジストリから取得します。
//
// レジストリとは:
//   Windows の設定情報が入っているデータベース。
//   CPU名は以下の場所に入っています:
//   HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0
//   → "ProcessorNameString" という値に CPU名が入っている
//
// Python だと platform.processor() に相当しますが、
// C++ ではレジストリ API を直接呼びます。
// ----------------------------------------------------------------------------
std::string get_cpu_name() {
    HKEY hKey;  // レジストリキーのハンドル（「開いた窓口」のようなもの）

    // RegOpenKeyExA: レジストリキーを開く
    // HKEY_LOCAL_MACHINE は「コンピュータ全体の設定」が入っている場所
    LONG result = RegOpenKeyExA(
        HKEY_LOCAL_MACHINE,
        "HARDWARE\\DESCRIPTION\\System\\CentralProcessor\\0",
        0,
        KEY_READ,
        &hKey
    );

    if (result != ERROR_SUCCESS) {
        return "unknown";
    }

    // 開いたキーから "ProcessorNameString" という値を読む
    char buffer[256];
    DWORD bufferSize = sizeof(buffer);
    DWORD type;

    result = RegQueryValueExA(
        hKey,
        "ProcessorNameString",
        nullptr,
        &type,
        reinterpret_cast<LPBYTE>(buffer),
        &bufferSize
    );

    // 使い終わったら必ず閉じる（リソースリーク防止）
    RegCloseKey(hKey);

    if (result != ERROR_SUCCESS) {
        return "unknown";
    }

    return std::string(buffer);
}


// ----------------------------------------------------------------------------
// get_gpu_name()
//
// GPU名をレジストリから取得します。
//
// GPU情報はデバイスマネージャーと同じ場所にあります:
//   HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-...}
//   このクラスGUIDは「ディスプレイアダプタ（GPU）」のクラスです。
//   その下に 0000, 0001, ... というサブキーがあり、
//   それぞれに "DriverDesc" という値でGPU名が入っています。
//
// ※ GPUが複数ある場合、最初の1つだけ取ります（学習用の簡易実装）
// ----------------------------------------------------------------------------
std::string get_gpu_name() {
    HKEY hKey;

    // ディスプレイアダプタのクラスGUID配下を開く
    LONG result = RegOpenKeyExA(
        HKEY_LOCAL_MACHINE,
        "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}",
        0,
        KEY_READ,
        &hKey
    );

    if (result != ERROR_SUCCESS) {
        return "unknown";
    }

    // サブキー（0000, 0001, ...）を順番に調べる
    // GPU が複数ある場合は複数のサブキーがある
    char subKeyName[256];
    DWORD index = 0;
    DWORD subKeySize;

    while (true) {
        subKeySize = sizeof(subKeyName);

        // RegEnumKeyExA: サブキーを1つずつ列挙する
        result = RegEnumKeyExA(hKey, index, subKeyName, &subKeySize,
                               nullptr, nullptr, nullptr, nullptr);

        if (result != ERROR_SUCCESS) {
            break;  // もうサブキーがない
        }

        index++;

        // 0000, 0001 などの数字のサブキーだけ調べる
        // （Properties などのサブキーは除外）
        if (subKeyName[0] >= '0' && subKeyName[0] <= '9') {
            // サブキーを開く
            HKEY hSubKey;
            char subKeyPath[512];
            snprintf(subKeyPath, sizeof(subKeyPath),
                "SYSTEM\\CurrentControlSet\\Control\\Class\\{4d36e968-e325-11ce-bfc1-08002be10318}\\%s",
                subKeyName);

            if (RegOpenKeyExA(HKEY_LOCAL_MACHINE, subKeyPath, 0, KEY_READ, &hSubKey) == ERROR_SUCCESS) {
                // DriverDesc を読む（GPU名が入っている）
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


// ----------------------------------------------------------------------------
// get_os_name()
//
// OS名を取得します。
//
// Windows のバージョン番号の対応:
//   10.0 (build < 22000)  → Windows 10
//   10.0 (build >= 22000) → Windows 11
//
// RtlGetVersion を使います。
// GetVersionEx は「アプリがどのバージョンを対象に作られたか」で
// 結果が変わってしまう問題があるため、RtlGetVersion が推奨です。
// ----------------------------------------------------------------------------
std::string get_os_name() {
    // RtlGetVersion は ntdll.dll 内にある関数。
    // ヘッダ宣言がないので、自分で型を定義して呼び出します。

    // OSVERSIONINFOEXW 構造体（バージョン情報を入れる型）
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

    // RtlGetVersion の関数ポインタ型
    typedef LONG(WINAPI* RtlGetVersionPtr)(OSVERSIONINFOEXW_LOCAL*);

    // ntdll.dll を読み込んで RtlGetVersion のアドレスを取得
    HMODULE hNtdll = GetModuleHandleA("ntdll.dll");
    if (hNtdll == nullptr) {
        return "Windows";
    }

    RtlGetVersionPtr RtlGetVersion =
        reinterpret_cast<RtlGetVersionPtr>(GetProcAddress(hNtdll, "RtlGetVersion"));

    if (RtlGetVersion == nullptr) {
        return "Windows";
    }

    // バージョン情報を取得
    OSVERSIONINFOEXW_LOCAL osInfo = {};
    osInfo.dwOSVersionInfoSize = sizeof(osInfo);
    RtlGetVersion(&osInfo);

    // バージョン番号からOS名を決定
    // Windows 11 は build 22000 以降
    if (osInfo.dwMajorVersion == 10 && osInfo.dwBuildNumber >= 22000) {
        return "Windows 11";
    } else if (osInfo.dwMajorVersion == 10) {
        return "Windows 10";
    }

    return "Windows";
}


// ----------------------------------------------------------------------------
// main() - 全ての情報を取得して表示
// ----------------------------------------------------------------------------
int main() {
    // 各関数を呼んで情報を集める
    std::string hostname = get_hostname();
    std::string os       = get_os_name();
    std::string cpu      = get_cpu_name();
    std::string gpu      = get_gpu_name();
    int memory_gb        = get_memory_gb();

    // 結果を表示
    // FastAPI に送るデータのイメージ:
    //   {"hostname": "Ymanoha-PC", "os": "Windows 11", "cpu": "...",
    //    "gpu": "...", "memory_gb": 16}
    std::cout << "=== DeviceHub Agent ===" << std::endl;
    std::cout << "hostname:  " << hostname  << std::endl;
    std::cout << "os:        " << os        << std::endl;
    std::cout << "cpu:       " << cpu       << std::endl;
    std::cout << "gpu:       " << gpu       << std::endl;
    std::cout << "memory_gb: " << memory_gb << std::endl;

    return 0;
}
