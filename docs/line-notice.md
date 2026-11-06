# LINE通知（app/services/line.py）

設定した1人へ毎日質問を送り、アプリの回答画面（`/answers`）へ案内します。小橋担当の試作（`notice/notice.py`、5001番ポートの別アプリ）を、2026-11-06 に本体のアプリへ統合しました。質問にはダミーの回答を使い、実在の個人情報は送らないでください。

## 回答の扱い

- 通知には、選択ボタン（「趣味の時間」「家族との時間」「まだ決めていない」）と「アプリで回答する」ボタンが付きます。
- **LINEのボタンで選んだ回答は保存しません。** サーバーが回答の平文を受け取って残すと、「サーバーに回答の平文はありません」という約束に反するためです。選択への返事でも、保存していないことと、回答画面のURLを伝えます。
- 回答を残すときは、アプリの回答画面で答えてもらいます。回答はその端末で暗号化してから保存します。
- LINEのトーク履歴には、ボタンで選んだ内容が残ります。

## 準備

1. LINE公式アカウントでMessaging APIを有効にし、アクセストークンとチャネルシークレットを取得する。
2. 依存ライブラリ（`line-bot-sdk`、`apscheduler`、`tzdata`）は `Pipfile` にあります。`py -3.14 -m pipenv install --dev` で入れます。
3. `.env` に次を追加します。認証情報やユーザーIDはGitへコミットしません。

```dotenv
LINE_CHANNEL_ACCESS_TOKEN=取得したアクセストークン
LINE_CHANNEL_SECRET=取得したチャネルシークレット
LINE_TARGET_USER_ID=送信先のユーザーID
DAILY_NOTIFICATION_TIME=18:00
# 通知から案内する回答画面のURLの元。未設定なら WEBAUTHN_ORIGIN（http://localhost:5000）
APP_BASE_URL=https://公開しているアプリのURL
```

自分宛てのテストでは、LINE Developersコンソールの対象チャネル → チャネル基本設定 → あなたのユーザーIDを使います。Uから始まる33文字で、友だち検索用のLINE IDではありません。テストする本人が公式アカウントを友だち追加してください。

`LINE_CHANNEL_ACCESS_TOKEN` と `LINE_CHANNEL_SECRET` が未設定のとき、`/line/callback` は503を返します。アプリのほかの画面は、LINEの設定がなくても動きます。

## Webhook（受信）

受信口は本体のアプリの `POST /line/callback` です。アプリを起動したまま（`flask --app run run`）、公開経路を用意します。ngrokなら `ngrok http 5000` を実行し、表示されたHTTPS URLに `/line/callback` を付けて、LINE DevelopersのWebhook URLに設定します。

- 署名（`X-Line-Signature`）がない・違う要求は400で断ります。
- 利用者がLINEで「質問」と送ると、通知と同じ質問を返します。それ以外のテキストは、おうむ返しします。

## すぐに通知を試す

```powershell
py -3.14 -m pipenv run python -m flask --app run line-send
```

質問を1回送って終了します。送信自体はWebhookなしでも行えますが、ボタン操作への返信には、アプリの起動と公開されたWebhookが必要です。

## 定期通知

```powershell
py -3.14 -m pipenv run python -m flask --app run line-schedule
```

Webサーバーとは別のターミナルで、1つだけ起動します。毎日 `DAILY_NOTIFICATION_TIME`（日本時間）に質問を送ります。止めるときは Ctrl+C です。複数起動すると通知が重複します。`flask run` や外部WSGIサーバーの起動では、定期通知は始まりません。

このコマンドが動いている間だけ有効です。停止中に過ぎた時刻の通知を、再起動後にまとめて送る機能はありません。

## 動作確認

- 手動通知：`line-send` で、質問と「アプリで回答する」・3択のボタンが届く。「アプリで回答する」で回答画面が開く。
- 定期通知：数分後の時刻を設定して `line-schedule` を起動し、指定時刻に1回届く。
- 選択回答：各ボタンを押し、対応する返事と「保存していません」・回答画面のURLが届く。
- テキスト受信：「接続テスト」を送り、同じ文章が返る。
- LINE公式アカウントの自動応答設定による返信の重複に注意する。

送信APIの受付成功だけでは実際の到達は確認できません。必ずスマートフォンで確認してください。プッシュ送信はプランのメッセージ通数・上限の対象です。通信エラー時の自動再送はありません。

外部へ送信しないテストは `tests/test_line.py` です（`py -3.14 -m pipenv run python -m pytest tests/test_line.py`）。

## 統合で変えたこと

- `notice/notice.py`（別アプリ、5001番ポート）を `app/services/line.py` と `app/blueprints/line.py` に移した。Webhookは `/callback` から `/line/callback` に変わった。
- `--send-now` は `flask line-send`、通常起動の定期通知は `flask line-schedule` に分けた。`ENABLE_SCHEDULE` は使わない（定期通知が要らないときは `line-schedule` を起動しない）。
- 通知に「アプリで回答する」ボタンと回答画面のURLを加え、選択への返事で保存していないことを伝えるようにした。
