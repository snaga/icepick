# Icepick for Snowflake 🧊⛏️

**AST Query Optimizer for Snowflake**  
抽象構文木（AST）に基づく決定論的静的診断と外科手術的局所パッチにより、クエリのセマンティクス（結果の等価性）を壊すことなく、Snowflakeの巨大バッチクエリを安全かつ爆速に最適化する開発者向けCLIツール。

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Coverage](https://img.shields.io/badge/Coverage-100%25-brightgreen.svg)]()
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://img.shields.io/badge/mypy-strict-blue)](https://mypy-lang.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🌟 なぜ Icepick なのか？ (Why Icepick?)

Snowflake 上で稼働する夜間バッチや dbt モデルなどの複雑な SQL クエリ（数百〜数千行の CTE 連鎖）は、パーティションプルーニングの阻害やメモリ溢れ（Spill）により、膨大なコンピュートコストと実行時間を浪費しがちです。

従来の「クエリ全体をそのまま LLM に丸投げするアプローチ」には、以下のような深刻な課題がありました：

* ❌ **Lost in the Middle**: 長大プロンプトの中間にある重要な JOIN や WHERE 句を LLM が見落とす。
* ❌ **意図しないコード破壊**: 修正すべきでない健全なロジックやインデント、コメントを勝手に改変する。
* ❌ **ハルシネーション**: 存在しない関数やテーブルを捏造し、本番障害・データ不整合を引き起こす。

**Icepick はこの問題を「決定論的 AST 診断 × 外科手術的局所パッチ」で解決します。**  
コード全体の 95% 以上の健全な構文木をそのまま維持し、問題のある患部（20〜30行のノード）だけをミリ単位で置換・削除・平坦化します。

---

## ✨ 主要機能 (Core Features)

1. **決定論的静的診断 (Deterministic SQL Linter)**
   * `sqlglot` を用いて Snowflake 方言の完全な AST を構築し、パフォーマンスを阻害するアンチパターンをミリ秒単位で非破壊検出。
2. **外科手術的 AST In-place 置換 (Targeted In-place Patching)**
   * 健全なコード構造、コメント、インデントを 100% 維持したまま、対象ノードのみを構文木上で機械的に差し替え（`node.replace()`）または安全に切り落とし（`node.pop()`）。
3. **インラインサブクエリの CTE 自動平坦化 (`SubqueryToCTE`)**
   * FROM/JOIN 句に深くネストした派生テーブル（Derived Table）を、**AST ノード深度降順（深さ優先）** で抽出し、依存順序を完全保証しながらトップレベルの `WITH` 句（CTE）へ自動昇格。
4. **超軽量・高速な局所 LLM 連携 (Gemini & Vertex AI REST)**
   * 重厚な外部 SDK（LiteLLM 等）を完全排除し、`httpx` による直接 REST 呼び出しで爆速起動を実現。
   * 患部ノードの周辺文脈のみを最小限スライス（`ContextSlicer`）してプロンプト化し、ハルシネーションを極小化。
   * LLM の返答は必ず `sqlglot.parse_one` で事前検証し、構文不正時は元の AST を一切壊さず安全にフォールバック（Fail-Safe）。
5. **Git 互換の Unified Diff ＆ 対話型適用 (`git add -p` モデル)**
   * AST 正規化により、インデント差異による偽陽性（ノイズ差分）を完全に排除した純粋な意味差分を出力。
   * `--interactive` モードにより、変更箇所（Hunk）ごとに開発者が `[y]/[n]/[q]` で個別承認・適用。
6. **決定論的等価性自動検証 (Equivalence Verification)**
   * 元クエリと最適化クエリの双方向 `EXCEPT` 差分ゼロ検証クエリを動的生成。セマンティクスが 1 行も変化していないことを数学的・集合論的に証明。

---

## 📋 診断ルール一覧 (Diagnostic Rules)

| ルールID | ルール名 | 重要度 | 診断対象と最適化アクション |
| :--- | :--- | :---: | :--- |
| **`SNOW-001`** | **Non-Sargable Predicate** | `HIGH` | `DATE(col) = '2026-09-01'` 等の関数ラップによるプルーニング阻害を検知し、`col >= '...' AND col < DATEADD(...)` の範囲条件へ自動置換。 |
| **`SNOW-003`** | **Redundant Sort in Subquery/CTE** | `MEDIUM` | `LIMIT` / `FETCH` を持たない中間 CTE やサブクエリ内の無意味な `ORDER BY`（Spill や無駄なソートコストの原因）を検知し、安全に削除（`pop()`）。 |
| **`SNOW-007`** | **Inline Subquery in FROM/JOIN** | `LOW` | FROM 句や JOIN 句に直接埋め込まれた派生テーブルを検知し、トップレベル CTE への抽出・平坦化を推奨。 |

*(※ 将来対応予定: `SNOW-002` 相関副クエリの結合化, `SNOW-004` 暗黙クロス結合, `SNOW-005` 重複テーブルスキャン, `SNOW-006` UNION ALL 置換)*

---

## 🏗️ システムアーキテクチャ

```mermaid
flowchart TD
    CLI["icepick CLI (Typer / Rich)"] --> Parser["SQLParser (sqlglot)"]
    Parser --> AST["Snowflake Root AST"]
    AST --> Linter["LinterEngine"]
    Linter --> Rules["Rules (SNOW-001, 003, 007)"]
    Rules --> Issues["List[DiagnosticIssue]"]
    Issues --> Patcher["ASTPatcher"]
    Patcher -->|Rule-based| InPlace["In-place Node Replacement"]
    Patcher -->|Subquery to CTE| CTEExt["SubqueryToCTE (Flattening)"]
    Patcher -->|LLM-based| Slicer["ContextSlicer"]
    Slicer --> LLMClient["LLMClient (httpx: Gemini / Vertex)"]
    LLMClient --> InPlace
    CTEExt --> InPlace
    InPlace --> Diff["DiffFormatter (difflib)"]
    Diff --> Terminal["Rich Color Unified Diff / .patch"]
    Terminal --> Verifier["EquivalenceVerifier"]
    Verifier --> DB["Snowflake (EXCEPT Verification)"]
```

---

## 🚀 インストール

### 前提要件
* Python 3.10 以上

### pip でインストール
```bash
# クローン後にパッケージをインストール
pip install -r requirements.txt
pip install -e .

# 開発用（テスト・静的解析ツールを含む）
pip install -r requirements-dev.txt
```

### uv で爆速インストール (推奨)
```bash
uv pip install -e ".[dev]"
```

---

## 💻 使い方 (Usage)

### 1. クエリのアンチパターン診断 (`check`)
クエリ内のパフォーマンス阻害要因をスキャンし、リッチなカラーテーブルで一覧表示します。

```bash
icepick check models/batch_mart.sql
```

```text
                      Diagnostic Report: batch_mart.sql                      
+-----------------------------------------------------------------------------+
| Rule ID  | Rule Name              | Severity | Line | Description           |
|----------+------------------------+----------+------+-----------------------|
| SNOW-001 | Non-Sargable Predicate | HIGH     | -    | Column                |
|          |                        |          |      | 'event_timestamp' is  |
|          |                        |          |      | wrapped in a          |
|          |                        |          |      | date/cast function... |
| SNOW-003 | Redundant Sort in CTE  | MEDIUM   | -    | Redundant ORDER BY    |
|          |                        |          |      | without LIMIT...      |
+-----------------------------------------------------------------------------+
```
*(※ CI パイプラインでの使用に適しており、問題検出時は終了コード `1`、問題なし時は `0` を返します)*

---

### 2. クエリの最適化 ＆ 差分プレビュー (`fix`)

#### 差分のみをターミナルで確認する (`--diff`)
```bash
icepick fix models/batch_mart.sql --diff
```
```diff
--- a/models/batch_mart.sql
+++ b/models/batch_mart.sql
@@ -16,7 +16,8 @@
     MAX(event_timestamp) AS last_active
   FROM raw_events
   WHERE
-    TO_DATE(event_timestamp) = '2026-09-01'
+    event_timestamp >= '2026-09-01'
+    AND event_timestamp < DATEADD(DAY, 1, '2026-09-01')
   GROUP BY
     user_id
```

#### 対話形式で変更を 1 つずつ確認・適用する (`--interactive`)
`git add -p` と同様のメンタルモデルで、差分ごとに適用（`[y]/[n]/[q]`）を選択できます。
```bash
icepick fix models/batch_mart.sql --interactive
```

#### インラインサブクエリを CTE に平坦化する (`--flatten-subqueries`)
```bash
icepick fix models/batch_mart.sql --flatten-subqueries --write
```

#### 元ファイルを安全に直接上書きする (`--write` / `-w`)
```bash
icepick fix models/batch_mart.sql --write
```

#### パッチファイルを出力する (`--patch` / `-p`)
```bash
icepick fix models/batch_mart.sql --patch patches/batch_mart.patch
```

---

### 3. セマンティクス等価性の検証 (`verify`)
元クエリと最適化クエリが同一の結果セットを返すことを証明する双方向 `EXCEPT` クエリを生成・確認します。

```bash
# 検証用 SQL をターミナルに表示（Snowflake Web UI や SnowSQL で即実行可能）
icepick verify models/batch_mart.sql models/batch_mart_optimized.sql --dry-run
```

```sql
WITH orig AS (
  /* 元のクエリ */
  ...
),
opt AS (
  /* 最適化後のクエリ */
  ...
)
SELECT 'orig_not_in_opt' AS diff_type, COUNT(*) AS cnt FROM (SELECT * FROM orig EXCEPT SELECT * FROM opt)
UNION ALL
SELECT 'opt_not_in_orig' AS diff_type, COUNT(*) AS cnt FROM (SELECT * FROM opt EXCEPT SELECT * FROM orig);
```
*(※ 双方向の `cnt` がともに `0` であれば、結果セットの一致が数学的に保証されます)*

---

## ⚙️ 設定 (Configuration)

環境変数または設定ファイル（JSON / TOML）により、デフォルト動作をカスタマイズできます。

### 環境変数
| 環境変数名 | 説明 | デフォルト値 |
| :--- | :--- | :--- |
| `ICEPICK_DIALECT` | 対象 SQL 方言 | `snowflake` |
| `ICEPICK_ENABLED_RULES` | 有効化するルールID（カンマ区切り） | すべて有効 |
| `ICEPICK_DISABLED_RULES` | 無効化するルールID（カンマ区切り） | なし |
| `ICEPICK_LLM_PROVIDER` | LLM プロバイダ (`gemini` または `vertex`) | `gemini` |
| `GEMINI_API_KEY` | Google AI Studio の API キー | なし |
| `GCP_PROJECT` | Google Cloud プロジェクト ID (Vertex AI 用) | なし |
| `GCP_LOCATION` | Google Cloud リージョン (Vertex AI 用) | `us-central1` |

---

## 🧪 テストと品質保証

Icepick は厳格なスペック駆動開発（SDD）およびテスト駆動開発（TDD）に基づき、**100% のテストカバレッジ** を維持して開発されています。

```bash
# 全テスト実行 & カバレッジ測定
uv run pytest --cov=icepick

# 高速静的解析
uv run ruff check src tests

# 厳格型チェック
uv run mypy src tests
```

---

## 📄 ライセンス

[MIT License](LICENSE)
