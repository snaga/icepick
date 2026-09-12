#!/usr/bin/env python3
"""
SDD Planning Validator (check_tasks.py)
tasks.md の外形・チェックボックス形式・テスト戦略（デトロイト派/ロンドン派）・進捗率を検証する自己完結スクリプト。
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

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

def check_tasks_md(fp: Optional[Path]) -> Dict[str, Any]:
    res = {
        "file": "tasks.md",
        "exists": False,
        "passed": False,
        "errors": [],
        "warnings": [],
        "stats": {}
    }
    if not fp or not fp.exists():
        res["errors"].append("tasks.md が見つかりません (doc_internal/tasks.md または doc_internal/SPECS/tasks.md または SPECS/tasks.md)")
        return res

    res["exists"] = True
    content = fp.read_text(encoding="utf-8")
    lines = content.splitlines()

    # タイトル
    if not re.search(r"#\s+(?:実装タスク|Tasks?)", content, re.IGNORECASE):
        res["warnings"].append("メインタイトル「# 実装タスク」が見当たりません。")

    # タスクアイテムの抽出 (- [ ] or - [x])
    tasks_todo = re.findall(r"-\s+\[\s*\]\s+(.+)", content)
    tasks_done = re.findall(r"-\s+\[[xX]\]\s+(.+)", content)
    total_tasks = len(tasks_todo) + len(tasks_done)

    res["stats"]["todo_tasks"] = len(tasks_todo)
    res["stats"]["done_tasks"] = len(tasks_done)
    res["stats"]["total_tasks"] = total_tasks
    res["stats"]["completion_rate"] = f"{(len(tasks_done) / total_tasks * 100):.1f}%" if total_tasks > 0 else "0%"

    if total_tasks == 0:
        res["errors"].append("タスク項目（例: `- [ ] タスクID: 概要`）が記述されていません。")

    # テスト戦略の検出 (デトロイト派 / ロンドン派)
    detroit_matches = len(re.findall(r"デトロイト派|Detroit", content, re.IGNORECASE))
    london_matches = len(re.findall(r"ロンドン派|London", content, re.IGNORECASE))
    res["stats"]["testing_school"] = {
        "Detroit": detroit_matches,
        "London": london_matches,
    }

    test_strategy_matches = re.findall(r"テスト戦略|テスト詳細|検証手順|Verification|Test\s+Strategy", content, re.IGNORECASE)
    res["stats"]["test_strategy_sections"] = len(test_strategy_matches)

    if total_tasks > 0 and len(test_strategy_matches) == 0:
        res["warnings"].append("タスク内に「テスト戦略」または「検証手順」の記述が見当たりません。")

    res["stats"]["line_count"] = len(lines)
    res["passed"] = len(res["errors"]) == 0
    return res

def main():
    parser = argparse.ArgumentParser(description="SDD Planning Self-Validator")
    parser.add_argument("--root", default=".", help="プロジェクトルート")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    fp = find_file(root, ["doc_internal/tasks.md", "doc_internal/SPECS/tasks.md", "SPECS/tasks.md", "doc/tasks.md"])
    res = check_tasks_md(fp)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        icon = "✅ PASS" if res["passed"] else "❌ FAIL"
        print("=" * 60)
        print(f"📋 SDD Planning Self-Validation Report [{icon}]")
        print("=" * 60)
        for err in res["errors"]:
            print(f"   ❌ エラー: {err}")
        for warn in res["warnings"]:
            print(f"   ⚠️ 警告: {warn}")
        if res["stats"]:
            print(f"   📊 統計: 全タスク数={res['stats'].get('total_tasks', 0)} (完了={res['stats'].get('done_tasks', 0)}, 未完了={res['stats'].get('todo_tasks', 0)}), "
                  f"進捗率={res['stats'].get('completion_rate', '0%')}")
            print(f"   🧪 テスト流派: Detroit={res['stats'].get('testing_school', {}).get('Detroit', 0)}, London={res['stats'].get('testing_school', {}).get('London', 0)}")
        print("=" * 60)

    if not res["passed"]:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
