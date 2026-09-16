# icepick.patcher モジュール境界仕様 (L1 Intent)

## 1. 責務 (Responsibilities)
`icepick.patcher` モジュールは、静的診断エンジン (`icepick.linter`) によって検出された `DiagnosticIssue` を受け取り、元の AST（抽象構文木）に対するピンポイントな In-place ノード置換（`replace()`）およびノード削除（`pop()`）を実行する。

主な責務：
- **外科手術的 In-place 置換 (`in_place.ASTPatcher`)**:
  - `issue.suggested_replacement` を用いて、対象ノード（`issue.target_node`）のみをその位置で差し替える。
  - 親ノード、兄弟ノード、未変更の節（SELECTリスト、JOIN条件、エイリアス等）の構文構造とセマンティクスを100%維持する。
- **ノード安全削除 (`pop()`)**:
  - 不要ソート（`SNOW-003`）のように削除が推奨されるノードを、親クエリから安全に除去する。
- **サブクエリ平坦化 (`subquery_to_cte.SubqueryToCTE` - タスク3-2予定)**:
  - ネストしたインラインサブクエリを命名されたトップレベル CTE に昇格させ、元の参照箇所をシンプルなテーブル参照に差し替える。

## 2. 依存関係の制約 (Dependency Constraints)
- **許可される依存ライブラリ**:
  - 外部ライブラリ: `sqlglot`
  - 内部モジュール: `icepick.linter.base` (`DiagnosticIssue` のみ)
- **禁止される依存**:
  - `icepick.cli`（CLI層）、`icepick.verifier`（等価性検証）、外部ネットワーク通信ライブラリに直接依存してはならない。

## 3. Fail-Safe 原則 (Fail-Safe Principle)
- **非破壊フォールバック保証**:
  - `apply_all()` 実行中、個別の Issue の適用時に構文違反やツリー不整合（孤立ノード等）が発生した場合は、該当 Issue を安全にスキップし、他の健全なノード置換を継続する。
  - いかなる場合も不正な AST を書き出さず、元の構文整合性を最優先する。
