// Special Judge 示例：用 testlib.h 检查 A + B 的答案
// 运行方式：checker <input> <output> <answer>
#include "testlib.h"

int main(int argc, char *argv[]) {
    registerTestlibCmd(argc, argv);

    long long a = inf.readLong();
    long long b = inf.readLong();
    long long expected = ans.readLong();
    long long got = ouf.readLong();

    if (got != expected) {
        quitf(_wa, "第 1 行应为 %s，实际为 %s",
              std::to_string(expected).c_str(), std::to_string(got).c_str());
    }
    if (!ouf.seekEof()) {
        quitf(_wa, "输出末尾存在多余内容");
    }
    quitf(_ok, "答案正确：%s + %s = %s",
          std::to_string(a).c_str(), std::to_string(b).c_str(), std::to_string(got).c_str());
}
