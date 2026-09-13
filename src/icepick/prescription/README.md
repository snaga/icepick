# Prescription Module (`icepick.prescription`)

処方箋ファースト（Prescription-First）アーキテクチャに基づく、Snowflake SQL の診断・最適化指示カプセル化モジュール境界仕様（L1 Intent）。

---

## 1. 責務 (Intent / Responsibility)

`icepick.prescription` パッケージは、Snowflake SQL の AST 意味論解析および静的診断結果を受け取り、独立した「処方箋（Prescription）」として構造化・カプセル化する中核モジュールです。

主な責務：
- **処方箋（Prescription）の構造化と ID 採番**:
  - Snowflake SQL から抽出された個別の最適化課題・アンチパターンを、一意な識別子（`RX-001`, `RX-002`, ...）を持つ独立した処方箋オブジェクトとしてカプセル化する。
- **処方箋プラン（`PrescriptionPlan`）の集約とシリアライズ**:
  - 対象ファイル内の全処方箋を集約した診断プランを生成し、機械可読な JSON シリアライズ（`schema_version = "1.0"`）およびデシリアライズ（`to_dict()`, `from_dict()`）を提供する。
- **診断（Diagnosis）と適用（Rewriting / Splicing）の完全な分離**:
  - 診断・修正指示の生成に専念し、修正の適用方法（人間の手動修正、AI エージェントの編集、CLI の自動適用）を呼び出し側に委ねる。

---

## 2. 依存制約 (Dependency Boundaries)

- **許可される依存先**:
  - 外部ライブラリ: `sqlglot`（SQL 構文解析・AST トラバース）
  - 内部パッケージ: `icepick.linter`（`LinterEngine`, `DiagnosticIssue`, `Severity`）
- **禁止される依存先**:
  - `icepick.cli`（CLI 層・プレゼンテーション層への逆流禁止）
  - `icepick.patcher`（パッチ適用処理への直接結合禁止）
  - `icepick.diff`（差分フォーマッターへの直接結合禁止）
- **境界原則**:
  - 本パッケージはリライト（Splicing）やファイル適用の実装から完全に分離され、AST 診断結果から純粋な修正指示（Prescription）を生成する責務のみを担います。

---

## 3. 技術選定理由 (Why)

本モジュールは、**ADR-0005（処方箋ファーストアーキテクチャの導入と診断・適用の責任分離）** に基づき設計されています。

1. **クエリ全文再生成による書式崩れ・コメント消失の防止**:
   - 従来の AST 全文再フォーマットでは、健全な CTE のインデントやインラインコメントが改変され、巨大な Git Diff が発生していました。処方箋単位で患部と修正案を明示することで、元コードを最大限尊重したアプローチが可能になります。
2. **AI エージェントとの親和性最大化 (Agent-Native DX)**:
   - Claude Code や Cursor などの AI コーディングエージェントにとって、巨大な Unified Diff よりも「どの CTE のどの句をどう書き換えるべきか（Why & How）」が構造化された機械可読データ（JSON）の方が、自律的かつ高精度に修正ツール（Edit Tool）を呼び出せます。
3. **人間による段階的レビューと意思決定**:
   - ブラックボックスな全自動上書きではなく、「処方箋 1: 不要ソート削除」「処方箋 2: SARGable 範囲条件化」といった個別の判断単位として提示され、開発者が意図を把握しながら取捨選択できます。

---

## 4. 処方箋データスキーマ (Schema Specification)

### 4.1. `PrescriptionPlan` スキーマ

診断結果全体を表すトップレベルのデータコンテナです。

| フィールド | 型 | 必須 | 説明 |
| :--- | :--- | :---: | :--- |
| `schema_version` | `str` | ✅ | スキーマの後方互換性を保証するバージョン番号（デフォルト: `"1.0"`） |
| `file` | `str` | ✅ | 診断対象となった Snowflake SQL のファイルパスまたは識別子 |
| `issues_count` | `int` | ✅ | 検出された問題 / 処方箋の総件数 |
| `prescriptions` | `list[Prescription]` | ✅ | 生成された個別の最適化処方箋リスト |

### 4.2. `Prescription` スキーマ

個別の最適化指示を表すデータモデルです。

| フィールド | 型 | 必須 | 説明 |
| :--- | :--- | :---: | :--- |
| `id` | `str` | ✅ | 一意な処方箋識別子（例: `"RX-001"`, `"RX-002"`） |
| `rule_id` | `str` | ✅ | 検出元の診断ルール ID（例: `"SNOW-001"`, `"SNOW-003"`） |
| `severity` | `str` (`Severity`) | ✅ | 重要度（`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, `INFO`） |
| `target` | `PrescriptionTarget` | ✅ | 修正対象の位置・AST メタデータ（所属 CTE 名、ノード型、行範囲） |
| `action` | `str` (`PrescriptionAction`)| ✅ | 修正操作種別（`DELETE`: 削除, `REPLACE`: 置換, `INSERT`: 挿入） |
| `original_sql` | `str` | ✅ | 対象箇所の修正前 SQL 断片 |
| `suggested_sql`| `str \| None` | - | 推奨される修正後 SQL 断片（`DELETE` アクション時は `None`） |
| `rationale` | `str` | ✅ | 修正が必要な理由（Why）の説明 |
| `expected_impact`| `str` | ✅ | 期待されるパフォーマンス改善効果（パーティションプルーニング有効化、Spill 削減など） |

### 4.3. `PrescriptionTarget` スキーマ

| フィールド | 型 | 必須 | 説明 |
| :--- | :--- | :---: | :--- |
| `cte` | `str \| None` | - | 対象ノードが属する CTE のエイリアス名（トップレベルクエリの場合は `None`） |
| `node_type` | `str` | ✅ | AST ノードの型名（例: `"Anonymous"`, `"Order"`, `"Join"`） |
| `line_range` | `list[int] \| None` | - | 該当コードの開始行・終了行 `[start_line, end_line]`（取得可能な場合） |

### 4.4. JSON 出力例 (`--format json`)

```json
{
  "schema_version": "1.0",
  "file": "models/mart_orders.sql",
  "issues_count": 2,
  "prescriptions": [
    {
      "id": "RX-001",
      "rule_id": "SNOW-001",
      "severity": "HIGH",
      "target": {
        "cte": "filtered_events",
        "node_type": "Anonymous",
        "line_range": [12, 12]
      },
      "action": "REPLACE",
      "original_sql": "TO_DATE(event_timestamp) = '2026-09-01'",
      "suggested_sql": "event_timestamp >= '2026-09-01' AND event_timestamp < DATEADD(DAY, 1, '2026-09-01')",
      "rationale": "Column 'event_timestamp' is wrapped in a date/cast function in WHERE clause, preventing partition pruning and clustering key usage.",
      "expected_impact": "Enables partition pruning, significantly reducing bytes scanned and warehouse execution time."
    },
    {
      "id": "RX-002",
      "rule_id": "SNOW-003",
      "severity": "MEDIUM",
      "target": {
        "cte": "sorted_base",
        "node_type": "Order",
        "line_range": [25, 25]
      },
      "action": "DELETE",
      "original_sql": "ORDER BY created_at DESC",
      "suggested_sql": null,
      "rationale": "Redundant ORDER BY in CTE 'sorted_base' without LIMIT/FETCH, which Snowflake ignores or wastes sort computation on.",
      "expected_impact": "Eliminates sorting overhead and potential spilling to remote disk."
    }
  ]
}
```
