// OI 对拍器 —— 启动器
// 负责找到 Python，后台启动 main.py（无控制台窗口）。
// 编译：build.bat（g++ + windres）

#include <windows.h>
#include <shlwapi.h>
#include <string>
#include <vector>

#pragma comment(lib, "shlwapi.lib")

namespace {

std::wstring quoteArg(const std::wstring &value) {
    if (value.empty()) return L"\"\"";
    if (value.find_first_of(L" \t\n\v\"") == std::wstring::npos) return value;

    std::wstring out;
    out.push_back(L'"');
    size_t backslashes = 0;
    for (wchar_t ch : value) {
        if (ch == L'\\') {
            ++backslashes;
            out.push_back(ch);
        } else if (ch == L'"') {
            out.append(backslashes + 1, L'\\');
            out.push_back(L'"');
            backslashes = 0;
        } else {
            backslashes = 0;
            out.push_back(ch);
        }
    }
    out.append(backslashes, L'\\');
    out.push_back(L'"');
    return out;
}

std::wstring exeDir() {
    wchar_t buffer[MAX_PATH * 2] = {0};
    DWORD length = GetModuleFileNameW(nullptr, buffer, MAX_PATH * 2);
    std::wstring path(buffer, length);
    size_t pos = path.find_last_of(L"\\/");
    return pos == std::wstring::npos ? path : path.substr(0, pos);
}

bool isFile(const std::wstring &path) {
    DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

bool fromRegistry(std::wstring &result) {
    const HKEY roots[] = {HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
    const wchar_t *base = L"SOFTWARE\\Python\\PythonCore";
    for (HKEY root : roots) {
        HKEY core = nullptr;
        if (RegOpenKeyExW(root, base, 0, KEY_READ, &core) != ERROR_SUCCESS) continue;
        for (DWORD index = 0;; ++index) {
            wchar_t version[256] = {0};
            DWORD versionLength = 255;
            if (RegEnumKeyExW(core, index, version, &versionLength, nullptr, nullptr, nullptr, nullptr) != ERROR_SUCCESS) break;
            HKEY install = nullptr;
            std::wstring sub = std::wstring(base) + L"\\" + version + L"\\InstallPath";
            if (RegOpenKeyExW(root, sub.c_str(), 0, KEY_READ, &install) == ERROR_SUCCESS) {
                wchar_t value[MAX_PATH * 2] = {0};
                DWORD size = sizeof(value) - sizeof(wchar_t);
                DWORD type = 0;
                if (RegQueryValueExW(install, nullptr, nullptr, &type, reinterpret_cast<LPBYTE>(value), &size) == ERROR_SUCCESS) {
                    std::wstring candidate = std::wstring(value) + L"\\pythonw.exe";
                    if (isFile(candidate)) {
                        result = candidate;
                        RegCloseKey(install);
                        RegCloseKey(core);
                        return true;
                    }
                }
                RegCloseKey(install);
            }
        }
        RegCloseKey(core);
    }
    return false;
}

bool fromLocalAppData(std::wstring &result) {
    wchar_t local[MAX_PATH * 2] = {0};
    if (GetEnvironmentVariableW(L"LOCALAPPDATA", local, MAX_PATH * 2) == 0) return false;
    std::wstring pattern = std::wstring(local) + L"\\Programs\\Python*";
    WIN32_FIND_DATAW data{};
    HANDLE handle = FindFirstFileW(pattern.c_str(), &data);
    if (handle == INVALID_HANDLE_VALUE) return false;
    bool found = false;
    do {
        if (!(data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        std::wstring candidate = std::wstring(local) + L"\\Programs\\" + data.cFileName + L"\\pythonw.exe";
        if (isFile(candidate)) {
            result = candidate;
            found = true;
            break;
        }
    } while (FindNextFileW(handle, &data));
    FindClose(handle);
    return found;
}

std::wstring findPython(const std::wstring &dir) {
    // 1. 便携运行时
    std::wstring portable = dir + L"\\runtime\\pythonw.exe";
    if (isFile(portable)) return portable;

    // 2. PATH
    wchar_t found[MAX_PATH * 2] = {0};
    if (SearchPathW(nullptr, L"pythonw.exe", nullptr, MAX_PATH * 2, found, nullptr) > 0) {
        return std::wstring(found);
    }

    // 3. 注册表 / 常见安装位置
    std::wstring candidate;
    if (fromRegistry(candidate)) return candidate;
    if (fromLocalAppData(candidate)) return candidate;

    // 4. python.exe 兜底（仍以无窗口方式启动）
    if (!portable.empty()) {
        std::wstring python = dir + L"\\runtime\\python.exe";
        if (isFile(python)) return python;
    }
    if (SearchPathW(nullptr, L"python.exe", nullptr, MAX_PATH * 2, found, nullptr) > 0) {
        return std::wstring(found);
    }
    return L"";
}

void showError(const std::wstring &message) {
    MessageBoxW(nullptr, message.c_str(), L"OI 对拍器", MB_ICONERROR | MB_OK | MB_TASKMODAL);
}

}  // namespace

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    std::wstring dir = exeDir();
    std::wstring mainPy = dir + L"\\main.py";
    if (!isFile(mainPy)) {
        showError(L"启动失败：找不到 main.py。\n\n请确认 OI 对拍器的文件完整（Tester.exe 与 main.py 应在同一目录）。");
        return 1;
    }

    std::wstring python = findPython(dir);
    if (python.empty()) {
        showError(
            L"没有找到可用的 Python 3。\n\n"
            L"OI 对拍器需要 Python 3.9 或更高版本：\n"
            L"1. 安装 Python 时勾选 “Add Python to PATH”；\n"
            L"2. 或者把便携版 Python 放到 runtime\\ 目录下。\n\n"
            L"下载地址：https://www.python.org/downloads/");
        return 2;
    }

    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    std::wstring command = quoteArg(python) + L" " + quoteArg(mainPy);
    if (argv) {
        for (int i = 1; i < argc; ++i) {
            command += L" " + quoteArg(argv[i]);
        }
        LocalFree(argv);
    }

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION info{};

    std::vector<wchar_t> buffer(command.begin(), command.end());
    buffer.push_back(L'\0');

    BOOL ok = CreateProcessW(
        nullptr, buffer.data(), nullptr, nullptr, FALSE,
        CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
        nullptr, dir.c_str(), &startup, &info);

    if (!ok) {
        showError(L"无法启动 Python 进程（错误码 " + std::to_wstring(GetLastError()) + L"）。");
        return 3;
    }

    CloseHandle(info.hThread);
    CloseHandle(info.hProcess);
    return 0;
}
