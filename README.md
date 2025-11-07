# Gihyo Software Design to Kindle

技術評論社のマイページから「Software Design」誌の最新号（EPUB）を自動ダウンロードし、Kindleに送信するPythonスクリプトです。

## 機能

- 🔐 技術評論社マイページへの自動ログイン
- 📚 Software Design最新号の自動検出
- 📥 EPUB形式での自動ダウンロード
- 📧 Send-to-Kindle経由での自動送信
- 🔄 重複送信防止機能
- 💾 セッション保存によるログイン効率化

## 前提条件

- Python 3.12以上
- [uv](https://github.com/astral-sh/uv) (Pythonパッケージマネージャー)
- 技術評論社の電子書籍購読アカウント
- KindleのSend-to-Kindle用メールアドレス

## セットアップ

### 1. リポジトリのクローン

```bash
git clone https://github.com/YOUR_USERNAME/gihyo-sd-to-kindle.git
cd gihyo-sd-to-kindle
```

### 2. 依存関係のインストール

```bash
# uvのインストール（未インストールの場合）
curl -LsSf https://astral.sh/uv/install.sh | sh

# Pythonパッケージのインストール
uv sync

# Playwrightブラウザのインストール
uv run playwright install chromium
```

### 3. 環境変数の設定

`.env.example`をコピーして`.env`を作成:

```bash
cp .env.example .env
```

`.env`ファイルを編集して、以下の情報を設定:

```env
# 技術評論社の認証情報
GIHYO_EMAIL=your-email@example.com
GIHYO_PASSWORD=your-password

# SMTP設定（Gmail + Send-to-Kindleの例）
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-gmail@gmail.com
SMTP_PASS=your-app-password  # Googleアプリパスワード
SENDER_EMAIL=your-gmail@gmail.com
KINDLE_EMAIL=your-kindle@kindle.com
```

#### Googleアプリパスワードの取得方法

1. [Googleアカウント](https://myaccount.google.com/)にアクセス
2. 「セキュリティ」→「2段階認証プロセス」を有効化
3. 「アプリパスワード」を生成
4. 生成されたパスワードを`SMTP_PASS`に設定

#### Kindleメールアドレスの確認方法

1. Amazon.co.jpの「コンテンツと端末の管理」にアクセス
2. 「設定」タブ→「パーソナル・ドキュメント設定」
3. 「Send-to-Kindle Eメールアドレス」を確認

### 4. 送信元メールアドレスの登録

Amazonの「承認済みEメールアドレス」に`SENDER_EMAIL`を追加:

1. 「コンテンツと端末の管理」→「設定」
2. 「パーソナル・ドキュメント設定」
3. 「承認済みEメールアドレス」に追加

## 使い方

### ローカルで実行

```bash
uv run python gihyo_sd_to_kindle.py
```

初回実行時はログインが行われ、セッション情報が`storage.json`に保存されます。
2回目以降はセッションが再利用されます。

### デバッグモード

詳細なログとHTMLスナップショットを取得する場合:

```bash
DEBUG=1 uv run python gihyo_sd_to_kindle.py
```

## GitHub Actionsでの自動実行

毎月自動で最新号をKindleに送信する設定例:

1. GitHubリポジトリの「Settings」→「Secrets and variables」→「Actions」
2. 以下のSecretsを追加:
   - `GIHYO_EMAIL`
   - `GIHYO_PASSWORD`
   - `SMTP_HOST`
   - `SMTP_PORT`
   - `SMTP_USER`
   - `SMTP_PASS`
   - `SENDER_EMAIL`
   - `KINDLE_EMAIL`

3. `.github/workflows/sd-to-kindle.yml`を作成:

```yaml
name: Send Software Design to Kindle

on:
  schedule:
    # 毎月18日 午前10時(JST)に実行
    - cron: '0 1 18 * *'
  workflow_dispatch: # 手動実行も可能

jobs:
  send-to-kindle:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install uv
        run: curl -LsSf https://astral.sh/uv/install.sh | sh

      - name: Install dependencies
        run: |
          uv sync
          uv run playwright install chromium --with-deps

      - name: Run script
        env:
          GIHYO_EMAIL: ${{ secrets.GIHYO_EMAIL }}
          GIHYO_PASSWORD: ${{ secrets.GIHYO_PASSWORD }}
          SMTP_HOST: ${{ secrets.SMTP_HOST }}
          SMTP_PORT: ${{ secrets.SMTP_PORT }}
          SMTP_USER: ${{ secrets.SMTP_USER }}
          SMTP_PASS: ${{ secrets.SMTP_PASS }}
          SENDER_EMAIL: ${{ secrets.SENDER_EMAIL }}
          KINDLE_EMAIL: ${{ secrets.KINDLE_EMAIL }}
        run: uv run python gihyo_sd_to_kindle.py
```

## トラブルシューティング

### ログインに失敗する

- 認証情報が正しいか確認
- `storage.json`を削除して再実行

### メール送信に失敗する

- Googleアプリパスワードが正しいか確認
- Amazonの承認済みメールアドレスに登録されているか確認
- ファイルサイズが25MB以下か確認（Gmailの制限）

### EPUBが見つからない

- 技術評論社のマイページで該当号を購入済みか確認
- DEBUGモードで実行してHTMLを確認

## ライセンス

MIT License

## 注意事項

- このツールは個人利用を目的としています
- ダウンロードしたコンテンツの著作権は技術評論社に帰属します
- 商用利用や再配布は禁止されています
