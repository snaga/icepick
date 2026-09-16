# 0007. LLM 連携の完全委譲と純粋決定論的 AST ツールへの純化

* **ステータス**: Accepted
* **決定日**: 2026-09-16
* **関連 ADR**:
  - [ADR-0003: セキュア認証情報プロバイダと優先順位ピラミッド](0003-secure-credential-management-and-multi-source-precedence.md) (Superseded by ADR-0007)
  - [ADR-0004: Snowflake DB 直接実行の廃止と等価性検証 SQL 生成への責任分離](0004-decouple-snowflake-execution-and-pure-verification-sql-generation.md) (強化・深化)
  - [ADR-0005: 処方箋ファーストアーキテクチャ](0005-prescription-first-architecture-and-decoupling-diagnosis-from-rewriting.md) (補完)
  - [ADR-0006: レガシーコマンド削除と処方箋駆動パイプラインへの純化](0006-remove-legacy-commands-and-standardize-prescription-pipeline.md) (補完)

---

## 1. 背景と課題 (Context)

Icepick は当初、Snowflake SQL の複雑なアンチパターン（相関副クエリ `SNOW-002` 等）を自律的にリライトするため、内部に LLM クライアント（Google AI Studio Gemini REST API / Google Cloud Vertex AI REST API）および局所コンテキストスライサー（`ContextSlicer`）を内蔵していました（ADR-0002, ADR-0003）。

しかし、近年の AI コーディングエージェント（Antigravity, Claude Code, Cursor 等）の進化と、コード探索 CLI **`ast-digger`** の登場により、以下の本質的な課題が顕在化しました：

1. **二重構造の無駄（エージェントがエージェントを下請け呼び出しするねじれ）**:
   - `icepick` を CLI として呼び出している主体は、すでに強力な推論能力と広大なコンテキストを持つ AI エージェント自身です。
   - エージェントが呼んだ先の CLI ツールが、内部でさらに別の LLM（Gemini 等）を呼び出すのは「下請けの二重構造」であり、プロンプトやモデルの柔軟性を著しく損ねていました。
   - エージェントが CLI ツールに求めているのは「自然言語でのあいまいな推論」ではなく、**「100% 決定論的な構文解析の事実（AST 診断結果、行範囲、所属 CTE、EXCEPT 検証クエリ）」** です。
2. **インフラ・認証責務の肥大化**:
   - 内部で LLM を呼び出すために、Windows 資格情報マネージャー（WCM: `cmdkey`）連携、UTF-16LE デコード、Google ADC 認証、HTTP 通信（`httpx`）、タイムアウト・リトライ処理、接続診断（`icepick config test`）といった、本来の「SQL オプティマイザ」とは無関係なインフラコード（約 1,500 行）を抱え込んでいました。
3. **`ast-digger` の登場による内部スライサーの完全な役目終了**:
   - `ast-digger outline`（CTE 依存 DAG の俯瞰）および `ast-digger symbol`（特定 CTE のピンポイントコード抽出）が実現したことで、長大クエリから問題箇所を切り出す責務は外部ツールで完全に代替可能となりました。
   - `icepick` 自身が構文木から自前でコードをスライスして LLM に送る必要性は完全に消滅しました。
4. **機密 DWH クエリに対するセキュリティ要件**:
   - 企業の DWH バッチクエリには高度な機密情報やビジネスロジックが含まれます。CLI 内部から外部 API への通信が存在することは、導入時のセキュリティ審査における重大な障壁となっていました。

---

## 2. 決定事項 (Decision)

### ① 内部 LLM 連携の完全撤廃
- `icepick` から以下のモジュールおよび機能を完全に削除・廃止します：
  - `src/icepick/llm/` 配下の全実装（`BaseLLMProvider`, `GeminiProvider`, `VertexAIProvider`, `LLMClient`, `ContextSlicer`）
  - `src/icepick/security/credentials.py`（WCM 管理、Gemini API キー解決ロジック）
  - `src/icepick/health/` および `icepick config test`（LLM 接続事前診断）
  - `pyproject.toml` から外部通信ライブラリ `httpx` を削除。
- これに伴い、**ADR-0003（セキュア認証情報プロバイダと優先順位ピラミッド）は正式に Superseded（廃止・後継へ移行）** とします。

### ② 純粋決定論的 AST 最適化エンジンへの純化
- `icepick` の責務を以下の「決定論的な職人ツール」へ純化します：
  1. **決定論的静的診断 (`icepick diag`)**: AST に基づきアンチパターンを検出、一意な処方箋 ID（`RX-001`...）と所属 CTE、ノード種別、Rationale（改善根拠）を提示。
  2. **決定論的局所差分 (`icepick diff`)**: ルールベースで安全に自動置換可能な処方箋（`SNOW-001`, `SNOW-003`, `SNOW-006`, `SNOW-007` 等）に対し、コメント・インデントを 100% 保持した最小 Unified Diff を生成。
  3. **決定論的ファイル適用 (`icepick fix`)**: 指定された処方箋（`--rx`）を元ファイルへインプレース適用。
  4. **決定論的等価性検証 SQL 生成 (`icepick verify`)**: 最適化前後のセマンティクスが 1 行たりとも違わないかを証明する双方向 EXCEPT SQL を副作用なく生成。

### ③ 3層アーキテクチャ（協調エコシステム）の確立
複雑なアンチパターン（相関サブクエリ等）のリライトは、以下の 3 層連携によって安全に実現します：

1. **`icepick diag`**: 問題のある CTE（例: `daily_summary`）と修正方針（例: 「相関サブクエリを窓関数または JOIN に書き換えてください」）をエージェントに提示。
2. **`ast-digger symbol`**: エージェントが該当 CTE ブロック（20〜30 行）のみをピンポイントで切り出し。
3. **`AI エージェント`**: 最新の推論モデル（Claude, Gemini 等）を用い、該当 CTE のみを超低トークンでリライト。
4. **`icepick fix` / 直接適用 ➔ `icepick verify`**: 修正後、双方向 EXCEPT SQL を生成し、公式 `snow CLI` 等で等価性を 100% 証明。

---

## 3. 影響とメリット (Consequences)

### メリット (Positive)
1. **完全ゼロ・クレデンシャル ＆ 100% オフライン動作（セキュリティの究極化）**:
   - データベース認証情報（ADR-0004 で廃止）に続き、LLM API キーも完全に不要化。
   - 外部ネットワーク通信が一切発生せず、CI/CD やエアギャップ環境、機密性の高いエンタープライズ DWH 環境でも安全・爆速で動作する。
2. **圧倒的なコードの軽量化と依存関係の削減**:
   - 複雑な認証・通信・プロバイダコード（約 1,500 行）を丸ごと断捨離。
   - `httpx` 等の不要ライブラリが排除され、`sqlglot` と CLI/UI ライブラリ（`typer`, `rich`）のみの純粋な AST ツールへスリム化。
3. **エージェント・エコシステムとの美しい疎結合**:
   - モデルのアップデートやプロンプトの工夫が `icepick` 本体の改修・リリースに縛られなくなる。
   - `ast-digger`（探索・ピンセット） ＋ `icepick`（診断・メス・検査器） ＋ `エージェント`（頭脳・指揮）という、Unix 哲学に則った美しい単一責任の組み合わせが成立する。

### トレードオフ・留意点 (Negative / Mitigations)
- `icepick` 単体では相関サブクエリ（`SNOW-002`）のコードを直接書き換えることはできず、「処方箋と Rationale の提示」までを担当する（自動修正不可フラグ）。
  - **対応策**: そもそも複雑な相関クエリの書き換えは人間のレビューや AI エージェントの文脈理解が必須であり、処方箋駆動パイプラインと `ast-digger` の組み合わせによって、より安全かつ柔軟に解決可能となる。
