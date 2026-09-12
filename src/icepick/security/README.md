# Icepick Security Module (`icepick.security`)

## 1. モジュールの責務 (Responsibilities)

`icepick.security` は、Icepick が外部システム（Google Gemini LLM、Snowflake 等）と連携する際に必要となる機密情報（APIキー、パスワード等）を安全かつ堅牢に解決・管理するセキュリティ境界モジュールです。

平文設定ファイル（`.env` や `config.json`）の利用やコマンドライン引数による秘密情報の受け渡しを排除し、OSネイティブストレージである **Windows 資格情報マネージャー (WCM: Windows Credential Manager)** をプライマリストレージとして採用しています。

---

## 2. 厳格な優先順位ピラミッド (The Strict Priority Pyramid)

Icepick のクレデンシャル解決は、以下の厳格な優先順位を遵守します。

```text
[優先度: 高]
  1. 一時デバッグ / CI・CD 専用環境変数 (例: DEBUG_ICEPICK_GEMINI_API_KEY)
        │ (未設定の場合)
        ▼
  2. Windows 資格情報マネージャー (Target: icepick:<key>)
        │ (未登録の場合)
        ▼
  3. 自己修正可能なエラー (Actionable AuthenticationError) を送出して異常終了 (Exit 1)
[優先度: 低]
```

### なぜ汎用環境変数を排除するのか？
一般的な名前（`GEMINI_API_KEY`, `SNOWFLAKE_PASSWORD` など）は、親プロセスやグローバル環境、他ツールの設定から意図せず継承・混入されるリスクがあります。
Icepick では意図的な一時実行（デバッグや CI/CD パイプライン）でのみ環境変数を許可するため、必ず `DEBUG_ICEPICK_` プレフィックスを必須化し、汎用環境変数は意図的に探索対象から除外しています。

---

## 3. Windows `cmdkey` UTF-16LE / Null Byte トラップ対策

Windows 標準の `cmdkey /generic:... /pass:...` コマンドで登録された資格情報は、内部的に **UTF-16LE（各文字の後ろに `0x00` が挟まる形式）** で保存されます。
一方、Windows のコントロールパネル（GUI）から登録した場合は通常の UTF-8 / ASCII バイト列として保存されます。

これらを無邪気にデコードすると、HTTP リクエストの `Authorization` ヘッダー破損や予期せぬクラッシュを招きます。
本モジュールの `decode_credential_blob` 関数は、偶数長判定と内部 null バイトの検出により、UTF-16LE と UTF-8/ASCII を自動判別してクリーンな文字列へと復元します。

---

## 4. テスト容易性とモック境界 (Pluggable Mock Boundary)

Windows 資格情報マネージャーの Win32 API (`advapi32.dll` の `CredReadW`) は OS ネイティブストレージに依存するため、CI 環境（Linux 等）やローカル単体テストで直接実行できません。

そのため、以下のモック境界を提供しています：
- `read_wcm_credential_fn`: パッケージレベルの呼び出し可能オブジェクトであり、テストコードから差し替え可能。
- `unittest.mock.patch("icepick.security.credentials.read_wcm_credential", ...)` による安全なモック差し替えが可能。
- 非Windows環境 (`sys.platform != 'win32'`) ではネイティブ API を呼び出さず安全に `None` を返却。
