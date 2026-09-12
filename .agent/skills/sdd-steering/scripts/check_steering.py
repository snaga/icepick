#!/usr/bin/env python3
"""
SDD Steering Validator (check_steering.py)
product.md, tech.md, structure.md の外形・構造・必須見出しを検証する自己完結スクリプト。
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

def check_product_md(fp: Optional[Path], strict: bool = False) -> Dict[str, Any]:
    res = {"file": "product.md", "exists": False, "passed": False, "errors": [], "warnings": [], "stats": {}}
    if not fp or not fp.exists():
        res["errors"].append("product.md が見つかりません (doc/product.md または SPECS/product.md)")
        return res

    res["exists"] = True
    content = fp.read_text(encoding="utf-8")
    lines = content.splitlines()

    if strict:
        template_sections = [
            ("製品の目的", r"##\s+🌟\s*製品の目的"),
            ("ターゲットユーザー", r"##\s+🎯\s*ターゲットユーザー"),
            ("主要機能 (Core Features)", r"##\s+✨\s*主要機能\s*\(Core\s+Features\)"),
            ("ビジネス目標 / ゴール", r"##\s+🚀\s*ビジネス目標\s*/\s*ゴール"),
            ("制約事項 / スコープ外", r"##\s+⚠️\s*制約事項\s*/\s*スコープ外"),
        ]
        for name, pat in template_sections:
            if not re.search(pat, content):
                res["errors"].append(f"[Strict] テンプレート準拠の見出し「{name}」が完全一致しません。")
    else:
        required_sections = [
            ("製品の目的", [r"##\s+(?:🌟\s*)?(?:製品の)?目的", r"##\s+Purpose"]),
            ("ターゲットユーザー", [r"##\s+(?:🎯\s*)?ターゲット(?:ユーザー)?", r"##\s+Target\s+Users?"]),
            ("主要機能", [r"##\s+(?:✨\s*)?主要機能", r"##\s+Core\s+Features?"]),
            ("ビジネス目標 / ゴール", [r"##\s+(?:🚀\s*)?ビジネス(?:・開発)?目標", r"##\s+Goals?"]),
        ]
        for name, patterns in required_sections:
            if not any(re.search(pat, content, re.IGNORECASE) for pat in patterns):
                res["errors"].append(f"必須見出し「{name}」が存在しません。")

        scope_patterns = [r"##\s+(?:⚠️\s*)?制約事項", r"##\s+(?:⚠️\s*)?制約条件", r"##\s+コアコンセプト", r"##\s+Constraints?", r"##\s+Scope"]
        if not any(re.search(pat, content, re.IGNORECASE) for pat in scope_patterns):
            res["warnings"].append("制約事項 / コアコンセプト / スコープ外の見出しが見当たりません。")

    if re.search(r"<!-- このプロジェクトが解決しようとしている問題", content):
        res["warnings"].append("テンプレートのプレースホルダーコメントがそのまま残っています。")

    res["stats"]["line_count"] = len(lines)
    res["passed"] = len(res["errors"]) == 0
    return res

def check_tech_md(fp: Optional[Path], strict: bool = False) -> Dict[str, Any]:
    res = {"file": "tech.md", "exists": False, "passed": False, "errors": [], "warnings": [], "stats": {}}
    if not fp or not fp.exists():
        res["errors"].append("tech.md が見つかりません (doc/tech.md または SPECS/tech.md)")
        return res

    res["exists"] = True
    content = fp.read_text(encoding="utf-8")
    lines = content.splitlines()

    if strict:
        template_sections = [
            ("プログラミング言語", r"##\s+🛠️\s*プログラミング言語"),
            ("フレームワーク & ライブラリ", r"##\s+🏗️\s*フレームワーク\s*&\s*ライブラリ"),
            ("テストツール", r"##\s+🧪\s*テストツール"),
            ("開発ツール", r"##\s+🔧\s*開発ツール"),
            ("インフラ / プラットフォーム", r"##\s+📦\s*インフラ\s*/\s*プラットフォーム"),
            ("外部API / サービス", r"##\s+🔗\s*外部API\s*/\s*サービス"),
        ]
        for name, pat in template_sections:
            if not re.search(pat, content):
                res["errors"].append(f"[Strict] テンプレート準拠の見出し「{name}」が完全一致しません。")
    else:
        required_sections = [
            ("言語・ランタイム", [r"##\s+(?:🛠️\s*)?(?:プログラミング)?言語(?:・ランタイム)?", r"##\s+Programming\s+Languages?"]),
            ("ライブラリ / フレームワーク", [r"##\s+(?:🏗️\s*)?(?:フレームワーク|コアライブラリ|ライブラリ)", r"##\s+Frameworks?", r"##\s+Libraries?"]),
        ]
        for name, patterns in required_sections:
            if not any(re.search(pat, content, re.IGNORECASE) for pat in patterns):
                res["errors"].append(f"必須見出し「{name}」が存在しません。")

        optional_sections = [
            ("テストツール", [r"##\s+(?:🧪\s*)?テストツール", r"##\s+Test(?:ing)?\s+Tools?"]),
            ("開発ツール / パターン", [r"##\s+(?:🔧\s*)?開発ツール", r"##\s+アーキテクチャパターン", r"##\s+Dev(?:elopment)?\s+Tools?"]),
        ]
        for name, patterns in optional_sections:
            if not any(re.search(pat, content, re.IGNORECASE) for pat in patterns):
                res["warnings"].append(f"推奨見出し「{name}」が見当たりません。")

    res["stats"]["line_count"] = len(lines)
    res["passed"] = len(res["errors"]) == 0
    return res

def check_structure_md(fp: Optional[Path], strict: bool = False) -> Dict[str, Any]:
    res = {"file": "structure.md", "exists": False, "passed": False, "errors": [], "warnings": [], "stats": {}}
    if not fp or not fp.exists():
        res["errors"].append("structure.md が見つかりません (doc/structure.md または SPECS/structure.md)")
        return res

    res["exists"] = True
    content = fp.read_text(encoding="utf-8")
    lines = content.splitlines()

    if strict:
        template_sections = [
            ("フォルダ構成", r"##\s+📁\s*フォルダ構成"),
            ("命名規則", r"##\s+🏷️\s*命名規則"),
            ("アーキテクチャの方針", r"##\s+🏗️\s*アーキテクチャの方針"),
            ("インポートパターン", r"##\s+🛠️\s*インポートパターン"),
            ("その他設計の決定事項", r"##\s+🔗\s*その他設計の決定事項"),
        ]
        for name, pat in template_sections:
            if not re.search(pat, content):
                res["warnings"].append(f"[Strict] テンプレート推奨見出し「{name}」が見当たりません。")

    if not re.search(r"```", content):
        res["warnings"].append("ディレクトリ構造を表現するコードブロックが見つかりません。")

    if "doc_internal" in content:
        res["errors"].append("公開用の structure.md に非公開の `doc_internal` が記載されています。除外してください。")

    res["stats"]["line_count"] = len(lines)
    res["passed"] = len(res["errors"]) == 0
    return res

def main():
    parser = argparse.ArgumentParser(description="SDD Steering Self-Validator")
    parser.add_argument("--root", default=".", help="プロジェクトルート")
    parser.add_argument("--file", choices=["product", "tech", "structure", "all"], default="all", help="チェック対象")
    parser.add_argument("--strict", action="store_true", help="テンプレート完全一致モード")
    parser.add_argument("--json", action="store_true", help="JSON出力")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    results = []

    if args.file in ["product", "all"]:
        fp = find_file(root, ["doc/product.md", "SPECS/product.md"])
        results.append(check_product_md(fp, args.strict))
    if args.file in ["tech", "all"]:
        fp = find_file(root, ["doc/tech.md", "SPECS/tech.md"])
        results.append(check_tech_md(fp, args.strict))
    if args.file in ["structure", "all"]:
        fp = find_file(root, ["doc/structure.md", "SPECS/structure.md"])
        results.append(check_structure_md(fp, args.strict))

    if args.json:
        print(json.dumps({"results": results, "all_passed": all(r["passed"] for r in results)}, ensure_ascii=False, indent=2))
    else:
        print("=" * 60)
        print("🧭 SDD Steering Self-Validation Report")
        print("=" * 60)
        for r in results:
            icon = "✅ PASS" if r["passed"] else "❌ FAIL"
            print(f"\n📄 [{icon}] {r['file']}")
            for err in r["errors"]:
                print(f"   ❌ エラー: {err}")
            for warn in r["warnings"]:
                print(f"   ⚠️ 警告: {warn}")
            if r["stats"]:
                print(f"   📊 統計: 行数={r['stats'].get('line_count', 0)}")
        print("\n" + "=" * 60)

    if any(not r["passed"] for r in results):
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
