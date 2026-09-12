#!/usr/bin/env python3
"""
SDD Requirements Validator (check_requirements.py)
requirements.md の外形・必須見出し・ユーザーストーリー・EARS 5大パターンを検証する自己完結スクリプト。
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

def check_requirements_md(fp: Optional[Path]) -> Dict[str, Any]:
    res = {
        "file": "requirements.md",
        "exists": False,
        "passed": False,
        "errors": [],
        "warnings": [],
        "stats": {}
    }
    if not fp or not fp.exists():
        res["errors"].append("requirements.md が見つかりません (doc/requirements.md または SPECS/requirements.md)")
        return res

    res["exists"] = True
    content = fp.read_text(encoding="utf-8")
    lines = content.splitlines()

    required_sections = [
        ("概要", [r"##\s+概要", r"##\s+Overview"]),
        ("前提条件", [r"##\s+前提条件", r"##\s+Pre-?conditions?"]),
        ("要件", [r"##\s+(?:非機能要件|機能要件|要件)", r"##\s+Requirements?"]),
    ]
    for name, patterns in required_sections:
        if not any(re.search(pat, content, re.IGNORECASE) for pat in patterns):
            res["errors"].append(f"必須見出し「{name}」が存在しません。")

    # 制約条件または非機能要件
    constraint_patterns = [r"##\s+制約条件", r"##\s+Constraints?", r"##\s+非機能要件"]
    if not any(re.search(pat, content, re.IGNORECASE) for pat in constraint_patterns):
        res["warnings"].append("見出し「制約条件」または「非機能要件」が見当たりません。")

    # 要件IDの抽出
    req_matches = re.findall(r"-\s+(?:要件\s*([A-Za-z0-9_\-]+)|Requirement\s*([A-Za-z0-9_\-]+))[:：]", content)
    extracted_ids = set()
    for m in req_matches:
        req_id = m[0] or m[1]
        if req_id:
            extracted_ids.add(req_id.strip())

    res["stats"]["requirement_count"] = len(extracted_ids)

    user_stories = re.findall(r"ユーザーストーリー|User\s+Story", content, re.IGNORECASE)
    acceptance_criteria = re.findall(r"受け入れ基準|Acceptance\s+Criteria", content, re.IGNORECASE)

    res["stats"]["user_story_count"] = len(user_stories)
    res["stats"]["acceptance_criteria_count"] = len(acceptance_criteria)

    # EARS 5大パターンの集計
    ears_patterns = {
        "Ubiquitous": len(re.findall(r"［Ubiquitous］|\[Ubiquitous\]", content, re.IGNORECASE)),
        "Event-driven": len(re.findall(r"［Event-driven］|\[Event-driven\]", content, re.IGNORECASE)),
        "State-driven": len(re.findall(r"［State-driven］|\[State-driven\]", content, re.IGNORECASE)),
        "Unwanted-behavior": len(re.findall(r"［Unwanted-behavior］|\[Unwanted-behavior\]", content, re.IGNORECASE)),
        "Optional-feature": len(re.findall(r"［Optional-feature］|\[Optional-feature\]", content, re.IGNORECASE)),
    }
    ears_ja_syntax = len(re.findall(r"(?:とき|場合).*?(?:しなければならない|してはならない)", content))
    res["stats"]["ears_patterns"] = ears_patterns
    res["stats"]["ears_ja_syntax_count"] = ears_ja_syntax
    total_ears = sum(ears_patterns.values()) + ears_ja_syntax
    res["stats"]["total_ears_syntax_matches"] = total_ears

    if len(extracted_ids) > 0 and len(acceptance_criteria) == 0:
        res["errors"].append("要件定義内に「受け入れ基準」のセクションが存在しません。")

    if len(acceptance_criteria) > 0 and total_ears == 0:
        res["warnings"].append("受け入れ基準内にEARS記法の構文（「〜とき、…しなければならない」または「［Event-driven］」等）が見当たりません。")

    res["stats"]["line_count"] = len(lines)
    res["passed"] = len(res["errors"]) == 0
    return res

def main():
    parser = argparse.ArgumentParser(description="SDD Requirements Self-Validator")
    parser.add_argument("--root", default=".", help="プロジェクトルート")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    fp = find_file(root, ["doc/requirements.md", "SPECS/requirements.md"])
    res = check_requirements_md(fp)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        icon = "✅ PASS" if res["passed"] else "❌ FAIL"
        print("=" * 60)
        print(f"📝 SDD Requirements Self-Validation Report [{icon}]")
        print("=" * 60)
        for err in res["errors"]:
            print(f"   ❌ エラー: {err}")
        for warn in res["warnings"]:
            print(f"   ⚠️ 警告: {warn}")
        if res["stats"]:
            print(f"   📊 統計: 要件数={res['stats'].get('requirement_count', 0)}, "
                  f"受け入れ基準数={res['stats'].get('acceptance_criteria_count', 0)}, "
                  f"EARS構文一致={res['stats'].get('total_ears_syntax_matches', 0)}")
            pats = [f"{k}:{v}" for k, v in res['stats'].get('ears_patterns', {}).items() if v > 0]
            if pats:
                print(f"   🎯 EARS内訳: {', '.join(pats)}")
        print("=" * 60)

    if not res["passed"]:
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
