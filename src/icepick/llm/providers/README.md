# LLM Providers Module (`icepick.llm.providers`)

## 1. 責務 (Intent / Responsibility)
- 各 LLM バックエンド（Google AI Studio Gemini, Google Cloud Vertex AI 等）固有の認証方式、REST エンドポイント URL 生成、HTTP リクエスト送受信、およびヘルスチェック（接続診断）の実装とカプセル化を担当します。
- プロバイダごとの固有の差異（APIキー認証 vs GCP ADC OAuth2 Bearer トークン認証、エンドポイント形式の違いなど）を吸収し、上位層に対して統一的なテキスト生成インターフェースおよびヘルスチェック結果を提供します。

## 2. 依存制約 (Dependency Boundaries)
- **インターフェース準拠**: すべてのプロバイダ実装は `BaseLLMProvider` 抽象基底クラスを継承し、`name` プロパティ、`generate_text()`、`health_check()` メソッドを実装します。
- **通信ライブラリ**: 外部 SDK や `httpx.Client` を用いて通信を行い、テスト容易性のためにクライアントのインジェクションをサポートします。
- **例外の抽象化**: 呼出元（`LLMClient` や `ConnectionTester`）に対してプロバイダ固有の低レベル例外を直接漏らさず、一貫した標準例外（`AuthenticationError`, `ValueError`, `httpx.HTTPError`）を送出します。
- **ヘルスチェックの標準化**: `health_check()` は常に標準化された辞書（`success`, `duration_ms`, `message`, `details`, `actionable_advice`）を返却し、未設定項目に対する実用的な修正手順（actionable advice）を提供します。

## 3. 新規プロバイダの追加手順 (How to Extend)
新規バックエンド（例: OpenAI, Anthropic, ローカルLLM等）を追加する場合は以下の手順に従います：

1. **プロバイダクラスの実装**:
   `BaseLLMProvider` を継承したクラスを作成します。
   ```python
   from icepick.llm.providers.base import BaseLLMProvider


   class CustomProvider(BaseLLMProvider):
       @property
       def name(self) -> str:
           return "custom"

       def generate_text(self, prompt: str) -> str:
           # 認証・リクエスト送信・レスポンス解析ロジック
           ...

       def health_check(self) -> dict[str, Any]:
           # 疎通確認と成否・遅延・アドバイスの辞書返却
           ...
   ```
2. **レジストリへの登録**:
   `register_provider()` 関数を用いて登録（またはエイリアス登録）します。
   ```python
   from icepick.llm.providers import register_provider

   register_provider("custom", CustomProvider)
   ```
3. **ファクトリ経由での生成**:
   `create_provider(name="custom", model="...", options={...})` により透過的にインスタンス化可能になります。

## 4. 技術選定理由 (Why)
- **オープン・クローズドの原則 (OCP)**: プロバイダごとに異なる認証方式（APIキー vs GCP ADC Bearer Token）やリクエスト構造を個別クラスに完全に分離し、設定を汎用 Dict (`options`) で受けることで、既存コード（`LLMClient` 等）に手を加えることなく新しい LLM プロバイダを追加・差し替え可能にしています。
- **テスト容易性と保守性**: 各プロバイダが独立したテストスイート（`tests/test_llm_providers.py`）でユニットテスト可能となり、エンドポイント仕様の変更やモック注入を局所的に検証できます。
