# プロジェクト構造 (structure.md)

## 📁 フォルダ構成

```text
Snowflake_Query_Optimizer_PoC/
├── doc/                        # SDD 公開仕様書群
│   ├── product.md              # 製品概要・ゴール
│   ├── tech.md                 # 技術スタック
│   ├── structure.md            # プロジェクト構造・規約
│   ├── requirements.md         # EARS記法要件定義
│   └── design.md               # 詳細設計書
├── src/
│   └── icepick/                # メインパッケージ
│       ├── __init__.py
│       ├── cli.py              # CLIエントリポイント (typer)
│       ├── config.py           # 設定管理 (ルール有効化・閾値)
│       ├── parser.py           # sqlglotラッパー・AST基盤
│       ├── linter/             # 決定論的AST診断エンジン
│       │   ├── __init__.py
│       │   ├── base.py         # BaseRule 抽象基底クラス
│       │   ├── engine.py       # ルール実行・レポート集約
│       │   └── rules/          # 個別ルール実装
│       │       ├── snow_001_sargable.py      # Non-Sargable WHERE
│       │       ├── snow_002_correlated.py    # 相関副クエリ
│       │       ├── snow_003_sort.py          # サブクエリ内ORDER BY
│       │       ├── snow_004_cross_join.py    # 暗黙のCROSS JOIN
│       │       ├── snow_005_repeated_scan.py # 重複テーブルスキャン
│       │       ├── snow_006_set_op.py        # UNION vs UNION ALL
│       │       └── snow_007_nested_subquery.py # サブクエリCTE外出し
│       ├── patcher/            # AST In-place置換エンジン
│       │   ├── __init__.py
│       │   ├── in_place.py     # node.replace / node.pop 管理
│       │   └── subquery_to_cte.py # インラインサブクエリのCTE平坦化
│       ├── llm/                # 局所プロンプト生成 & LLM連携
│       │   ├── __init__.py
│       │   ├── slicer.py       # コンテキストスライサー (部分AST抽出)
│       │   └── client.py       # LLM API呼び出しラッパー
│       ├── diff/               # 差分生成・対話UI
│       │   ├── __init__.py
│       │   ├── formatter.py    # Unified Diff生成 (difflib)
│       │   └── interactive.py  # Hunkごとの適用CLI ([y]/[n]/[e]/[q])
│       └── verifier/           # 決定論的等価性検証
│           ├── __init__.py
│           └── equivalence.py  # Snowflake EXCEPT / HASH_AGG 突合
├── tests/                      # テストスイート
│   ├── conftest.py
│   ├── test_parser.py
│   ├── test_linter_rules.py
│   ├── test_patcher.py
│   ├── test_subquery_to_cte.py
│   ├── test_diff.py
│   └── fixtures/               # サンプルSQLファイル群
│       ├── sample_batch.sql
│       └── sample_nested.sql
├── pyproject.toml              # パッケージ定義
└── README.md                   # ツール利用ガイド
```

## 🏷️ 命名規則
- **ファイル名**: `snake_case.py` (例: `snow_001_sargable.py`)
- **クラス名**: `PascalCase` (例: `SnowflakeAstOptimizer`, `NonSargableRule`)
- **関数・メソッド名**: `snake_case()` (例: `diagnose_query()`, `extract_to_cte()`)
- **定数名**: `UPPER_SNAKE_CASE` (例: `DEFAULT_DIALECT = "snowflake"`)
- **ルールID**: `SNOW-XXX` (3桁の連番プレフィックス)

## 🏗️ アーキテクチャの方針
- **単一責任の原則 (SRP)**:
  - 構文パース (`parser.py`)、診断 (`linter/`)、置換 (`patcher/`)、差分提示 (`diff/`)、等価性検証 (`verifier/`) を完全に疎結合に分離する。
- **純粋関数の徹底**:
  - AST診断および置換ロジックは副作用を持たない決定論的関数として実装し、外部I/O（ファイル書き込み、LLM API、Snowflake接続）は明示的なアダプタ層に集約する。
- **Fail-Safe設計**:
  - LLMやルールが生成したパッチコードは必ず `sqlglot.parse_one` で事前バリデーションを行い、構文エラーが発生した場合は元のASTノードを絶対に破壊せず自動フォールバックする。

## 🛠️ インポートパターン
- プロジェクト内モジュールは絶対インポートを使用する：
  ```python
  from icepick.parser import parse_snowflake_sql
  from icepick.linter.base import DiagnosticIssue, BaseRule
  ```
