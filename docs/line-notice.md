# LINE接続テスト（notice.py）

このBotはLINEのテキストをそのまま返す**接続確認専用**です。人生会議の回答や実在の個人情報は送らないでください。既存の `run.py`（5000番）とは別の5001番ポートで起動します。

1. LINE公式アカウントを作成し、Messaging APIを有効化して、アクセストークンとチャネルシークレットを取得する。
2. プロジェクト内のPowerShellで以下を実行する。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv install --dev --python 3.14
Copy-Item .env.example .env
```

3. ローカルの `.env` に以下を追加（実際の値はGitにコミットしない）。

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=取得したアクセストークン
LINE_CHANNEL_SECRET=取得したチャネルシークレット
```

4. 次のコマンドで起動する。

```powershell
py -3.14 -m pipenv run python notice.py
```

5. ブラウザで http://127.0.0.1:5001/ を開き、「LINE Echo Bot is running.」を確認する。
6. ngrokにログイン・認証設定後、別のターミナルから `ngrok http 5001` を実行する。表示されたHTTPS URLに `/callback` を付けてLINE DevelopersのWebhook URLに設定し、Webhookを有効化する。
7. Botにダミーの文章を送って同じ文章が返るか確認する。LINE公式アカウントの自動応答設定による返信の重複に注意する。

これは外部のLINEとの通信を確認するための開発用Botであり、回答の共有・暗号化・通知機能は未実装です。既存の `app/__init__.py`、`run.py` は変更しません。
