# 0003. セキュア認証情報管理とマルチソース設定優先順位ピラミッドの確立

* **ステータス**: Superseded (by [ADR-0007](0007-decouple-llm-integration-and-pure-deterministic-ast-tool-standardization.md))
* **決定日**: 2026-09-13 (更新: 2026-09-16)

---

## 1. 背景と課題 (Context)

Icepick では、LLM 局所リライト（Gemini / Vertex AI）および Snowflake 等価性自動検証（`verify`）のために、API キーやデータベース接続パスワードを扱う必要があります。
しかし、エンタープライズの現場およびオープンソース運用において、以下のセキュリティおよび環境適応上の課題がありました：

1. **平文シークレット漏洩のリスク**:
   - 設定ファイル（`.icepick.toml`, `icepick.json`）や `.env` に API キーやパスワードを平文で保存すると、GitHub への誤コミットやログ漏洩の原因となる。
   - シェル履歴（`.bash_history`, PowerShell PSReadLine）に生のシークレットが残るリスク。
2. **社内環境 (Enterprise VPC) と個人環境の認証ギャップ**:
   - 個人開発では Google AI Studio の `GEMINI_API_KEY` を直接利用することが多いが、企業環境では Google Cloud Vertex AI が指定され、サービスアカウント偽装（`impersonate_service_account`）や gcloud CLI 経由の ADC（Application Default Credentials）が必須となる。
   - Python の `google-auth` ライブラリだけでは、社内の gcloud 偽装設定が反映されないケースや、リージョンが `global` の際のエンドポイント仕様（`aiplatform.googleapis.com`）の誤りによりリクエストが失敗する問題があった。
3. **設定の不透明性（どの環境で動いているか分からない）**:
   - CLI 引数、環境変数、設定ファイル、資格情報マネージャーが混在し、開発者や AI エージェントが「今どのプロバイダ・プロジェクト・モデルで動いているのか」を把握できず、意図しない課金や接続ミスを誘発する恐れがあった。

---

## 2. 決定事項 (Decision)

### ① 厳格な設定優先順位ピラミッド (The Strict Precedence Pyramid)
設定の決定ルールを以下の 5 段階カスケードピラミッドとして確立し、`ConfigResolver` で一元管理します：

```text
【最優先】 1. CLI オプション       (--provider, --model, --timeout, etc.)
              ▲
           2. 環境変数           (ICEPICK_LLM_PROVIDER, GOOGLE_CLOUD_PROJECT, etc.)
              ▲
           3. 設定ファイル       (--config 指定ファイル、または .icepick.toml / icepick.json)
              ▲
           4. セキュア認証情報   (Windows 資格情報マネージャー WCM: icepick:*)
              ▲
【ベース】 5. 組み込みデフォルト (gemini, gemini-3.8-flash, us-central1)
```

### ② Windows 資格情報マネージャー (WCM) によるゼロ・平文ストレージと Snowflake ペア管理
- 機密情報（`gemini_api_key`, `snowflake_user`, `snowflake_password`）は、設定ファイルや一般環境変数から完全に除外し、OS ネイティブの **Windows 資格情報マネージャー（Target: `icepick:*`）** に保存・解決します。
- 特に Snowflake 認証情報については、ユーザ名とパスワードを単一ターゲット **`icepick:snowflake`**（`UserName` にユーザ名、`CredentialBlob` にパスワード）としてセット保管・一括取得します。
- 登録コマンド案内時も、PowerShell の `Get-Credential` を用いたマスク入力手順を推奨し、シェル履歴への平文残留を防止します：
  ```powershell
  $cred = Get-Credential -Message "Enter Snowflake Credentials"
  cmdkey /generic:icepick:snowflake /user:$($cred.UserName) /pass:$($cred.GetNetworkCredential().Password)
  ```
- Windows の `cmdkey` 特有の UTF-16LE（Null Byte `0x00`）混入は、Icepick 内部で自動検知・安全にデコードします。
- CI/CD や一時デバッグに限り、意図的な誤読込みを防ぐため `DEBUG_ICEPICK_` プレフィックス付き環境変数（`DEBUG_ICEPICK_SNOWFLAKE_USER`, `DEBUG_ICEPICK_SNOWFLAKE_PASSWORD`）での上書きのみを特別に許可します。

### ③ 実行時アクティブコンフィグ・フィードバック (Active Configuration Feedback)
- `--agentic` や `--verify-loop` 実行時、アクティブな設定（プロバイダ、モデル、GCPプロジェクト、ロケーション、認証方式）および**「その設定がどのレイヤーで決定されたか（CLI / ENV / FILE / KEYRING / DEFAULT）」**をターミナルに Rich バナーで明示します。
- `--json` 出力時、ルート要素に `runtime_config` オブジェクトを含め、機械可読にプロベナンス（決定元）を出力します。
- `icepick config show` サブコマンドを提供し、現在解決されている全設定値と出処の一覧テーブル（機密情報は `sk-...abcd` でマスク表示）を確認可能にします。

### ④ 会社環境向け Vertex AI キーレス接続の強化
- Vertex AI の `location == "global"` 指定時、プレフィックスなしの `https://aiplatform.googleapis.com` へ自動ルーティングします。
- `google.auth.default()` でトークン取得ができない場合、自動で `gcloud.cmd auth application-default print-access-token`（Windows）または `gcloud`（POSIX）を安全なサブプロセスとして実行し、社内 SSO やサービスアカウント偽装が設定された gcloud CLI から直接 ADC トークンを取得する二重安全フォールバックを実装します。

---

## 3. 影響と評価 (Consequences)

### メリット (Positive)
- **企業セキュリティポリシーへの完全適合**: 設定ファイルへの平文シークレット書き込みが排除され、GitHub 漏洩リスクが根絶されます。
- **個人・会社環境のシームレスな切り替え**: CLI オプション（`-p vertex -m gemini-1.5-pro`）や環境変数の指定だけで、コード変更なしに即座に接続先を切り替えられます。
- **完全な設定透明性と事故防止**: 実行時にどのプロバイダ・プロジェクト・モデルが動いているかが一目瞭然となり、AI エージェントにとっても予期せぬ挙動を自己診断できるようになります。

### トレードオフ・デメリット (Negative / Mitigation)
- **Windows / gcloud への環境依存**: WCM は Windows 専用、`gcloud` サブプロセスは gcloud CLI インストールを前提とします。
  - *対策*: 非 Windows 環境や CI 環境では `DEBUG_ICEPICK_` 環境変数で等価に動作可能とし、gcloud 未インストール時も Actionable なエラー案内を表示して安全にフォールバックします。
