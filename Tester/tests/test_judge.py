"""评测内核与识别的单元测试（可直接运行：python -m unittest -v）。"""

from __future__ import annotations

import os
import sys
import time
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("OITESTER_HOME", str(ROOT / ".oit-test"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import detect, judge  # noqa: E402
from app.paths import work_root  # noqa: E402
from app.session import Session  # noqa: E402
from app.store import Store  # noqa: E402

BASE_SETTINGS = {
    "compiler": "g++",
    "flags": "-O2 -std=c++17 -static",
    "stackMB": 256,
    "timeMs": 1000,
    "memoryMB": 256,
    "spaceMB": 64,
    "compareMode": "trim",
}

TMP = ROOT / ".oit-test" / "tests"
TMP.mkdir(parents=True, exist_ok=True)


def compile_cpp(name: str, code: str, settings=None) -> Path:
    source = TMP / f"{name}.cpp"
    source.write_text(code, encoding="utf-8")
    exe = TMP / f"{name}.exe"
    result = judge.compile_source(source, exe, settings or BASE_SETTINGS)
    if result["status"] != "ok":
        raise AssertionError(f"编译失败：{result['output']}")
    return exe


class CompareTests(unittest.TestCase):
    def test_trim_ignores_trailing_spaces(self):
        self.assertTrue(judge.compare_texts("1 2\n3\n", "1 2   \n3\t\n\n", "trim")["equal"])

    def test_trim_detects_value(self):
        result = judge.compare_texts("1\n2\n", "1\n3\n", "trim")
        self.assertFalse(result["equal"])
        self.assertEqual(result["line"], 2)

    def test_tokens_mode(self):
        self.assertTrue(judge.compare_texts("1 2\n3", "1\n2    3", "tokens")["equal"])

    def test_strict_mode(self):
        self.assertFalse(judge.compare_texts("1 2\n", "1 2 \n", "strict")["equal"])
        self.assertTrue(judge.compare_texts("1 2\n", "1 2\n", "strict")["equal"])

    def test_strict_mode_trailing_newline(self):
        result = judge.compare_texts("1 2\n", "1 2", "strict")
        self.assertFalse(result["equal"])
        self.assertIn("严格", result["reason"])

    def test_missing_lines(self):
        result = judge.compare_texts("1\n2\n", "1\n", "trim")
        self.assertFalse(result["equal"])
        self.assertIn("少了", result["reason"])


class JudgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ok_exe = compile_cpp("sum", r"""
#include <cstdio>
int main() { long long a, b; if (scanf("%lld%lld", &a, &b) != 2) return 0; printf("%lld\n", a + b); return 0; }
""")
        cls.wa_exe = compile_cpp("sum_wrong", r"""
#include <cstdio>
int main() { long long a, b; scanf("%lld%lld", &a, &b); printf("%lld\n", a + b + 1); return 0; }
""")
        cls.tle_exe = compile_cpp("loop", r"""
int main() { volatile long long x = 0; for (;;) x++; return 0; }
""")
        cls.mle_exe = compile_cpp("memory", r"""
#include <cstdio>
#include <vector>
int main() { std::vector<char> v(400LL << 20, 7); printf("%d\n", (int)v[100]); return 0; }
""")
        cls.re_exe = compile_cpp("crash", r"""
#include <cstdio>
int main() { int *p = nullptr; *p = 1; printf("no\n"); return 0; }
""")
        cls.ole_exe = compile_cpp("flood", r"""
#include <cstdio>
int main() { for (;;) printf("1234567890"); return 0; }
""")
        cls.freopen_exe = compile_cpp("freopen", r"""
#include <cstdio>
int main() { long long a, b; freopen("input.in", "r", stdin); scanf("%lld%lld", &a, &b); freopen("result.txt", "w", stdout); printf("%lld\n", a + b); return 0; }
""")

        cls.data = TMP / "data"
        cls.data.mkdir(exist_ok=True)
        (cls.data / "1.in").write_text("1 2\n", encoding="utf-8")
        (cls.data / "1.out").write_text("3\n", encoding="utf-8")

    def run_case(self, exe, settings=None, in_name="1.in", ans_name="1.out"):
        case = {
            "in": str(self.data / in_name),
            "ans": str(self.data / ans_name) if ans_name else None,
        }
        return judge.judge_case(
            [str(exe)], case, settings or BASE_SETTINGS,
            artifact_dir=TMP / "artifacts" / exe.stem,
            work_root=work_root(),
        )

    def test_ac(self):
        result = self.run_case(self.ok_exe)
        self.assertEqual(result["status"], "AC", result)
        self.assertGreater(result["memoryKB"], 0)
        self.assertLess(result["timeMs"], 500)

    def test_wa_has_diff(self):
        result = self.run_case(self.wa_exe)
        self.assertEqual(result["status"], "WA")
        self.assertEqual(result["compare"]["line"], 1)

    def test_tle(self):
        settings = dict(BASE_SETTINGS, timeMs=300)
        started = time.time()
        result = self.run_case(self.tle_exe, settings)
        self.assertEqual(result["status"], "TLE")
        self.assertLess(time.time() - started, 3.0)

    def test_mle(self):
        settings = dict(BASE_SETTINGS, memoryMB=64)
        result = self.run_case(self.mle_exe, settings)
        self.assertEqual(result["status"], "MLE", result)
        self.assertGreaterEqual(result["memoryKB"] // 1024, 40)

    def test_re(self):
        result = self.run_case(self.re_exe)
        self.assertEqual(result["status"], "RE")
        self.assertNotEqual(result["exitCode"], 0)

    def test_ole(self):
        settings = dict(BASE_SETTINGS, spaceMB=1, timeMs=3000)
        result = self.run_case(self.ole_exe, settings)
        self.assertEqual(result["status"], "OLE", result)

    def test_run_without_answer(self):
        result = self.run_case(self.ok_exe, ans_name=None)
        self.assertEqual(result["status"], "RUN")

    def test_freopen_output_detected(self):
        # 程序从 input.in 读入、把答案写进 result.txt；评测器应识别唯一产物
        case = {
            "in": str(self.data / "1.in"),
            "ans": str(self.data / "1.out"),
        }
        # 把输入文件重命名成 input.in 供 freopen 使用
        (self.data / "input.in").write_text("1 2\n", encoding="utf-8")
        result = judge.judge_case(
            [str(self.freopen_exe)], {"in": str(self.data / "input.in"),
                                      "ans": str(self.data / "1.out")},
            BASE_SETTINGS, artifact_dir=TMP / "artifacts" / "freopen", work_root=work_root(),
        )
        self.assertEqual(result["status"], "AC", result)
        self.assertIn("文件输出", result["message"])


class CheckerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.checker_src = ROOT / "examples" / "SPJP1001" / "checker.cpp"
        if not cls.checker_src.is_file():
            raise unittest.SkipTest("缺少 SPJ 示例")
        cls.checker_exe = TMP / "spj_checker.exe"
        result = judge.compile_source(cls.checker_src, cls.checker_exe, BASE_SETTINGS)
        if result["status"] != "ok":
            raise unittest.SkipTest(f"无法编译 testlib checker：{result['output'][:200]}")

        cls.data = TMP / "spjdata"
        cls.data.mkdir(exist_ok=True)
        (cls.data / "1.in").write_text("1 2\n", encoding="utf-8")
        (cls.data / "1.out").write_text("3\n", encoding="utf-8")

        cls.ok_exe = compile_cpp("spj_ok", r"""
#include <cstdio>
int main() { long long a, b; scanf("%lld%lld", &a, &b); printf("%lld\n", a + b); return 0; }
""")
        cls.bad_exe = compile_cpp("spj_bad", r"""
#include <cstdio>
int main() { long long a, b; scanf("%lld%lld", &a, &b); printf("%lld\n", a + b + 1); return 0; }
""")
        cls.garbage_exe = compile_cpp("spj_garbage", r"""
#include <cstdio>
int main() { printf("not-a-number\n"); return 0; }
""")

    def run_with_checker(self, exe, checker_exe=None, ans=True):
        return judge.judge_case(
            [str(exe)],
            {"in": str(self.data / "1.in"), "ans": str(self.data / "1.out") if ans else None},
            BASE_SETTINGS,
            artifact_dir=TMP / "artifacts" / ("spj_" + exe.stem),
            work_root=work_root(),
            checker={
                "exe": str(checker_exe or self.checker_exe),
                "timeMs": 5000,
                "memoryMB": 512,
            },
        )

    def test_accept(self):
        result = self.run_with_checker(self.ok_exe)
        self.assertEqual(result["status"], "AC", result)
        self.assertEqual(result["checker"]["verdict"], "ok")
        self.assertIn("答案正确", result["message"])

    def test_wrong_answer_uses_checker_message(self):
        result = self.run_with_checker(self.bad_exe)
        self.assertEqual(result["status"], "WA", result)
        self.assertEqual(result["checker"]["exitCode"], 1)
        self.assertIn("应为", result["message"])

    def test_presentation_error(self):
        result = self.run_with_checker(self.garbage_exe)
        self.assertEqual(result["status"], "PE", result)

    def test_partial_points(self):
        checker = compile_cpp("partial_checker", r"""
#include "testlib.h"
int main(int argc, char *argv[]) {
    registerTestlibCmd(argc, argv);
    int got = ouf.readInt();
    if (got == 3) quitf(_ok, "ok");
    quitp(0.5, "只对了一半");
}
""")
        result = self.run_with_checker(self.bad_exe, checker)
        self.assertEqual(result["status"], "PC", result)
        self.assertAlmostEqual(result["score"], 0.5, places=3)

    def test_full_points_counts_as_accept(self):
        checker = compile_cpp("full_checker", r"""
#include "testlib.h"
int main(int argc, char *argv[]) {
    registerTestlibCmd(argc, argv);
    ouf.readInt();
    quitp(1.0, "满分");
}
""")
        result = self.run_with_checker(self.ok_exe, checker)
        self.assertEqual(result["status"], "AC", result)

    def test_points_parser(self):
        self.assertAlmostEqual(judge._parse_points("0.6 half"), 0.6)
        self.assertEqual(judge._parse_points("100 分"), 100.0)
        self.assertIsNone(judge._parse_points("points_info=abc"))

    def test_no_answer_still_runs_checker(self):
        # 没有 .ans 时用空文件运行，checker 读到 EOF 会给出 PE/WA，而不是让评测器崩溃
        result = self.run_with_checker(self.ok_exe, ans=False)
        self.assertIn(result["status"], ("PE", "WA", "CJF"), result)
        self.assertTrue(result.get("checker"))


class SpjSessionTests(unittest.TestCase):
    def test_auto_detect_and_judge_with_checker(self):
        if not judge.find_testlib("g++"):
            self.skipTest("当前 MinGW 没有 testlib.h，跳过 Special Judge 集成测试")
        store = Store(TMP / f"store_spj_{uuid.uuid4().hex[:6]}.json")
        session = Session(store)
        try:
            state = session.open_folder(str(ROOT / "examples" / "SPJP1001"))
            self.assertEqual(state["source"]["name"], "P1001.cpp")
            self.assertTrue(state["checker"]["enabled"], state["checker"])
            self.assertEqual(state["checker"]["name"], "checker.cpp")

            session.start_judge(all_cases=True)
            deadline = time.time() + 40
            while time.time() < deadline:
                state = session.state()
                if not state["judging"]["running"]:
                    break
                time.sleep(0.1)
            statuses = [case["result"]["status"] if case["result"] else None for case in state["cases"]]
            self.assertEqual(statuses, ["AC"] * 5, statuses)
            self.assertTrue(all(case["result"]["checker"] for case in state["cases"]))

            # 关闭 Special Judge 后应回到普通答案比较
            session.set_checker(enabled=False)
            state = session.state()
            self.assertFalse(state["checker"]["enabled"])
        finally:
            session.shutdown()


class DetectTests(unittest.TestCase):
    def test_example_folder(self):
        folder = ROOT / "examples" / "P1001"
        scan = detect.scan_folder(folder)
        self.assertEqual(scan.name, "P1001")
        self.assertEqual(len(scan.cases), 5)
        self.assertTrue(all(c.ans_path is not None for c in scan.cases))
        self.assertIsNotNone(scan.source)
        self.assertEqual(scan.source.name, "P1001.cpp")

    def test_natural_order(self):
        folder = TMP / "natural"
        folder.mkdir(exist_ok=True)
        for index in (1, 2, 10):
            (folder / f"{index}.in").write_text(f"{index}\n", encoding="utf-8")
            (folder / f"{index}.ans").write_text(f"{index}\n", encoding="utf-8")
        scan = detect.scan_folder(folder)
        self.assertEqual([c.stem for c in scan.cases], ["1", "2", "10"])

    def test_import_pairing(self):
        folder = TMP / "imported"
        folder.mkdir(exist_ok=True)
        in_file = folder / "sample3.in"
        ans_file = folder / "sample3.out"
        in_file.write_text("1\n", encoding="utf-8")
        ans_file.write_text("1\n", encoding="utf-8")
        pairs = detect.pair_import_paths([str(ans_file)])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0].name, "sample3.in")


if __name__ == "__main__":
    unittest.main(verbosity=2)
