# パスキー、家族共有、QR閲覧、端末移行

この文書は、今牧（回答入力）、坂（QRの画像）、小橋（LINE）、大城（ノートと履歴）が、森下担当のAPIとブラウザ暗号を呼ぶときの契約です。サーバーは回答の平文、パスキーの秘密鍵、PRFの出力、HPKEの秘密鍵、完全なQR URLを受け取りません。

画面は次のとおりです。

| 画面 | 役割 |
| --- | --- |
| `/auth` | パスキーの登録、ログイン、鍵の解除、追加、無効化 |
| `/family` と `/family/invite` | 招待、承認、共有、切断 |
| `/view/<共有ID>` | QRを読み取った人の閲覧。画像は出さない |
| `/transfer` | 端末移行 |

`/answers` と `/sharing` は未実装のままです。POSTすると 405 です。

## 他の担当が使うJavaScript

`app/static/js/watashi/client.mjs` を同一オリジンの module として読みます。暗号の実体は `crypto.mjs`、HPKEは `app/static/vendor/hpke-js-1.8.0.esm.mjs`（中身は hpke-js 1.8.0）です。CDNへは接続しません。

主な関数:

- `registerAccount(displayName)` / `login()` / `unlock()` / `logout()` / `addPasskey()`
- `saveAnswerDocument(items)` / `loadAnswerDocument()`
- `createFamilyShare({ connectionId, recipientUserId, recipientKeyId, selectedIds, expiresAt })`
- `openReceivedShare(share)` / `openOwnerCopy(share)`
- `createQrShare({ mode, expiresAt })` は `{ share, url }` を返す。`url` の秘密は fragment だけに入る
- `detectPrf()` が `supported: false` のとき、登録を始めない

`items` の形は `{ 質問ID: { state, value } }` です。`state` は `answered`、`undecided`、`declined` です。オブジェクトに無いIDは未回答で、`undecided`（未定）や `declined`（回答しない）とは別です。選択していない項目は共有ペイロードに入れません。

変更するAPIは、Cookieの `wn_csrf` を `X-CSRF-Token` に載せてください。Cookieは `/auth` や `/api/` を開いた応答で付きます。`HttpOnly` ではありません。セッションCookie `wn_session` は `HttpOnly` です。登録とログインの options、QRの start、移行の bind だけCSRFを免除しています。

## 暗号契約 v1

方式は AES-256-GCM（nonce 12バイト、暗号文の末尾に16バイトのタグ）と HKDF-SHA256 です。HPKEは `DHKEM(X25519, HKDF-SHA256)` / `HKDF-SHA256` / `AES-128-GCM` です。AADとinfoはUTF-8で、区切りは `|` です。

| 用途 | info | AAD |
| --- | --- | --- |
| 回答 | `acp/document/v1\|{data_key_id}` | `acp/document/v1\|{user_id}\|{data_key_id}\|{revision}\|{schema}` |
| パスキーで包む保管用鍵 | `acp/passkey-wrap/v1` | `acp/passkey-wrap/v1\|{user_handle}\|{credential_id}\|{data_key_id}` |
| 鍵束 | `acp/key-bundle/v1\|{data_key_id}` | `acp/key-bundle/v1\|{data_key_id}\|{revision}` |
| 共有の所有者用控え | `acp/share-owner-copy/v1\|{share_id}\|{version}` | 同じ文字列 |
| HPKE | AADと同じバイト列を info にも使う | `hpke-v1\|{purpose}\|{context_id}\|{owner_id}\|{recipient_id}\|{key_id}\|{version}` |

HKDFのsaltは、パスキーの包みでは封筒の `kdf_salt`、それ以外のAESでは32バイトのゼロです。PRFの評価入力はパスキーごとにサーバーが作った32バイトで、出力そのものは送りません。`clientExtensionResults` を含む認証応答は拒否します。

家族共有の `purpose` は `family`、QRは `qr`、移行は `migration` です。QRの `recipient_id` は `qr`、`key_id` は公開鍵のbase64urlです。移行の `recipient_id` は `target`、`key_id` は移行先公開鍵のbase64urlです。

バイト列のJSON表現は、パディング無しのbase64urlです。日時はUTCの `Z` 付きです。

## 認証

すべてのAPIの所有者はセッションから決めます。リクエストの `owner_user_id` は受け付けません。

1. `POST /api/v1/auth/register/options` に `{ "display_name" }`。応答の `public_key` を `navigator.credentials.create` に渡します。`challenge` と `user.id` はbase64urlなので、ブラウザへ渡す前にバイトへ戻します。
2. PRFの `results.first` が無い場合は verify を呼びません。
3. `POST /api/v1/auth/register/verify` に `credential`、`envelope`、`encryption_public_key`、`key_bundle`。封筒は `prf_input`、`kdf_salt`、`wrapped_key`（48バイト）、`nonce`、`kdf_version: 1`、`crypto_version: 1` です。
4. ログインは `POST /api/v1/auth/login/options` と `login/verify` です。ログインだけでは保管用鍵は解けません。
5. `POST /api/v1/auth/unlock/options` と `unlock/verify` で、同じパスキーのPRFから解きます。共有や移行の開始には、この再認証が15分必要です。
6. `POST /api/v1/auth/passkeys/options` と `passkeys/verify` でパスキーを追加します。古いパスキーは残ります。追加する側は、今の保管用鍵を新しいPRF出力で包みます。
7. `POST /api/v1/auth/passkeys/<id>/disable` です。最後の1件は `{ "confirm_last": true }` が無いと409 `last_passkey` です。無効化しても、同期パスキーのコピーや相手が保存した共有は消えません。
8. `GET /api/v1/auth/session` は未ログインでも200で `{ "authenticated": false }` です。

署名カウンターが0の同期パスキーは、0のままもう一度成功します。0だからといって拒否しません。カウンターが増える認証器では、増えていない応答を拒否します。

受信用の公開鍵はパスキーの認証用公開鍵とは別です。`GET /api/v1/encryption-keys/<user_id>` は、本人にはすべての鍵、接続中の相手には有効な鍵だけを返します。それ以外は404です。置き換えるときは `POST /api/v1/encryption-keys` に `public_key`、`replaces`、`acknowledge_key_change: true` が必要です。確認が無い置換は保存しません。その鍵宛ての有効な共有は `key_pending` になります。

## 回答の保存（今牧）

`GET /api/v1/answers/contract` と `GET /api/v1/answers/questions` を読めます。質問文は作りません。`is_active` の質問だけがIDつきで返ります。

`GET /api/v1/answers/current` は自分の最新版だけです。無ければ `{ "revision": 0, "document": null }` です。

`PUT /api/v1/answers/current` の本体は `expected_revision`、`data_key_id`、`nonce`、`ciphertext`、`crypto_version`、`schema_version` だけです。`expected_revision` が現在の版と違うときは409 `revision_conflict` で、最新版は上書きしません。平文の項目名を足すと400になり、その値は応答と監査ログへ書きません。

大城の履歴画面は、この最新版と、家族共有の `GET /api/v1/family/shares/<id>/history`（所有者だけ）を使います。履歴は所有者用の暗号文です。受信者には返しません。

## 家族共有

メール送信機能は接続していません。宛先メールの招待は、`verified_contacts` に検証済みのハッシュがある相手だけ作れます。無いときは409で、入力したメールアドレスは応答に出しません。通常は `POST /api/v1/family/invitations` の `{ "kind": "open" }` を使います。

応答の `token` は一度だけ返します。リンクは `/family/invite#token=<token>` です。トークンをクエリに付けないでください。ページを開いただけでは招待は消費しません。受信者が `POST /api/v1/family/invitations/request` を送り、招待者が `POST /api/v1/family/invitations/<id>/confirm` するまで接続されません。第三者は同じトークンを使えません。

共有の作成は `POST /api/v1/family/shares` です。`confirmed` が `true` でないと保存しません。`id` はクライアントが作ったUUIDで、暗号のAADに入れます。`recipient_key_id` が相手の現在の有効な鍵と違うときは409 `key_changed` と `current_key_id` を返し、新しい版は作りません。

更新は `POST /api/v1/family/shares/<id>/update` に `expected_version` を付けます。回答を保存しても共有の版は変わりません。停止は `POST .../stop`、接続解除は `POST /api/v1/family/connections/<id>/disconnect` です。解除すると、その接続の共有は受信者から見えなくなります。相手がすでに復号して保存した内容は消せません。再接続しても、停止した共有は戻りません。新しい共有を作ってください。

受信者は `GET /api/v1/family/received` と `GET /api/v1/family/received/<id>` で現在の版だけを受け取ります。期限切れ、停止、切断のあとは404です。

## QR（坂）

坂担当は画像と、表示用のURL組み立てだけを足します。秘密鍵と開始トークンはサーバーへ送らず、URLのfragmentに置きます。

1. 32バイトのHPKE鍵対と、32バイトの開始トークンを端末で作ります。
2. 共有IDは32バイトのbase64url（43文字）です。AADに入れるため、作成前に決めます。
3. `POST /api/v1/qr-shares` に `id`、`mode`（`emergency` または `timed`）、`public_key`、`start_token_hash`（SHA-256）、`version`（`hpke_enc` と `ciphertext`）、期限付きなら `expires_at` を送ります。`private_key` や完全なURLは項目として拒否します。
4. 表示するURLは `createShareUrl` の形です。`/view/<id>#k=<秘密鍵>&t=<開始トークン>`。
5. 閲覧ページはfragmentを `sessionStorage` に移し、アドレスバーからは消します。`POST /api/v1/qr-shares/<id>/start` には `start_token` と `attempt_id` だけを送ります。秘密鍵は送りません。
6. 成功すると `wn_qr` Cookieが付きます。これはアカウントのセッションではありません。`/api/v1/answers/current` には使えません。
7. `GET /api/v1/qr-view/current` で現在の暗号文を取り、端末で復号します。ページを開いただけ、またはGETしただけでは緊急用QRは消費しません。startが消費します。
8. 緊急用は最初の成功したattemptだけがセッションを得ます。同じattemptの再送は同じセッションです。別のattemptは409です。期限付きは、期限まで複数のセッションを作れます。
9. 閲覧の絶対期限は30分で、再取得しても延びません。5分操作が無いとアイドル期限で切れます。
10. 更新は `POST /api/v1/qr-shares/<id>/update` です。緊急用を消費したあとも、同じ鍵対で新しい版を復号できます。消費時刻は戻りません。
11. 停止は `POST /api/v1/qr-shares/<id>/stop` です。以降の取得は失敗します。相手が保存済みの内容は消えません。

## 端末移行

古いパスキーは、次が全部終わるまで無効化しません。

1. 今の端末が `POST /api/v1/transfer/start`。再認証が必要です。
2. 新しい端末が一時的なHPKE鍵を作り、`POST /api/v1/transfer/bind` に `migration_id` と `target_public_key` を送ります。応答の `transfer_token` を `X-Transfer-Token` に付けます。公開鍵を別のものへ取り替えると409です。
3. 両方の画面に同じ照合コードと指紋が出ます。一致しなければ承認しません。
4. 今の端末が `POST /api/v1/transfer/<id>/approve` に、手入力したコードと表示中の指紋を送ります。
5. 解除済みの保管用鍵を、移行先の公開鍵でHPKEして `POST /api/v1/transfer/<id>/bundle` します。
6. 新しい端末が bundle を復号し、`POST /api/v1/transfer/passkey/options` と `passkey/verify` でパスキーを登録します。
7. その新しいパスキーでログインしたセッションが `POST /api/v1/transfer/<id>/finalize` に `{ "confirmed": true }` を送ったときだけ、古いパスキーと古いセッションを無効化します。
8. `POST /api/v1/transfer/<id>/cancel` か期限切れでは、新しいパスキーだけを無効化し、古いパスキーは残します。完了フラグだけを送っても無効化しません。

## エラーと制限

変更系の失敗は `{ "error", "message" }` です。メッセージに秘密や入力した平文は入れません。試行回数を超えると429 `rate_limited` で、アカウントやセッションは作りません。暗号文は256KiBまで、共有の版は30までです。

監査ログには操作名、対象のID、成功または失敗、エラーコードだけを残します。PRF出力、平文、トークン、完全なURLは残しません。
