# icepick パッケージモジュール境界仕様 (L1 Intent)

## 1. パッケージの責務 (Responsibilities)
`icepick` は、Snowflake SQL を対象とした決定論的 AST（抽象構文木）解析、静的アンチパターン診断、局所 In-place 最適化、Unified Diff 生成、および等価性検証を統括するコアパッケージである。

本パッケージが担う主な責務：
- **CLI コントローラー (`cli.py`)**: ユーザー入力の受付、コマンドフラグの解釈、処理パイプライン（`diag` ➔ `diff` ➔ `fix` ➔ `verify`）のオーケストレーション。
- **設定管理 (`config.py`)**: ルール有効化・無効化、Snowflake 方言設定、実行モード（対話・バッチ）の一元管理。
- **パーサー基盤 (`parser.py`)**: `sqlglot` を基盤とする Snowflake SQL の構文解析および構文エラーの詳細レポート。
- **静的診断エンジン (`linter/`)**: パーティションプルーニング阻害（Non-Sargable WHERE）、不要 ORDER BY、ネストされたインラインサブクエリ等のアンチパターン検出。
- **処方箋エンジン (`prescription/`)**: 構造化処方箋プラン（`PrescriptionPlan`）の生成、局所置換 Diff 生成、インプレース適用。
- **AST パッチャー (`patcher/`)**: AST ノードのインプレース置換（`replace()`）およびインラインサブクエリのトップレベル CTE 平坦化、元テキスト書式保持局所置換（`TextSplicer`）。
- **Diff & フォーマッター (`diff/`)**: Git 互換 Unified Diff の生成、Rich による構文ハイライト。
- **等価性検証 SQL 生成 (`verifier/`)**: 双方向 `EXCEPT` によるクエリ実行結果の完全等価性検証 SQL 生成。

## 2. モジュール境界と依存関係の意図 (Module Boundaries & Dependencies)

```mermaid
graph TD
    CLI["icepick.cli"] --> Config["icepick.config"]
    CLI --> Parser["icepick.parser"]
    CLI --> Linter["icepick.linter"]
    CLI --> Prescription["icepick.prescription"]
    CLI --> Patcher["icepick.patcher"]
    CLI --> Diff["icepick.diff"]
    CLI --> Verifier["icepick.verifier"]
    
    Linter --> Config
    Linter --> Parser
    Prescription --> Linter
    Prescription --> Patcher
    Prescription --> Diff
    Verifier --> Config
```

### 依存関係の設計方針
1. **純粋決定論的 AST 操作とゼロ・ネットワーク依存 (ADR-0007)**:
   - 内部 LLM 連携を完全に撤廃し、`sqlglot` を用いた純粋決定論的 AST 操作に純化。外部ネットワーク通信や機密情報漏洩リスクを恒久的に排除する。
2. **単方向依存の原則**:
   - `config.py` は他のサブモジュールに一切依存しない。
   - `linter`, `patcher`, `diff`, `verifier` などの各エンジンは互いに直接依存せず、データモデル（`DiagnosticIssue`, `PrescriptionPlan` や AST オブジェクト）を介して疎結合に連携する。
3. **副作用の局所化**:
   - `linter`, `prescription`, `patcher` のコアロジックは副作用を持たない決定論的関数として実装する。
   - ファイル I/O やターミナル対話はアダプタ層（`cli`）に隔離する。

## 3. 設計判断 (Design Rationale)
- **Fail-Safe 保証**:
   - パッチ適用後の SQL は必ずパーサーによる構文検証を受け、不正な SQL が生成された場合は元のコードを破壊せずフォールバックする。
- **可逆性と透明性**:
   - すべての変更は Git diff 互換の Unified Diff として出力可能とし、開発者が変更内容を完全に把握・検証できる状態を担保する。
