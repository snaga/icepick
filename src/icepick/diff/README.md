# icepick.diff モジュール境界仕様 (L1 Intent)

## 1. 責務 (Responsibilities)
`icepick.diff` モジュールは、SQL クエリの最適化前後の差分計算、正規化による構文ノイズの排除、およびターミナル向けのカラーハイライト表示を担当する。

主な責務：
- **Unified Diff の生成 (`formatter.format_diff`)**:
  - `difflib.unified_diff` をラップし、Git 互換（`git apply` で適用可能）なパッチテキストを生成する。
- **構文正規化による偽陽性排除 (`formatter.normalize_sql`)**:
  - `sqlglot` の Snowflake 方言フォーマッターにより、改行・インデント・キーワード大文字小文字の差異に起因する無意味な差分（ノイズ）を排除し、純粋な意味的変更のみを抽出する。
- **ターミナル描画 (`formatter.render_diff`)**:
  - `rich.syntax.Syntax` を生成し、追加行（緑）、削除行（赤）、Hunk ヘッダー（シアン）をコンソール上にハイライト表示する。
- **Hunk 分割 (`formatter.split_hunks`)**:
  - 対話型リファクタリング（CLI `--interactive` モード）において、Issue/Hunk 単位の個別承認 (`[y]/[n]/[q]`) を行うためのデータ構造を提供する。

## 2. 依存関係の制約 (Dependency Constraints)
- **許可される依存ライブラリ**:
  - Python 標準ライブラリ: `difflib`, `dataclasses`, `re`
  - 外部ライブラリ: `sqlglot`, `rich`
  - 内部モジュール: `icepick.parser` (`parse_snowflake_sql` のみ)
- **禁止される依存**:
  - `icepick.linter` や `icepick.patcher` の内部状態・具体的実装には一切依存してはならない。
  - SQL 文字列または AST のみを入力として受け取る純粋関数として動作する。

## 3. 設計判断 (Design Rationale)
- **Git 互換性の担保**:
  - 生成されるヘッダーは `--- a/<filename>` / `+++ b/<filename>` を基本とし、末尾の改行コードを正しく付与することで、外部の標準 patch/git コマンドとシームレスに連携できるようにする。
- **決定論的 Diff**:
  - 比較対象の双方が同一意味を持つ場合、差分ゼロ（空文字列 `""`）を確実に返却する。
