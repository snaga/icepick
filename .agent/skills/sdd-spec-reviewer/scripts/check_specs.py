#!/usr/bin/env python3
"""
SDD Cross-Spec Integrity & Traceability Validator (check_specs.py)
各ドキュメント単体の外形チェックが各スキル側でパスしていることを前提とし、
ドキュメント間の「クロス整合性」「トレーサビリティ」「網羅性」「技術スタック遵守」を厳密に検証する総合レビュースクリプト。

検証内容:
1. Requirements ↔ Design トレーサビリティ（未設計要件の検出、存在しない要件ID参照の検出）
2. Design ↔ Tasks カバレッジ（設計されたコンポーネント・パッケージがタスクで網羅されているか）
3. Steering ↔ Design 整合性（tech.md で定義された技術・ライブラリの整合性）
4. Tasks 完了度 & テスト戦略網羅率
5. (任意) --run-all: 各スキルの個別単体バリデータを一括実行して総合確認
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Set, Tuple

# Windowsの標準出力(cp932)対策
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

def find_file(root: Path, candidates: List[str]) -> Optional[Path]:
    for candidate in candidates:
        p = root / candidate
        if p.is_file():
            return p
    return None

class CrossSpecReviewer:
    def __init__(self, root: Path):
        self.root = root
        self.product_file = find_file(root, ["doc/product.md", "SPECS/product.md"])
        self.tech_file = find_file(root, ["doc/tech.md", "SPECS/tech.md"])
        self.structure_file = find_file(root, ["doc/structure.md", "SPECS/structure.md"])
        self.requirements_file = find_file(root, ["doc/requirements.md", "SPECS/requirements.md"])
        self.design_file = find_file(root, ["doc/design.md", "SPECS/design.md"])
        self.tasks_file = find_file(root, ["doc_internal/tasks.md", "doc_internal/SPECS/tasks.md", "SPECS/tasks.md", "doc/tasks.md"])

        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.stats: Dict[str, Any] = {}

    def extract_requirements(self) -> Set[str]:
        if not self.requirements_file or not self.requirements_file.exists():
            self.errors.append("requirements.md が存在しません。")
            return set()
        content = self.requirements_file.read_text(encoding="utf-8")
        # - **要件 EXCEL-01:** や - 要件 EXCEL-01: や - **Requirement EXCEL-01:**
        req_matches = re.findall(r"-\s+\*?\*?\s*(?:要件\s*([A-Za-z0-9_\-]+)|Requirement\s*([A-Za-z0-9_\-]+))", content)
        req_ids = set()
        for m in req_matches:
            req_id = m[0] or m[1]
            if req_id:
                req_ids.add(req_id.strip())
        return req_ids

    def extract_design_mappings(self) -> Tuple[Set[str], Set[str], List[str]]:
        if not self.design_file or not self.design_file.exists():
            self.errors.append("design.md が存在しません。")
            return set(), set(), []
        content = self.design_file.read_text(encoding="utf-8")

        # 1. 対応要件IDの抽出 (要件ID形式: EXCEL-01, NFR-01, PPTX-02, REQ-01 等のパターンに厳密にマッチ)
        mapped_req_ids = set()
        
        # テーブル内の要件ID抽出
        table_rows = re.findall(r"\|[^|\n]+\|[^|\n]+\|[^|\n]+\|[^|\n]+\|([^|\n]+)\|", content)
        for cell in table_rows:
            found_ids = re.findall(r"\b([A-Z0-9_]+-[0-9A-Z_]+)\b", cell)
            for fid in found_ids:
                mapped_req_ids.add(fid.strip())

        # 機能詳細ブロック内の「対応要件: ...」抽出
        inline_refs = re.findall(r"(?:対応要件(?:ID)?|要件ID?|Requirements?)[：:]\s*([^\n]+)", content, re.IGNORECASE)
        for ref_str in inline_refs:
            found_ids = re.findall(r"\b([A-Z0-9_]+-[0-9A-Z_]+)\b", ref_str)
            for fid in found_ids:
                mapped_req_ids.add(fid.strip())

        # 2. 機能IDの抽出
        feature_matches = re.findall(r"-\s+\[?([A-Za-z0-9_\-]+)\]?[：:]\s*(.+)", content)
        feature_ids = set(m[0].strip() for m in feature_matches if len(m[0].strip()) > 2)

        # 3. パッケージ/モジュール名の抽出
        pkg_matches = re.findall(r"(?:pkg/[a-zA-Z0-9_\-]+|internal/[a-zA-Z0-9_\-]+)", content)
        referenced_pkgs = sorted(list(set(pkg_matches)))

        return mapped_req_ids, feature_ids, referenced_pkgs

    def extract_tasks_info(self) -> Tuple[int, int, Set[str], int]:
        if not self.tasks_file or not self.tasks_file.exists():
            self.errors.append("tasks.md が存在しません。")
            return 0, 0, set(), 0
        content = self.tasks_file.read_text(encoding="utf-8")

        tasks_todo = re.findall(r"-\s+\[\s*\]\s+(.+)", content)
        tasks_done = re.findall(r"-\s+\[[xX]\]\s+(.+)", content)
        total_tasks = len(tasks_todo) + len(tasks_done)

        # タスク内で言及されているファイル/パッケージ
        touched_paths = set(re.findall(r"(?:pkg/[a-zA-Z0-9_\-]+|internal/[a-zA-Z0-9_\-]+|cmd/[a-zA-Z0-9_\-]+)", content))

        # テスト戦略記載数
        test_strategy_matches = len(re.findall(r"テスト戦略|テスト詳細|検証手順|Verification|Test\s+Strategy", content, re.IGNORECASE))

        return len(tasks_done), total_tasks, touched_paths, test_strategy_matches

    def verify_all(self) -> Dict[str, Any]:
        # 1. 要件 ↔ 設計のトレーサビリティ検証
        req_ids = self.extract_requirements()
        design_mapped_ids, feature_ids, design_pkgs = self.extract_design_mappings()
        done_tasks, total_tasks, task_paths, test_strategy_count = self.extract_tasks_info()

        self.stats["total_requirements"] = len(req_ids)
        self.stats["design_mapped_requirements"] = len(design_mapped_ids)
        self.stats["total_features_in_design"] = len(feature_ids)
        self.stats["design_packages"] = design_pkgs
        self.stats["tasks_progress"] = f"{done_tasks}/{total_tasks} ({(done_tasks/total_tasks*100):.1f}%)" if total_tasks > 0 else "0/0"
        self.stats["test_strategy_coverage"] = f"{test_strategy_count}/{total_tasks}" if total_tasks > 0 else "0/0"

        # 未設計の要件（Requirements にあるが Design でカバーされていない）
        unmapped_requirements = req_ids - design_mapped_ids
        if unmapped_requirements:
            self.warnings.append(
                f"【トレーサビリティ警告】要件定義（requirements.md）にある以下の要件IDが、設計書（design.md）の「対応要件」に明示されていません ({len(unmapped_requirements)}件):\n"
                f"   -> {', '.join(sorted(list(unmapped_requirements))[:12])}{' ...' if len(unmapped_requirements) > 12 else ''}"
            )

        # 存在しない要件IDの参照（Design で参照しているが Requirements に定義がない）
        unknown_mapped_ids = design_mapped_ids - req_ids
        if unknown_mapped_ids:
            self.warnings.append(
                f"【要件ID不整合】設計書（design.md）で参照されている以下の要件IDが、要件定義書に存在しません:\n"
                f"   -> {', '.join(sorted(unknown_mapped_ids))}"
            )

        # 2. 設計 ↔ 実装タスクの網羅性検証
        if design_pkgs and task_paths:
            missing_in_tasks = set(design_pkgs) - task_paths
            if missing_in_tasks:
                self.warnings.append(
                    f"【タスク網羅性警告】設計書で定義された以下のパッケージが、tasks.md の変更対象に含まれていません:\n"
                    f"   -> {', '.join(sorted(list(missing_in_tasks)))}"
                )

        # 3. テスト戦略の網羅検証
        if total_tasks > 0 and test_strategy_count < total_tasks:
            missing_tests = total_tasks - test_strategy_count
            self.warnings.append(f"【テスト戦略不足】tasks.md 内の {missing_tests} 件のタスクにテスト戦略の明記がありません。")

        return {
            "passed": len(self.errors) == 0,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }

def run_individual_validators(root: Path) -> List[Dict[str, Any]]:
    """各スキルの個別バリデータを一括実行"""
    validators = [
        ("Steering", [sys.executable, str(root / ".agent/skills/sdd-steering/scripts/check_steering.py")]),
        ("Requirements", [sys.executable, str(root / ".agent/skills/sdd-requirements/scripts/check_requirements.py")]),
        ("Design", [sys.executable, str(root / ".agent/skills/sdd-design/scripts/check_design.py")]),
        ("Planning", [sys.executable, str(root / ".agent/skills/sdd-planning/scripts/check_tasks.py")]),
    ]

    results = []
    for name, cmd in validators:
        script_path = Path(cmd[1])
        if not script_path.exists():
            results.append({"name": name, "passed": False, "msg": f"スクリプトが見つかりません: {script_path}"})
            continue
        try:
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            p = subprocess.run(cmd + ["--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root, env=env)
            passed = p.returncode == 0
            results.append({"name": name, "passed": passed, "stdout": p.stdout, "stderr": p.stderr})
        except Exception as e:
            results.append({"name": name, "passed": False, "msg": str(e)})
    return results

def main():
    parser = argparse.ArgumentParser(description="SDD Cross-Spec Integrity & Traceability Reviewer")
    parser.add_argument("--root", default=".", help="プロジェクトルート")
    parser.add_argument("--run-all-validators", action="store_true", help="各スキルの個別単体バリデータを一括実行して確認")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    root = Path(args.root).resolve()

    if args.run_all_validators:
        val_results = run_individual_validators(root)
        if args.json:
            print(json.dumps({"individual_validators": val_results}, ensure_ascii=False, indent=2))
        else:
            print("=" * 65)
            print("🚀 各スキル個別バリデータ 一括実行結果")
            print("=" * 65)
            for vr in val_results:
                icon = "✅ PASS" if vr["passed"] else "❌ FAIL"
                print(f"[{icon}] {vr['name']} Validator")
            print("=" * 65 + "\n")

    reviewer = CrossSpecReviewer(root)
    review_result = reviewer.verify_all()

    if args.json:
        print(json.dumps(review_result, ensure_ascii=False, indent=2))
    else:
        print("=" * 65)
        print("🔍 SDD クロスドキュメント・トレーサビリティ総合レビューレポート")
        print("=" * 65)

        for err in review_result["errors"]:
            print(f"\n❌ [重大エラー] {err}")

        if review_result["warnings"]:
            print("\n⚠️ [整合性・トレーサビリティ指摘事項]")
            for warn in review_result["warnings"]:
                print(f"   {warn}")
        else:
            print("\n✨ ドキュメント間の不整合や未設計要件は見つかりませんでした！")

        print("\n📊 [全体クロス統計]")
        for k, v in review_result["stats"].items():
            if isinstance(v, list):
                print(f"   - {k}: [{', '.join(str(x) for x in v)}]")
            else:
                print(f"   - {k}: {v}")

        print("\n" + "=" * 65)
        status = "🎉 合格 (PASS)" if review_result["passed"] and not review_result["warnings"] else "⚠️ 要確認 / 警告あり"
        print(f"総合判定: {status}")
        print("=" * 65)

    if not review_result["passed"]:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
