# icepick.linter.rules モジュール境界仕様 (L1 Intent)

## 1. 責務 (Responsibilities)
`icepick.linter.rules` パッケージは、Snowflake SQL の AST 上で特定のアンチパターン（パフォーマンス阻害やメモリ肥大化要因）を検出する個別ルール群を格納する。

現在および今後のルール一覧：
- **`SNOW-001` (`snow_001_sargable.py`)**: Non-Sargable WHERE 句（カラムの関数ラップ）の検出と範囲条件へのリライト提示。
- **`SNOW-002` (`snow_002_correlated.py`)**: 相関副クエリの検出（外部AIエージェント連携 / 手動リライト推奨）。
- **`SNOW-003` (`snow_003_sort.py`)**: サブクエリ/CTE 内の不要な ORDER BY の検出と削除提示。
- **`SNOW-004` (`snow_004_implicit_cross_join.py`)**: 暗黙 CROSS JOIN（カンマ区切りFROM）の検出と明示的JOINへのリライト提示。
- **`SNOW-005` (`snow_005_duplicate_scan.py`)**: 複数CTE間での同一ベーステーブル重複スキャンの検出。
- **`SNOW-006` (`snow_006_union.py`)**: 重複排除不要な UNION から UNION ALL への置換提示。
- **`SNOW-007` (`snow_007_nested_subquery.py`)**: FROM/JOIN句内のインラインサブクエリ検出とトップレベルCTE昇格提示。
- **`SNOW-008` (`snow_008_redundant_distinct.py`)**: GROUP BY / 集計関数ブロック内の冗長 DISTINCT 検出と自動削除（`pop()`）提示。
- **`SNOW-009` (`snow_009_qualify_flattening.py`)**: ウィンドウ関数サブクエリの検出と Snowflake ネイティブ `QUALIFY` 句への自動平坦化。
- **`SNOW-010` (`snow_010_cte_multi_reference.py`)**: 同一 CTE の多重参照（3回以上）検出と一時テーブル（TEMPORARY TABLE）マテリアライズ検討警告。
- **`SNOW-011` (`snow_011_huge_in_list.py`)**: 巨大 IN リスト（500要素超）検出とコンパイル過負荷回避のためのリライト警告。
- **`SNOW-012` (`snow_012_select_star.py`)**: 中間 CTE や JOIN における不要な全列展開（`SELECT *`）検出とカラム指定リライト警告。

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

## 4. 将来の拡張方針: Rule Decoupling（汎用コアと方言特化の責任分離）

### 4.1 背景と設計思想
現時点では、Snowflake 専用バッチ最適化ツールとしてその機能の拡充を最優先に堅持しつつ、内部アーキテクチャとしては他 DBMS（Databricks / Spark SQL, PostgreSQL, DuckDB, BigQuery 等）への将来的な移植・横展開が容易な疎結合性を維持する。

### 4.2 ルールの 2 系統分類
現在のルール群は、性質に応じて以下の 2 系統に分類できる：

1. **汎用コア・アンチパターン (Dialect-Agnostic Core)**:
   ANSI SQL / 関係モデル共通のパフォーマンス・メモリ阻害要因。AST 検出ロジックはどの DBMS でも共通。
   - `SNOW-001` (Non-Sargable 述語)
   - `SNOW-003` (サブクエリ/CTE 内の不要な ORDER BY)
   - `SNOW-004` (暗黙のクロス結合 / カンマ区切り FROM)
   - `SNOW-005` (複数 CTE 間での同一テーブル重複スキャン)
   - `SNOW-006` (重複排除不要な UNION ➔ UNION ALL)
   - `SNOW-007` (インラインサブクエリの CTE 昇格)
   - `SNOW-012` (中間パイプライン・JOIN における不要な全列展開 SELECT *)
2. **方言特化アンチパターン (Dialect-Specific Rules)**:
   各 DBMS 固有のオプティマイザ挙動や独自構文に最適化されたルール。
   - `SNOW-009` (ウィンドウ関数サブクエリの Snowflake ネイティブ `QUALIFY` 句統合)
   - `SNOW-010` (CTE 多重参照による Snowflake インライン再計算・一時テーブル化警告)
   - `SNOW-011` (Snowflake オプティマイザのコンパイル過負荷を避ける巨大 IN リスト警告)

### 4.3 今後の Decoupling アプローチ
- **Dialect Strategy / Helper の導入**:
  リライトコード生成（例: 日付加算の `DATEADD` vs `+ INTERVAL` vs `date_add`）をルール本体から小さく分離し、ルール側は「AST パターンマッチング」に専念させる。
- **Snowflake の拡充優先**:
  内部で汎用コアルールへのリファクタリングを行う場合でも、ユーザー体験（CLI `diag`、処方箋 ID `RX-xxx`、JSON スキーマ、ルールカタログ）においては一貫して `SNOW-XXX` とし、ユーザーからは一貫して見える状態を維持する。
