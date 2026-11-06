# LINE定期通知・選択式回答の試作（notice/notice.py）

このBotは、設定した1人へ毎日質問を送り、選択ボタンの回答に応じて返信します。既存の `run.py` とは別に5001番ポートで動かす試作です。質問にはダミーの回答を使い、実在の個人情報は送らないでください。

## 準備

1. LINE公式アカウントでMessaging APIを有効にし、アクセストークンとチャネルシークレットを取得する。
2. リポジトリのフォルダーで依存ライブラリをインストールする。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv install --dev --python 3.14
```

3. `.env` がまだない場合だけ `Copy-Item .env.example .env` を実行する。既存の `.env` がある場合は上書きせず、次の項目を追加・更新する。認証情報やユーザーIDはGitへコミットしない。

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=取得したアクセストークン
LINE_CHANNEL_SECRET=取得したチャネルシークレット
LINE_TARGET_USER_ID=送信先のユーザーID
DAILY_NOTIFICATION_TIME=18:00
ENABLE_SCHEDULE=true
```

自分宛てのテストでは、LINE Developersコンソールの対象チャネル → チャネル基本設定 → あなたのユーザーIDを使います。Uから始まる33文字で、友だち検索用のLINE IDではありません。表示されない場合はビジネスIDとLINEアカウントの連携を確認します。テストする本人が公式アカウントを友だち追加してください。

`DAILY_NOTIFICATION_TIME` は日本時間の24時間表記です。変更後はBotを再起動します。`ENABLE_SCHEDULE=false` にすると送信先の設定なしで受信だけを試せます。

## 起動とWebhook設定

```powershell
py -3.14 -m pipenv run python notice/notice.py
```

ブラウザーで http://127.0.0.1:5001/ を開くと「LINE Private Note Bot is running.」と表示されます。この表示だけではLINEとの接続成功は確認できません。

既存の公開経路がなければ、ngrokのログイン・認証設定後、別のターミナルで `ngrok http 5001` を実行します。表示されたHTTPS URLに `/callback` を付けてLINE DevelopersのWebhook URLに設定し、Webhookを有効にします。既存の公開経路を使っている場合は、同じ5001番ポートへの転送を継続できます。

## すぐに通知を試す

サーバーを起動したまま、リポジトリのフォルダーで別のターミナルを開きます。

```powershell
py -3.14 -m pipenv run python notice/notice.py --send-now
```

このコマンドは質問を1回送信して終了し、スケジューラーは起動しません。送信自体はWebhookなしでも行えますが、ボタン操作への返信には受信用サーバーと公開Webhookが必要です。

送信される質問は「これからの生活で大切にしたい時間は？」です。「趣味の時間」「家族との時間」「まだ決めていない」の3択を表示し、選択に応じた返事を送ります。ボタンの選択結果はPostbackイベントで受け取ります。

利用者がLINEで「質問」と送っても同じ質問を表示します。それ以外のテキストは従来どおりおうむ返しします。質問と返事は `question_message()` と `CHOICES` で変更できます。

## 定期通知

通常起動すると、毎日指定時刻に設定した1人へ同じ質問を送ります。`flask run` や外部WSGIサーバー経由の起動ではスケジューラーは開始しません。この試作では上記コマンドで1プロセスだけ起動してください。複数のプロセスやパソコンで起動すると通知が重複します。

パソコンとプログラムが稼働している間だけ有効です。停止中に過ぎた時刻の通知を、再起動後にまとめて送る機能はありません。スリープ中も指定時刻の動作は保証できません。

## 動作確認

- 手動通知：`--send-now` で質問と3択が届くことを確認する。
- 定期通知：数分後の時刻を設定して再起動し、指定時刻に1回届くことを確認する。
- 選択回答：各ボタンを押し、それぞれ対応する返事が届くことを確認する。「質問」と送ればボタンを再表示できる。
- テキスト受信：「接続テスト」を送り、同じ文章が返ることを確認する。
- LINE公式アカウントの自動応答設定による返信の重複に注意する。

送信APIの受付成功だけでは実際の到達は確認できません。必ずスマートフォンで確認してください。通知音や画面表示はLINE・OSの設定に依存します。プッシュ送信はプランのメッセージ通数・上限の対象です。通信エラー時の自動再送は実装していません。

外部へ送信しないテストは次で実行できます。

```powershell
py -3.14 -m pipenv run python -m unittest discover -s tests -p test_notice.py -v
```

## 未実装の範囲

回答のデータベース保存、家族共有、暗号化保存、既存Webアプリとの接続はありません。回答はLINEのトーク履歴には表示されます。選択回答後の返事でも、ノートやデータベースには保存していないことを明示します。

既存の `app/__init__.py`、`run.py`、他担当の画面・DB処理は変更しません。依存は既存方針どおり `Pipfile` で管理し、APSchedulerとtzdataを追加します。

## 公式資料

- [プッシュメッセージ](https://developers.line.biz/ja/reference/messaging-api/#send-push-message)
- [ユーザーIDの取得](https://developers.line.biz/ja/docs/messaging-api/getting-user-ids/)
- [クイックリプライ](https://developers.line.biz/ja/docs/messaging-api/using-quick-reply/)
- [APScheduler 3.x](https://apscheduler.readthedocs.io/en/3.x/userguide.html)
