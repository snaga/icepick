# icepick.linter モジュール境界仕様 (L1 Intent)

## 1. 責務 (Responsibilities)
`icepick.linter` モジュールは、Snowflake SQL の AST（抽象構文木）を走査し、パフォーマンス阻害要因となるアンチパターンや最適化の機会を決定論的に検出・集約する。

主な責務：
- **決定論的静的診断 (`base.BaseRule`, `engine.LinterEngine`)**:
  - 各ルールクラスが AST をトラバースし、規約違反ノードを発見した際に `DiagnosticIssue` オブジェクトを生成する。
- **診断結果の標準化 (`base.DiagnosticIssue`, `base.Severity`)**:
  - 問題の深刻度、対象 AST ノード、コードスニペット、置換案ノード、LLM 依存フラグを構造化データとして保持する。
- **プラグイン拡張性 & フィルタリング**:
  - `BaseRule` を継承した個別ルール（`SNOW-001`〜`SNOW-007` 等）を `LinterEngine` に動的に登録可能。
  - `icepick.config.Config` に従い、有効化・無効化されたルールのみをフィルタリングして実行する。

## 2. 依存関係の制約 (Dependency Constraints)
- **許可される依存ライブラリ**:
  - Python 標準ライブラリ: `abc`, `dataclasses`, `enum`, `typing`
  - 外部ライブラリ: `sqlglot`
  - 内部モジュール: `icepick.config` (`Config` のみ)
- **禁止される依存**:
  - `icepick.patcher`（AST 置換エンジン）や `icepick.diff`（差分生成・表示）、`icepick.cli` に依存してはならない。
  - 診断処理は **副作用ゼロ（Pure Functions）** を徹底し、入力された AST ノードを変更（mutate）してはならない。

## 3. 設計判断 (Design Rationale)
- **診断と置換の完全分離**:
  - `LinterEngine` の役割は問題の検出と置換案の提示（`suggested_replacement`）までにとどめ、実際の AST への書き換えやファイル反映は `patcher` に委任する。
- **非破壊検査の徹底**:
  - ルール実行中に入力 AST が破壊されることを防ぐため、置換ノードの生成は独立した新規 AST ノードとして構築する。
