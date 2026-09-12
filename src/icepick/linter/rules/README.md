# icepick.linter.rules モジュール境界仕様 (L1 Intent)

## 1. 責務 (Responsibilities)
`icepick.linter.rules` パッケージは、Snowflake SQL の AST 上で特定のアンチパターン（パフォーマンス阻害やメモリ肥大化要因）を検出する個別ルール群を格納する。

現在および今後のルール一覧：
- **`SNOW-001` (`snow_001_sargable.py`)**: Non-Sargable WHERE 句（カラムの関数ラップ）の検出と範囲条件へのリライト提示。
- **`SNOW-002` (`snow_002_correlated.py`)**: 相関副クエリの検出（LLM リライト対象フラグ付け）。
- **`SNOW-003` (`snow_003_sort.py`)**: サブクエリ/CTE 内の不要な ORDER BY の検出と削除提示。
- **`SNOW-004`〜`SNOW-007`**: 暗黙 CROSS JOIN、重複スキャン、UNION ALL 最適化、インラインサブクエリ CTE 平坦化。

## 2. 独立性と依存関係の制約 (Independence & Constraints)
- **完全な独立性**:
  - 各ルールクラスは `BaseRule` を継承し、他の個別ルールクラスへの依存・インポートを一切禁止する。
  - ルール間の実行順序に依存しない設計とする。
- **副作用ゼロの保証**:
  - `check(ast)` メソッド内での AST の書き換え（インプレース変更）は禁止。
  - `suggested_replacement` は完全に新規にパース・構築した独立 AST ノードとして生成し、元の AST ツリーを変更しない。
- **Fail-Safe**:
  - `suggested_replacement` を生成する際は、必ずパーサー検証を行い、不正なノードが生成された場合は該当 Issue の提示を安全にスキップまたは警告とする。

## 3. 新規ルール追加ガイドライン
1. ファイル名は `snow_XXX_<name>.py`（小文字スネークケース、3桁のゼロ埋めID）とする。
2. クラス名は `<Name>Rule(BaseRule)` とする。
3. `rule_id`, `rule_name`, `severity`, `description` を定義し、`check(ast) -> list[DiagnosticIssue]` を実装する。
4. `src/icepick/linter/rules/__init__.py` でクラスをエクスポートする。
5. `tests/test_rules_<name>.py` で正常系・異常系・左右配置・非該当パターンを検証する単体テストを作成する。
