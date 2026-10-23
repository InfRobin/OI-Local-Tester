"""题目文件夹自动识别：测试数据、选手程序、可执行文件。"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

IN_EXTS = {".in", ".inp", ".input"}
ANS_EXTS = {".out", ".ans", ".answer", ".expected", ".exp", ".ou"}
SOURCE_EXTS = {".cpp", ".cc", ".cxx", ".c", ".py"}

JUNK_DIRS = {
    ".git", ".svn", ".hg", ".vs", ".vscode", ".idea", "__pycache__",
    "node_modules", ".cache", ".tester", ".dsh",
}

# 明显不是选手程序的源码名
NON_SOLUTION_STEMS = {
    "gen", "generator", "make", "maker", "data", "brute", "baoli", "std",
    "checker", "check", "validator", "val", "template", "tmp", "test",
    "compile", "run", "lib", "header", "main_gen",
}

# Special Judge 检查器命名
CHECKER_EXACT_STEMS = {
    "checker", "spj", "chk", "judge", "special_judge", "specialjudge",
    "checker_main", "spj_main",
}
CHECKER_HINTS = ("checker", "spj", "chk")


def is_checker_name(stem: str) -> bool:
    lowered = stem.lower()
    if lowered in CHECKER_EXACT_STEMS:
        return True
    return any(hint in lowered for hint in CHECKER_HINTS)

MAX_DEPTH = 4
MAX_FILES = 5000


def natural_key(text: str):
    return tuple(int(part) if part.isdigit() else part.lower()
                 for part in re.split(r"(\d+)", text))


def short_id(text: str) -> str:
    return hashlib.md5(text.lower().encode("utf-8", "replace")).hexdigest()[:12]


@dataclass
class CaseFile:
    stem: str
    in_path: Path
    ans_path: Optional[Path]
    rel: str
    size: int
    order: tuple

    @property
    def cid(self) -> str:
        return short_id(str(self.in_path))

    @property
    def display(self) -> str:
        return self.rel


@dataclass
class ScanResult:
    folder: Path
    name: str
    cases: List[CaseFile] = field(default_factory=list)
    sources: List[Path] = field(default_factory=list)
    source: Optional[Path] = None
    exe: Optional[Path] = None
    checkers: List[Path] = field(default_factory=list)
    truncated: bool = False


# --------------------------------------------------------------------------- #
# 文件遍历
# --------------------------------------------------------------------------- #

def iter_files(root: Path, max_depth: int = MAX_DEPTH) -> Iterable[Path]:
    """广度优先遍历，跳过垃圾目录，限制深度与数量。"""
    count = 0
    root = Path(root)
    queue: List[Tuple[Path, int]] = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        entries.sort(key=lambda e: natural_key(e.name))
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if depth + 1 > max_depth:
                        continue
                    if entry.name.lower() in JUNK_DIRS or entry.name.startswith("."):
                        continue
                    queue.append((Path(entry.path), depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    count += 1
                    if count > MAX_FILES:
                        return
                    yield Path(entry.path)
            except OSError:
                continue


# --------------------------------------------------------------------------- #
# 数据识别
# --------------------------------------------------------------------------- #

def _pair_key(path: Path) -> Tuple[str, str]:
    return (os.path.normcase(str(path.parent)), path.stem.lower())


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return path.name


def collect_cases(root: Path) -> List[CaseFile]:
    """扫描文件夹，配对 *.in 与 *.out / *.ans。"""
    root = Path(root)
    in_files: List[Path] = []
    ans_map: Dict[Tuple[str, str], Path] = {}
    scan = list(iter_files(root))

    for path in scan:
        ext = path.suffix.lower()
        if ext in IN_EXTS:
            in_files.append(path)
        elif ext in ANS_EXTS:
            key = _pair_key(path)
            # 同名多种答案后缀时，优先 .out / .ans
            old = ans_map.get(key)
            if old is None or (old.suffix.lower() not in (".out", ".ans") and ext in (".out", ".ans")):
                ans_map[key] = path

    cases: List[CaseFile] = []
    seen = set()
    for in_path in in_files:
        ans = ans_map.get(_pair_key(in_path))
        key = os.path.normcase(str(in_path))
        if key in seen:
            continue
        seen.add(key)
        try:
            size = in_path.stat().st_size
        except OSError:
            size = 0
        rel = _rel(in_path, root)
        rel_dir = str(Path(rel).parent).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        cases.append(CaseFile(
            stem=in_path.stem,
            in_path=in_path,
            ans_path=ans,
            rel=rel,
            size=size,
            order=(rel_dir.lower(), natural_key(in_path.stem), in_path.name.lower()),
        ))

    # 没有 .in 只有 input.txt / output.txt 的情况
    if not cases:
        input_txt = root / "input.txt"
        output_txt = root / "output.txt"
        if input_txt.is_file():
            cases.append(CaseFile(
                stem="input",
                in_path=input_txt,
                ans_path=output_txt if output_txt.is_file() else None,
                rel="input.txt",
                size=input_txt.stat().st_size,
                order=("", natural_key("input"), "input.txt"),
            ))

    cases.sort(key=lambda c: c.order)
    return cases


# --------------------------------------------------------------------------- #
# 程序识别
# --------------------------------------------------------------------------- #

def _score_source(path: Path, folder: Path, case_stems: set) -> int:
    stem = path.stem.lower()
    if is_checker_name(stem):
        return -1000
    score = 0
    if stem == folder.name.lower():
        score += 100
    if stem in case_stems:
        score -= 200
    if stem in NON_SOLUTION_STEMS:
        score -= 300
    if any(word in stem for word in ("brute", "baoli", "gen", "check", "std", "solve_")):
        score -= 40
    if stem in ("main", "solution", "sol", "answer", "ans", "code", "submit"):
        score += 60
    if path.parent == folder:
        score += 20
    ext = path.suffix.lower()
    if ext in (".cpp", ".cc", ".cxx"):
        score += 10
    elif ext == ".c":
        score += 4
    elif ext == ".py":
        score += 2
    # 越新越可能是当前程序
    try:
        score += min(path.stat().st_mtime, 2_000_000_000) / 1e12
    except OSError:
        pass
    return score


def detect_source(folder: Path, cases: List[CaseFile]) -> Tuple[Optional[Path], List[Path]]:
    folder = Path(folder)
    case_stems = {c.stem.lower() for c in cases}
    candidates: List[Path] = []
    scan = list(iter_files(folder, max_depth=2))
    for path in scan:
        if path.suffix.lower() not in SOURCE_EXTS:
            continue
        if is_checker_name(path.stem):
            continue
        # 只考虑文件夹根目录以及一层子目录
        try:
            depth = len(path.relative_to(folder).parts) - 1
        except ValueError:
            continue
        if depth > 1:
            continue
        candidates.append(path)

    if not candidates:
        return None, []

    ranked = sorted(
        candidates,
        key=lambda p: (-_score_source(p, folder, case_stems), natural_key(p.name)),
    )
    best = ranked[0]
    if _score_source(best, folder, case_stems) < 0:
        return None, ranked
    return best, ranked


def detect_checkers(folder: Path, source: Optional[Path] = None,
                    case_stems: Optional[set] = None) -> List[Path]:
    """找出可能是 Special Judge 检查器的源文件。"""
    folder = Path(folder)
    case_stems = case_stems or set()
    found: List[Path] = []
    for path in iter_files(folder, max_depth=2):
        if path.suffix.lower() not in SOURCE_EXTS:
            continue
        if not is_checker_name(path.stem):
            continue
        if source is not None and path == source:
            continue
        if path.stem.lower() in case_stems:
            continue
        try:
            depth = len(path.relative_to(folder).parts) - 1
        except ValueError:
            continue
        if depth > 1:
            continue
        found.append(path)

    def rank(path: Path):
        stem = path.stem.lower()
        score = 0
        if stem in CHECKER_EXACT_STEMS:
            score += 100
        if path.suffix.lower() in (".cpp", ".cc", ".cxx"):
            score += 10
        if path.parent == folder:
            score += 20
        if "checker" in stem:
            score += 5
        return (-score, natural_key(path.name))

    found.sort(key=rank)
    return found


def detect_exe(folder: Path, source: Optional[Path]) -> Optional[Path]:
    folder = Path(folder)
    names: List[str] = []
    if source is not None:
        names.append(source.stem + ".exe")
    names.append(folder.name + ".exe")
    for name in names:
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- #

def scan_folder(path: str | os.PathLike) -> ScanResult:
    folder = Path(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"文件夹不存在：{folder}")
    cases = collect_cases(folder)
    source, sources = detect_source(folder, cases)
    exe = detect_exe(folder, source)
    checkers = detect_checkers(folder, source, {c.stem.lower() for c in cases})
    return ScanResult(
        folder=folder,
        name=folder.name,
        cases=cases,
        sources=sources,
        source=source,
        exe=exe,
        checkers=checkers,
    )


def pair_import_paths(paths: Iterable[str | os.PathLike]) -> List[Tuple[Path, Optional[Path]]]:
    """把用户选中的文件/文件夹整理成 (输入, 答案) 配对。"""
    in_files: List[Path] = []
    ans_map: Dict[Tuple[str, str], Path] = {}
    folders: List[Path] = []

    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            folders.append(path)
            continue
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in IN_EXTS:
            in_files.append(path)
        elif ext in ANS_EXTS:
            ans_map[_pair_key(path)] = path

    for folder in folders:
        for case in collect_cases(folder):
            if case.in_path not in in_files:
                in_files.append(case.in_path)
            if case.ans_path is not None:
                ans_map.setdefault(_pair_key(case.in_path), case.ans_path)

    pairs: List[Tuple[Path, Optional[Path]]] = []
    used_ans = set()
    for in_path in sorted(in_files, key=lambda p: (str(p.parent).lower(), natural_key(p.name))):
        ans = ans_map.get(_pair_key(in_path))
        if ans is not None:
            used_ans.add(_pair_key(ans))
        pairs.append((in_path, ans))

    # 只选了答案文件：尝试在同目录找同名输入
    for key, ans in ans_map.items():
        if key in used_ans:
            continue
        parent = ans.parent
        stem = ans.stem
        for ext in IN_EXTS:
            candidate = parent / (stem + ext)
            if candidate.is_file():
                pairs.append((candidate, ans))
                break

    pairs.sort(key=lambda item: (str(item[0].parent).lower(), natural_key(item[0].name)))
    return pairs


def case_from_paths(in_path: Path, ans_path: Optional[Path], base: Optional[Path] = None) -> dict:
    """把任意路径的测试数据包装成 case 字典（用于导入）。"""
    in_path = Path(in_path)
    rel = _rel(in_path, base) if base else in_path.name
    try:
        size = in_path.stat().st_size
    except OSError:
        size = 0
    return {
        "id": short_id(str(in_path)),
        "name": in_path.name,
        "stem": in_path.stem,
        "rel": rel,
        "in": str(in_path),
        "ans": str(ans_path) if ans_path else None,
        "hasAnswer": bool(ans_path and Path(ans_path).is_file()),
        "size": size,
        "imported": True,
        "origin": "imported",
    }
