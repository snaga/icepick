tasks_content = """# 実装タスク (tasks.md)

## フェーズ 1: 基盤構築・パーサー・Diffエンジン

- [ ] タスク 1-1: プロジェクト基盤とパッケージ構造の初期化
  - 変更内容:
    - `pyproject.toml`: 依存パッケージ（`sqlglot`, `rich`, `typer`, `pytest`）の定義。
    - `src/snow_opt/__init__.py`: パッケージ初期化。
    - `src/snow_opt/config.py`: ルール設定・Dialect設定管理クラス。
  - テスト戦略: [デトロイト派]
    - `tests/test_config.py`: デフォルト設定のロード・バリデーション検証。

- [ ] タスク 1-2: Snowflake SQLパーサーモジュールの実装
  - 変更内容:
    - `src/snow_opt/parser.py`: `sqlglot` をラップし、BOM除去、Snowflake方言でのAST生成、および構文エラー時の行番号付き例外ハンドリングを実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_parser.py`: 正常なSnowflake SQL、BOM付きSQL、および構文不正なSQLに対するパース挙動と例外の検証。

- [ ] タスク 1-3: Unified Diff フォーマッターの実装
  - 変更内容:
    - `src/snow_opt/diff/formatter.py`: `difflib.unified_diff` を用い、入力クエリの正規化フォーマットと変更後フォーマットの差分生成、および `rich.syntax.Syntax` によるカラーハイライト機能を実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_diff.py`: 意図した変更行のみがHunkとして抽出され、インデント差異による偽陽性が出ないことを検証。

---

## フェーズ 2: 決定論的AST診断ルールエンジン (Linter)

- [ ] タスク 2-1: 診断エンジンのコアインターフェース設計
  - 変更内容:
    - `src/snow_opt/linter/base.py`: `DiagnosticIssue` データクラスおよび `BaseRule` 抽象基底クラスの定義。
    - `src/snow_opt/linter/engine.py`: 全ルールを順次評価し、診断レポートを集約する `LinterEngine` の実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_linter_engine.py`: モックルールを用いたエンジンの集約動作の検証。

- [ ] タスク 2-2: プルーニング阻害ルール (SNOW-001) の実装
  - 変更内容:
    - `src/snow_opt/linter/rules/snow_001_sargable.py`: `DATE(col) = '...'` などの関数ラップ述語を検出し、範囲条件ノードを `suggested_replacement` にセットするルールを実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_rules_sargable.py`: DATE, TO_DATE, TO_VARCHAR 等の各種Non-sargableパターンに対する検出と置換ノード生成の検証。

- [ ] タスク 2-3: サブクエリ内不要ソートルール (SNOW-003) の実装
  - 変更内容:
    - `src/snow_opt/linter/rules/snow_003_sort.py`: LIMIT句やウィンドウ関数を持たないサブクエリ/CTE内の `ORDER BY` を検出するルールを実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_rules_sort.py`: サブクエリ内のORDER BYを正確に検出し、トップレベルSELECTのORDER BYやLIMIT付きORDER BYは除外することを検証。

- [ ] タスク 2-4: インラインサブクエリ検出ルール (SNOW-007) の実装
  - 変更内容:
    - `src/snow_opt/linter/rules/snow_007_nested_subquery.py`: FROM句およびJOIN句に直接ネストされたDerived Tableを検出するルールを実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_rules_nested.py`: FROM句・JOIN句内のサブクエリ検出の検証。

---

## フェーズ 3: AST In-place パッチャー & サブクエリCTE外出し

- [ ] タスク 3-1: AST In-place パッチャーの実装
  - 変更内容:
    - `src/snow_opt/patcher/in_place.py`: `DiagnosticIssue` に紐づくノード置換（`replace()`）およびノード削除（`pop()`）を実行する `ASTPatcher` の実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_patcher.py`: 対象ノードのみが置換され、親・兄弟ノードの構造が完全維持されることを検証。

- [ ] タスク 3-2: インラインサブクエリのトップレベルCTE外出しエンジンの実装
  - 変更内容:
    - `src/snow_opt/patcher/subquery_to_cte.py`: ネストしたサブクエリの内部SELECTを抽出し、一意なCTEとしてトップレベルの `WITH` 句に昇格させ、元の出現箇所をシンプルなテーブル参照に置換するロジックを実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_subquery_to_cte.py`: FROM句・JOIN句の複数サブクエリが順番通りにトップレベルCTEに平坦化されることを検証。

---

## フェーズ 4: 局所スライシング ＆ LLMリライト連携

- [ ] タスク 4-1: コンテキストスライサーの実装
  - 変更内容:
    - `src/snow_opt/llm/slicer.py`: `SNOW-002`（相関副クエリ）等の複雑ノードから、該当ブロックのSQL断片および最小限のテーブル・カラム文脈を抽出してプロンプトを構築するスライサーの実装。
  - テスト戦略: [デトロイト派]
    - `tests/test_slicer.py`: 長大クエリから問題ノードの周辺文脈のみが正しくMarkdownプロンプトにスライスされることを検証。

- [ ] タスク 4-2: LLMクライアントと部分パース・検証
  - 変更内容:
    - `src/snow_opt/llm/client.py`: LLM APIを呼び出し、生成されたSQLコードブロックを `sqlglot.parse_one` で部分パースして構文検証した上で置換ノードを返すクライアントの実装。
  - テスト戦略: [ロンドン派]
    - `tests/test_llm_client.py`: LLMレスポンスをモックし、正常なSQLのパース成功と、構文異常レスポンス時の安全なロールバック挙動を検証。

---

## フェーズ 5: CLI・対話型Hunkレビュー・等価性検証

- [ ] タスク 5-1: TyperによるCLIインターフェース実装
  - 変更内容:
    - `src/snow_opt/cli.py`: `check`, `fix` コマンドの実装。`--interactive` (Hunkごとの `[y]/[n]/[q]` 適用)、`--diff` (Diff標準出力)、`--write` (インプレース保存)、`--patch` (パッチファイル保存) オプションの実装。
  - テスト戦略: [ロンドン派]
    - `tests/test_cli.py`: Typerの `CliRunner` を用いたコマンド引数、インタラクティブプロンプト、終了コードの検証。

- [ ] タスク 5-2: Snowflake EXCEPT等価性検証エンジンの実装
  - 変更内容:
    - `src/snow_opt/verifier/equivalence.py`: 元クエリと最適化クエリの双方向 `EXCEPT` クエリを動的生成し、差分行数が双方0件であることを検証するモジュールを実装。
  - テスト戦略: [ロンドン派]
    - `tests/test_verifier.py`: Snowflakeコネクタをモックし、差分0件時のPASS判定および差分検出時のNG判定・ログ出力を検証。
"""

with open('Temp/Snowflake_Query_Optimizer_PoC/SPECS/tasks.md', 'w', encoding='utf-8') as f:
    f.write(tasks_content.strip() + '\n')
print('Successfully created tasks.md')
