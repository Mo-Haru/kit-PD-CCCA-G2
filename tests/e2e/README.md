# 家族共有の例外的な操作の E2E

`family_edge.js` は、実際のブラウザ（Chromium、Playwright）と開発サーバーで、家族共有の例外的な操作を試します。パスキーは Chromium の仮想認証器（CDP の WebAuthn 機能、PRF 付き）を使うので、登録・鍵の解除・暗号化・復号は本物の画面とコードを通ります。

確かめる場面は次の5つです（34件）。共有は `/family/share` の手順（解除 → 範囲 → 家族と期限 → 再確認）、再共有は家族画面の共有の詳細の「内容を選び直して更新」から行います。

1. 確認後の相手・期限・鍵の変更
2. 期限切れ（2分後に切れる共有を作り、実際に待つ。期限切れの共有を更新できないことも確かめる）
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

# 統合した機能の E2E

`integration.js` は、統合した機能（回答の画面・わたしノート・QRでの共有・LINEの受信口）を通して試します（38件、1分半ほど）。

1. 回答の画面（`/answers`）：開くと自動で解除する、22問（2ページ）、用語の説明、1問1つの選択、途中保存、確認、開き直しでの復元、ブラウザとDBに平文が残らないこと
2. わたしノート（`/note`）：進み具合（%）、回答日、未回答の表示、「書き直す」で回答の画面のその質問を開く（自由記述も）。未ログインで質問のURLを開き、ログインしてもその質問に戻る
3. QRで共有：回答の画面の回答を `/sharing/new` で共有し、QRコードが出て、別のブラウザで読めること
4. LINEの受信口（`/line/callback`）：署名つきの確認用の要求は200、署名なし・違う署名は400（`e2e_app.py` の仮のチャネルシークレットを使う。LINEへは送信しない）
5. 回答の画面の例外：`#` 以降が壊れたURL、回答を変えたら確認の一覧を隠す、ログインが切れたあとに入力を残して本人確認をやり直し保存できる
6. メニューと端末移行：開いた直後にメニューが画面を覆わない、移行の途中でログインが切れても移行IDを残しログインと鍵の解除で続きを追える、新しい端末で移行を完了すると古い端末にも終了が出る、新しい端末で移行前の回答を読める

```sh
export DATABASE_URL=sqlite:////tmp/integration.db
rm -f /tmp/integration.db
pipenv run flask --app run init-db
pipenv run flask --app run seed-questions
PYTHONPATH=. pipenv run flask --app tests/e2e/e2e_app.py run --port 5000   # 別のターミナル
E2E_DB_FILE=/tmp/integration.db node tests/e2e/integration.js /tmp/integration-out
```

`E2E_DB_FILE` を付けると、DBファイルに自由記述の回答の平文がないことも確かめます。
