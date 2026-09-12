---
name: sdd-packaging-go
description: Go 言語プロジェクトの配布用単一バイナリパッケージング（リリース前提条件チェック、指定タグ／HEAD のチェックアウト、デバッグシンボル除外ビルド、ZIP圧縮化）を自動化・標準化するためのスキル。
---

# SDD Go パッケージング・マネージャー (`sdd-packaging-go`)

## 役割 (Role)
あなたは **Go 言語プロジェクトのリリース・パッケージング専門家** です。
リリース前提条件を検証したうえで、指定されたバージョン（またはHEAD）をチェックアウトし、デバッグシンボルが除外された軽量な単一バイナリとしてコンパイルして、標準命名規則（`{ツール名}-{バージョン}-{OS}-{ARCH}`）に従って README と共に ZIP パッケージ化します。

> [!IMPORTANT]
> **フェーズ 0 の事前条件チェックを飛ばしてはならない。**
> 4項目すべてが合格するまでビルドに進まないこと。不合格の項目があれば、その内容をユーザーに報告し、対処するか続行するかの判断を仰ぐこと。

---

## パッケージ命名規則 (Naming Convention)

- **パッケージフォルダ名**: `{ツール名}-{バージョン}-{OS}-{ARCH}`
  - 例: `mytool-1.0.0-windows-amd64`（バージョン指定あり / gitタグあり）
  - 例: `mytool-rel_0_7-702fd84-windows-amd64`（タグ未指定＝ `{ブランチ名}-{短縮コミットハッシュ}`）
- **ZIP ファイル名**: `{パッケージフォルダ名}.zip`

> [!IMPORTANT]
> **バージョン番号に `v` プレフィックスを付けてはならない！**
> - ❌ 間違い: `mytool-v1.0.0-windows-amd64.zip`
> - ✅ 正しい: `mytool-1.0.0-windows-amd64.zip`
> - git タグが `v1.0.0` の場合でも、パッケージ名では `v` を除いて `1.0.0` を使用すること。
> - タグが打たれていないコミットをビルドする場合は、バージョン番号ではなく `{ブランチ名}-{短縮コミットハッシュ}`（例: `rel_0_7-702fd84`）を使用すること。

---

# フェーズ 0: 事前条件チェック 🚦

パッケージングに入る前に、以下の4項目を**この順で**確認する。

### 0-1. テストカバレッジが 90% を超えていること

主要ロジックパッケージ（`pkg/` 配下）のステートメントカバレッジを実測する。
**出力が 1 行も出なければ合格**。

```bash
go test ./pkg/... -count=1 -cover 2>&1 | awk '/coverage:/ {
  for (i = 1; i <= NF; i++)
    if ($i == "coverage:") {
      p = $(i+1); sub(/%/, "", p);
      if (p + 0 < 90) printf "  NG  %-28s %s%%\n", $2, p
    }
}'
```

> [!NOTE]
> `cmd/` 配下のエントリーポイントはロジックを持たないため対象外（`./pkg/...` に限定している）。
> テストが FAIL した場合もここで検出できるよう、必ず `-count=1` を付けてキャッシュを無効化すること。

### 0-2. README が最新化されていて、ソースコードと相違がないこと

README に書かれたコマンドが実際にバイナリへ存在するかを機械的に照合する。
一時ビルドしたバイナリの `agent-context` 出力を正とする。

```bash
# 照合用に一時ビルド（dist/ は汚さない）
go build -o /tmp/verify-cli ./cmd/{ツール名}

PYTHONIOENCODING=utf-8 python .claude/skills/sdd-packaging-go/assets/check_readme_sync.py \
  /tmp/verify-cli README.md
```

実在しないコマンドが README に書かれていれば `NG README.md:{行番号}` として報告される。
加えて、**新規追加したコマンドが README に載っているか**を目視で確認すること（スクリプトは「書かれているのに無い」は検出できるが、「あるのに書かれていない」は検出しない）。

### 0-3. 変更履歴が README に記載されていること

README 末尾の `## 📜 更新履歴` に、今回リリースするバージョンの見出しが存在することを確認する。

```bash
grep -n "^### " README.md | head -5
```

- **バージョン指定あり**: そのバージョンの見出し（例: `### 1.0.0 (2026-08-13)`）があること。
- **バージョン指定なし**: `### Unreleased` 相当の見出しがあり、直近の変更内容が反映されていること。

### 0-4. 変更がすべてコミットされていること

追跡ファイルに未コミットの変更が無いことを確認する。

```bash
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  echo "  NG 未コミットの変更があります:"
  git status --porcelain --untracked-files=no
fi
```

未追跡ファイル（`??`）については、**リリースに含めるべきソースが混ざっていないか**を確認する。
`.gitignore` のパターンがソースを巻き込んでいると、ここに現れないまま成果物から欠落する事故が起きるため注意すること。

```bash
git status --porcelain | grep '^??' || echo "  未追跡ファイルなし"
```

---

# フェーズ 1: ビルド対象の決定 🎯

### 1-1. バージョンとリファレンスの決定

| ユーザー指定 | チェックアウト対象 | 使用するバージョン文字列 |
|---|---|---|
| **バージョンあり**（例: `1.0.0`） | そのバージョンの **git タグ** | 指定されたバージョン（`v` プレフィックスは除去） |
| **バージョンなし / タグなし** | **最新の HEAD** | `{ブランチ名}-{短縮コミットハッシュ}`（例: `rel_0_7-702fd84`） |

```bash
# --- バージョン指定あり ---
VERSION="${ARG#v}"                       # 先頭の v を除去
REF=$(git rev-parse --verify "refs/tags/$ARG" 2>/dev/null \
   || git rev-parse --verify "refs/tags/$VERSION")   # タグの存在を確認

# --- バージョン指定なし / タグなし ---
REF=$(git rev-parse HEAD)
BRANCH=$(git branch --show-current 2>/dev/null || git rev-parse --abbrev-ref HEAD)
SHORT_HASH=$(git rev-parse --short HEAD)
if [ -n "$BRANCH" ] && [ "$BRANCH" != "HEAD" ]; then
  SAFE_BRANCH=$(echo "$BRANCH" | tr '/' '-')
  VERSION="${SAFE_BRANCH}-${SHORT_HASH}"     # 例: rel_0_7-702fd84
else
  VERSION="${SHORT_HASH}"                    # 例: 702fd84
fi
```

> [!IMPORTANT]
> 指定されたタグが存在しない場合は、**勝手に近いタグを選ばず**ユーザーに確認すること。

### 1-2. チェックアウト

作業ツリーを汚さないため、**一時的な git worktree** を切ってそこでビルドする。

```bash
WORKDIR=$(mktemp -d)/build
git worktree add --quiet --detach "$WORKDIR" "$REF"
```

ビルド完了後は必ず後片付けする。

```bash
git worktree remove --force "$WORKDIR"
git worktree prune
```

---

# フェーズ 2: ビルドとパッケージング 📦

### 2-1. メタ情報の確認

- **ツール名**: `cmd/` 配下のエントリーポイントディレクトリ名（例: `cmd/mytool/` → `mytool`）
- **Goモジュール名**: `go.mod` の `module` 行から確認（`ldflags` の `-X` パスに使用）
- **プラットフォーム**: `go env GOOS GOARCH` で確認

### 2-2. パッケージフォルダの作成

プロジェクトルート配下の `dist/` に、命名規則に沿ったフォルダを作成する。
**既存の同名フォルダがある場合は、古いファイルの残留を防ぐため一度削除してから作り直す**（削除前に必ず中身を確認すること）。

```bash
PKG="{ツール名}-${VERSION}-$(go env GOOS)-$(go env GOARCH)"
rm -rf "dist/$PKG" && mkdir -p "dist/$PKG"
```

### 2-3. デバッグシンボル除外コンパイル (`-ldflags="-s -w"`)

バイナリサイズを削減するため、シンボルテーブルとデバッグ情報を除外する。
`internal/version` パッケージが存在する場合は `-X` でバージョン文字列を注入する。

```bash
# Windows の場合（拡張子 .exe）
go build -ldflags="-s -w -X '{Goモジュール名}/internal/version.Version=${VERSION}'" \
  -o "dist/$PKG/{ツール名}.exe" ./cmd/{ツール名}

# Linux / macOS の場合
go build -ldflags="-s -w -X '{Goモジュール名}/internal/version.Version=${VERSION}'" \
  -o "dist/$PKG/{ツール名}" ./cmd/{ツール名}
```

### 2-4. リソースファイルの同梱

`README.md` と `LICENSE` を同梱する。

```bash
cp README.md LICENSE "dist/$PKG/"
```

> [!IMPORTANT]
> **同梱するのは git 管理下のファイルのみ。** フェーズ 1 で切った worktree にはコミット済みの内容しか展開されないため、未追跡ファイルはそもそも存在しない。
> 「配布物に入れたいファイルが worktree に無い」場合は、勝手に作業ツリーからコピーせず、**git に追加してコミットするか、同梱しないか**をユーザーに確認すること。

### 2-5. ZIP パッケージ化

```bash
# Windows (PowerShell)
Compress-Archive -Path "dist\$PKG" -DestinationPath "dist\$PKG.zip" -Force

# Linux / macOS
cd dist && zip -r "$PKG.zip" "$PKG/" && cd ..
```

---

# フェーズ 3: 成果物の検証 🔬

ZIP を**別ディレクトリに解凍し、解凍したバイナリで**検証する。ビルド直後のバイナリではなく、実際に配布される中身を確認することが重要。

```bash
# 1. 解凍テスト
VERIFY=$(mktemp -d)
unzip -q "dist/$PKG.zip" -d "$VERIFY"       # PowerShell: Expand-Archive
ls -la "$VERIFY/$PKG/"

# 2. 解凍したバイナリが起動し、注入したバージョンが反映されているか
"$VERIFY/$PKG/{ツール名}" agent-context | head -5

# 3. 実ファイルを使った動作確認（プロジェクトの代表的なコマンドを 1〜2 個）
"$VERIFY/$PKG/{ツール名}" {代表的なサブコマンド} {テストデータ}

# 4. シンボル除外が効いているかサイズを比較
go build -o /tmp/nostrip ./cmd/{ツール名}
ls -la /tmp/nostrip "dist/$PKG/{ツール名}"
```

---

## チェックリスト

### フェーズ 0（事前条件）
- [ ] `pkg/` 配下の全パッケージでカバレッジ 90% 以上を実測したか？（`-count=1` でキャッシュ無効化）
- [ ] README のコマンド記述とバイナリの `agent-context` を照合したか？
- [ ] 新規コマンドが README に追記されているか目視確認したか？
- [ ] README の更新履歴に今回のバージョンの記載があるか？
- [ ] 追跡ファイルに未コミットの変更が無いか？
- [ ] 未追跡ファイルにリリース対象のソースが紛れていないか？

### フェーズ 1（ビルド対象）
- [ ] バージョン指定あり → 該当タグの存在を確認したか？
- [ ] バージョン指定なし → 短縮コミットハッシュをバージョンに採用したか？
- [ ] 一時 worktree でチェックアウトし、作業ツリーを汚していないか？

### フェーズ 2（ビルド）
- [ ] `go env GOOS GOARCH` でプラットフォームを確認したか？
- [ ] バイナリは `-ldflags="-s -w"` でビルドされているか？
- [ ] `internal/version` がある場合、`-X '{モジュール名}/internal/version.Version={バージョン}'` でバージョンを注入したか？
- [ ] パッケージ名が `{ツール名}-{バージョン}-{OS}-{ARCH}` に沿っているか？（`v` プレフィックスなし！）
- [ ] フォルダ内にバイナリと `README.md` / `LICENSE` が同梱されているか？
- [ ] 同梱したファイルはすべて git 管理下のものか？（未追跡ファイルを勝手に含めていないか）

### フェーズ 3（検証・後片付け）
- [ ] ZIP の解凍テストが正常に通過したか？
- [ ] **解凍したバイナリ**が起動し、注入バージョンが反映されているか？
- [ ] 実ファイルでの動作確認を行ったか？
- [ ] 一時 worktree を `git worktree remove` で削除したか？
- [ ] ZIP アーカイブのサイズが妥当か確認したか？
