# 家族共有の例外的な操作の E2E

`family_edge.js` は、実際のブラウザ（Chromium、Playwright）と開発サーバーで、家族共有の例外的な操作を試します。パスキーは Chromium の仮想認証器（CDP の WebAuthn 機能、PRF 付き）を使うので、登録・鍵の解除・暗号化・復号は本物の画面とコードを通ります。

確かめる場面は次の5つです（30件）。

1. 確認後の相手・期限・鍵の変更
2. 期限切れ（2分後に切れる共有を作り、実際に待つ）
3. 鍵更新後の再共有
4. 通信が遅いときの閲覧終了（家族共有の詳細、QR 閲覧）
5. 招待リンク・確認リンクの切り替え

ブラウザと開発サーバーが必要で、2分ほど待つため、`pytest` には含めていません。

## 実行のしかた（Linux / macOS）

```sh
# 1. 専用のDBを作る（開発用DBとは分ける）
export DATABASE_URL=sqlite:////tmp/family-edge.db
rm -f /tmp/family-edge.db
pipenv run flask --app run init-db
pipenv run flask --app run seed-answer-demo

# 2. 回数制限を外した設定で起動する（別のターミナル）
PYTHONPATH=. pipenv run flask --app tests/e2e/e2e_app.py run --port 5000

# 3. 実行する（Playwright と Chromium が必要）
node tests/e2e/family_edge.js /tmp/family-edge-out
```

Playwright をプロジェクトに入れていない場合は、`npm install --no-save playwright && npx playwright install chromium` で用意してください。全体（グローバル）に入れてある場合は、`NODE_PATH=$(npm root -g) node tests/e2e/family_edge.js ...` のように読み込み先を指定します。

結果は `OK` / `NG` の行で表示され、`results.json` と画面の記録（PNG）が出力先に残ります。NG があると終了コードが1になります。

- URL は `http://localhost:5000` のままにしてください（WebAuthn の RP ID が `localhost` のため）。別の URL で動かすときは `E2E_BASE_URL` を設定します。
- テストのブラウザはすべて同じ接続元になるため、`e2e_app.py` で回数制限を外しています。回数制限そのものは `tests/test_abuse_limits.py` などで確かめています。
- 受信用鍵を更新する画面はないため、鍵の更新は画面と同じ暗号関数で `POST /api/v1/encryption-keys` を呼んで行います。
