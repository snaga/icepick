#!/usr/bin/env python3
"""
SDD Design Validator (check_design.py)
design.md の外形・目次・機能一覧テーブル・アーキテクチャ・Mermaid図・IPO記述を検証する自己完結スクリプト。
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

def check_design_md(fp: Optional[Path]) -> Dict[str, Any]:
    res = {
        "file": "design.md",
        "exists": False,
        "passed": False,
        "errors": [],
        "warnings": [],
        "stats": {}
    }
    if not fp or not fp.exists():
        res["errors"].append("design.md が見つかりません (doc/design.md または SPECS/design.md)")
        return res

    res["exists"] = True
    content = fp.read_text(encoding="utf-8")
    lines = content.splitlines()

    required_sections = [
        ("目次", [r"##\s+目次", r"##\s+Table\s+of\s+Contents?"]),
        ("機能一覧", [r"##\s+機能一覧", r"##\s+Features?\s+List"]),
        ("機能詳細", [r"##\s+機能詳細", r"##\s+Feature\s+Details?"]),
        ("アーキテクチャ", [r"##\s+アーキテクチャ", r"##\s+Architecture"]),
    ]
    for name, patterns in required_sections:
        if not any(re.search(pat, content, re.IGNORECASE) for pat in patterns):
            res["errors"].append(f"必須見出し「{name}」が存在しません。")

    # Mermaid 図の解析
    mermaid_blocks = re.findall(r"```mermaid\s*\n(.*?)\n```", content, re.DOTALL | re.IGNORECASE)
    res["stats"]["mermaid_diagram_count"] = len(mermaid_blocks)
    diagram_types = []
    for mb in mermaid_blocks:
        first_line = mb.strip().splitlines()[0].strip() if mb.strip().splitlines() else ""
        diagram_types.append(first_line.split()[0] if first_line else "unknown")
    res["stats"]["mermaid_diagram_types"] = diagram_types

    if len(mermaid_blocks) == 0:
        res["warnings"].append("Mermaid図（アーキテクチャ図・シーケンス図・データフロー等）が記述されていません。")

    # 機能一覧のテーブル構文チェック
    has_table = re.search(r"\|.*?機能ID.*?\|.*?機能名.*?\|", content) or re.search(r"\|.*?Feature ID.*?\|", content)
    if not has_table:
        res["warnings"].append("機能一覧のMarkdownテーブルが見当たりません。")

    # IPO (Input/Processing/Output) 記述チェック
    ipo_matches = len(re.findall(r"(?:入力|Input).*?(?:処理概要|Processing).*?(?:出力|Output)", content, re.DOTALL | re.IGNORECASE))
    res["stats"]["ipo_sections"] = ipo_matches

    res["stats"]["line_count"] = len(lines)
    res["passed"] = len(res["errors"]) == 0
    return res

def main():
    parser = argparse.ArgumentParser(description="SDD Design Self-Validator")
    parser.add_argument("--root", default=".", help="プロジェクトルート")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    fp = find_file(root, ["doc/design.md", "SPECS/design.md"])
    res = check_design_md(fp)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        icon = "✅ PASS" if res["passed"] else "❌ FAIL"
        print("=" * 60)
        print(f"📐 SDD Design Self-Validation Report [{icon}]")
        print("=" * 60)
        for err in res["errors"]:
            print(f"   ❌ エラー: {err}")
        for warn in res["warnings"]:
            print(f"   ⚠️ 警告: {warn}")
        if res["stats"]:
            print(f"   📊 統計: 行数={res['stats'].get('line_count', 0)}, "
                  f"Mermaid図={res['stats'].get('mermaid_diagram_count', 0)} ({', '.join(res['stats'].get('mermaid_diagram_types', []))})")
        print("=" * 60)

    if not res["passed"]:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
