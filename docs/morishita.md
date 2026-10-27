# パスキー、家族共有、QR閲覧、端末移行

この文書は、今牧（回答入力）、坂（QRの画像）、小橋（LINE）、沖（ノートと履歴）が、森下担当のAPIとブラウザ暗号を呼ぶときの契約です。サーバーは回答の平文、パスキーの秘密鍵、PRFの出力、HPKEの秘密鍵、完全なQR URLを受け取りません。

画面は次のとおりです。

| 画面 | 役割 |
| --- | --- |
| `/auth` | ヘッダーなしのログイン・新規登録。新規登録には画面上の注意事項への同意が必要 |
| `/account` | ヘッダー付きのアカウント管理。鍵の解除、パスキーの追加・無効化、ログアウト |
| `/answers/demo` | ダミー質問6問による暗号化保存・再読み込み・共有テスト |
| `/sharing` | 共有する内容を確認してリンクを作成・停止する。QR画像は未実装 |
| `/family` | 家族一覧、リンク・QR招待、承認、共有、登録解除。送信・受信内容はモーダルで表示 |
| `/family/invite` | ログイン中の名前を確認して招待を承諾する専用画面 |
| `/view/<共有ID>` | QRを読み取った人の閲覧。画像は出さない |
| `/transfer` | 端末移行 |

本実装の `/answers` は未実装のままです。保存・共有テストは `/answers/demo` から利用できます。画面のURLへPOSTすると405で、回答の保存や共有の作成には既存の暗号化APIを使います。

共通ヘッダーのログインリンクは `/auth?next=<元ページのパスとクエリ>` を使います。ログイン・新規登録後はそのページへ戻り、`next` が未指定または不正ならホームへ戻ります。外部URLと `/auth` 自身への戻り先は受け付けません。招待トークンなどの元ページのfragmentは、ログインURLのfragmentでブラウザ内だけに引き継ぎ、`next` やサーバーへのリクエストへ混ぜません。ログインページでこのfragmentを取り出した後はアドレスバーから消します。ページ移動後の鍵は未解除なので、共有等の操作には移動先で通常どおり解錠してください。

登録前の同意はフロント側だけで判定し、サーバーの同意記録やAPI契約は追加していません。暗号化対象は回答として記録する個人情報・回答本文です。公開用の表示名は暗号化されず、ログイン・共有管理の情報もサーバーで管理します。「すべての個人情報が暗号化される」「元の端末以外では復号できない」とは表示しません。同期パスキーを使える別端末や共有先でも、それぞれの権限・鍵で閲覧できる場合があります。すべての利用可能なパスキーを失ったとき、運営が本人の記録を復元する機能はありません。

## 他の担当が使うJavaScript

`app/static/js/watashi/client.mjs` を同一オリジンの module として読みます。暗号の実体は `crypto.mjs`、HPKEは `app/static/vendor/hpke-js-1.8.0.esm.mjs`（中身は hpke-js 1.8.0）です。CDNへは接続しません。

主な関数:

- `registerAccount(displayName)` / `login()` / `unlock()` / `logout()` / `addPasskey()`
- `loadAnswerDocument()` は `{ revision, document }` を返す。文書が無いときは `{ revision: 0, document: null }`
- `saveAnswerDocument(items, expectedRevision)` は、編集を始めた `revision` を第2引数に取る。省略すると保存しない
- `createFamilyShare({ connectionId, recipientUserId, recipientKeyId, selection, expiresAt })`
- `updateFamilyShare({ shareId, expectedVersion, recipientUserId, recipientKeyId, selection })`
- `openReceivedShare(share)` / `openOwnerCopy(share)`
- `openOwnedQrShare(share)` は、発行者用の暗号化された控えで、リンクに含めた回答を復号する。現在の回答で代用しない
- `createQrShare({ mode, expiresAt, selection, payload = null })` は `{ share, url, privateKey, startToken }` を返す。`payload` を渡すと確認済みの内容をそのまま暗号化する。`url` の秘密は fragment だけに入る
- `updateQrShare({ shareId, expectedVersion, publicKey, selection })`
- `confirmMigrationAccess(migrationId)` は、新しいパスキーで解錠してから既存データと受信共有を復号し、成功したときだけ確認APIを呼ぶ
- `getUnlockState()` はログインと、このページのメモリ上で鍵が解けているかを分ける
- `detectPrf()` が `supported: false` のとき、登録を始めない

`items` の形は `{ 質問ID: { state, value } }` です。保存文書の `state` は `answered`、`undecided`、`declined` です。オブジェクトに無いIDは未回答で、`undecided`（未定）や `declined`（回答しない）とは別です。

共有範囲 `selection` は次のどちらかです。

- `{ scope: "all" }`。質問IDは付けません
- `{ scope: "chapters", ids: [章ID, ...] }` または `{ scope: "questions", ids: [質問ID, ...] }`

未指定、空配列、存在しないIDはエラーです。全体共有へは読み替えません。選択した質問だけを暗号文に入れます。保存文書に無い選択済みの質問は、共有平文では `{ state: "unanswered" }` です。選択していない質問は入れません。配列の値はそのまま残します。

鍵はページごとのメモリにだけあります。別ページへ移ると消えます。`localStorage`、Cookie、URL、サーバーへは置きません。暗号操作の前に、そのページで `login()` と `unlock()` を呼びます。再認証は15分です。切れたら選択内容を残したまま、同じページで `unlock()` をやり直します。

変更するAPIは、Cookieの `wn_csrf` を `X-CSRF-Token` に載せてください。Cookieは `/auth` や `/api/` を開いた応答で付きます。`HttpOnly` ではありません。セッションCookie `wn_session` は `HttpOnly` です。登録とログインの options、QRの start、移行の bind だけCSRFを免除しています。

## 暗号契約 v1

方式は AES-256-GCM（nonce 12バイト、暗号文の末尾に16バイトのタグ）と HKDF-SHA256 です。HPKEは `DHKEM(X25519, HKDF-SHA256)` / `HKDF-SHA256` / `AES-128-GCM` です。AADとinfoはUTF-8で、区切りは `|` です。

| 用途 | info | AAD |
| --- | --- | --- |
| 回答 | `acp/document/v1\|{data_key_id}` | `acp/document/v1\|{user_id}\|{data_key_id}\|{revision}\|{schema}` |
| パスキーで包む保管用鍵 | `acp/passkey-wrap/v1` | `acp/passkey-wrap/v1\|{user_handle}\|{credential_id}\|{data_key_id}` |
| 鍵束 | `acp/key-bundle/v1\|{data_key_id}` | `acp/key-bundle/v1\|{data_key_id}\|{revision}` |
| 共有の所有者用控え | `acp/share-owner-copy/v1\|{share_id}\|{version}` | 同じ文字列 |
| QRの発行者用控え | `acp/qr-owner-secret/v1\|{data_key_id}\|{share_id}` | 同じ文字列 |
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
6. `POST /api/v1/auth/passkeys/options` と `passkeys/verify` でパスキーを追加します。その後、同じパスキーで `POST /api/v1/auth/passkeys/<id>/proof/options` と `proof/verify` を行い、今の保管用鍵を再び開けることを確認します。`proof/verify` が成功するまで `proven_at` は空です。そのパスキーでの通常ログインは403 `passkey_unproven` で、セッションは発行しません。有効なパスキーの件数にも入れないので、確認待ちが残っていても、確認済みが1件なら無効化は409 `last_passkey` です。確認に失敗した追加は無効化します。古いパスキーは残ります。初回登録と移行先の登録は、その手続きの中で保管用鍵を包むので確認済みです。
7. `POST /api/v1/auth/passkeys/<id>/disable` です。確認済みの最後の1件は `{ "confirm_last": true }` が無いと409 `last_passkey` です。確認待ちのパスキーは、この件数に入りません。無効化しても、同期パスキーのコピーや相手が保存した共有は消えません。
8. `GET /api/v1/auth/session` は未ログインでも200で `{ "authenticated": false }` です。

署名カウンターが0の同期パスキーは、0のままもう一度成功します。0だからといって拒否しません。カウンターが増える認証器では、増えていない応答を拒否します。

受信用の公開鍵はパスキーの認証用公開鍵とは別です。`GET /api/v1/encryption-keys/<user_id>` は、本人にはすべての鍵、接続中の相手には有効な鍵だけを返します。それ以外は404です。置き換えるときは `POST /api/v1/encryption-keys` に `public_key`、`replaces`、`acknowledge_key_change: true` が必要です。確認が無い置換は保存しません。その鍵宛ての `active` な共有は `key_pending` になります。停止済みの共有は `key_pending` にも `active` にも戻りません。

`key_pending` のあいだ、受信者の一覧と個別取得は暗号文も所有者用コピーも返しません。一覧では状態だけを返すことがあります。期限切れ、停止、切断は404のままです。鍵を更新しても、すでに発行されたログインセッションは残りますが、そのセッションから旧暗号文は取得できません。所有者が現在の受信用鍵で `POST /api/v1/family/shares/<id>/update` し、新しい暗号文の保存が成功したときだけ `active` に戻ります。状態だけを戻す操作はありません。すでに受け取った暗号文をサーバーから消すことはできません。

## 回答の保存（今牧）

`GET /api/v1/answers/contract` と `GET /api/v1/answers/questions` を読めます。質問文は作りません。`is_active` の質問だけがIDつきで返ります。

`GET /api/v1/answers/current` は自分の最新版だけです。無ければ `{ "revision": 0, "document": null }` です。

`PUT /api/v1/answers/current` の本体は `expected_revision`、`data_key_id`、`nonce`、`ciphertext`、`crypto_version`、`schema_version` だけです。`expected_revision` が現在の版と違うとき、または同時INSERTで版が衝突したときは409 `revision_conflict` です。最新版は上書きしません。`expected_revision` が無いときは400 `invalid_revision` です。平文の項目名を足すと400になり、その値は応答と監査ログへ書きません。

今牧の画面は、読み込んだ `revision` を編集中に保持し、保存時へそのまま渡します。保存直前に最新版を取り直して古い内容へ付け替えないでください。409のときは入力を捨てず、再読込と比較を案内します。

```javascript
import { loadAnswerDocument, saveAnswerDocument, unlock } from "/static/js/watashi/client.mjs";

await unlock();
const opened = await loadAnswerDocument();
const editing = opened.revision;
const items = opened.document ? opened.document.items : {};
// ここで items を画面の状態として編集する。
try {
  await saveAnswerDocument(items, editing);
} catch (error) {
  if (error.status === 409) {
    // items は残す。最新版を読み直して比べる案内を出す。自動では保存しない。
  }
}
```

沖のノート・履歴画面は、この最新版と、家族共有の `GET /api/v1/family/shares/<id>/history`（所有者だけ）を使います。履歴は所有者用の暗号文です。受信者には返しません。`note/` の画面はこの契約の接続先であり、森下側では変更しません。

## 家族共有

メール送信機能は接続していません。通常は `POST /api/v1/family/invitations` の `{ "kind": "open" }` を使います。`{ "kind": "email", "email": "..." }` も受け付けますが、検証済みかどうかにかかわらず、形式が正しければ同じ200応答です。未登録のアドレスでは接続を作りません。招待一覧にもメールアドレスや登録有無は出しません。

照合値は `HMAC-SHA256(CONTACT_HASH_KEY, SHA-256(正規化メール))` です。正規化は前後の空白を除き、小文字にします。保存版は `address_hash_version = 2` です。`CONTACT_HASH_KEY` は32バイト以上で、環境変数から読みます。リポジトリやDBには置きません。未設定や短すぎる値では計算せず、メール招待は503 `contact_hash_unconfigured` です。単純なSHA-256には戻しません。秘密を変えると照合結果も変わるため、変えたあとの既存行はそのままでは一致しません。

既存のSHA-256行は、`init-db` の手順 `20261021_contact_hash_hmac` が `HMAC-SHA256(CONTACT_HASH_KEY, 保存済みSHA-256)` に置き換え、版を2にします。元のメールが無くても、新しい入力と同じ値になります。置き換え後に古いSHA-256は残しません。秘密が未設定で旧値が残っているときは変換せず失敗します。手順の再実行は、版2の行を変えません。

応答の `token` は一度だけ返します。リンクは `/family/invite#token=<token>` です。トークンをクエリに付けないでください。ページを開いただけでは招待は消費しません。受信者が `POST /api/v1/family/invitations/request` を送り、招待者が `POST /api/v1/family/invitations/<id>/confirm` するまで接続されません。第三者は同じトークンを使えません。

共有の作成は `POST /api/v1/family/shares` です。`confirmed` が `true` でないと保存しません。`id` はクライアントが作ったUUIDで、暗号のAADに入れます。`recipient_key_id` が相手の現在の有効な鍵と違うときは409 `key_changed` と `current_key_id` を返し、新しい版は作りません。

更新は `POST /api/v1/family/shares/<id>/update` に `expected_version` を付けます。回答を保存しても共有の版は変わりません。停止は `POST .../stop`、期限変更は `POST /api/v1/family/shares/<id>/expiry` に `{ "expires_at": "2026-10-16T00:00:00Z" }` または期限を消す `null` です。受信者の次の取得から反映されます。受信者はこのAPIを呼べません。接続解除は `POST /api/v1/family/connections/<id>/disconnect` です。送信者・受信者のどちらからでも解除でき、その接続の共有は受信者から見えなくなります。相手がすでに復号して保存した内容は消せません。再接続しても、停止した共有は戻りません。新しい共有を作ってください。

受信者は `GET /api/v1/family/received` と `GET /api/v1/family/received/<id>` で、共有が `active` で、接続が有効、期限内、受信用鍵が現在の鍵であるときだけ暗号文を受け取ります。`key_pending` は状態だけで、暗号文はありません。期限切れ、停止、切断のあとは404です。再接続や鍵更新では、止めた共有を配信可能な状態へ戻しません。`/family` では、復号した質問名と回答値を表示します。`answered` は値、`undecided` は「未定」、`declined` は「回答しない」、`unanswered` は「未回答」です。描画は `textContent` で行い、回答中のHTMLはスクリプトとして実行しません。

作成と更新のどちらも、先に「共有する内容を確認」で出した項目、相手、接続、受信用鍵ID、期限と、確定時のそれらが一致しているときだけ暗号化します。相手や期限や鍵を変えた場合も、チェックを外し、表示を消して、もう一度確認するまで保存しません。更新は、確認した相手と接続がその共有と同じときだけ行います。送信する鍵IDは、共有に残っている旧IDではなく、確認した現在の受信用鍵IDです。相手の鍵が変わったあとは、その新しい鍵を確認し直してから更新します。確認済みの `payload` を `createFamilyShare` と `updateFamilyShare` に渡すと、そのバイト列をそのまま暗号文にします。渡したあとに回答を読み直して別の内容へ付け替えません。`payload` を省いた呼び出しは、その時点の `selection` だけを暗号化し、未指定を全体共有にはしません。

## QR（坂）

坂担当は画像と、表示用のURL組み立てだけを足します。秘密鍵と開始トークンはサーバーへ送らず、URLのfragmentに置きます。

1. 32バイトのHPKE鍵対と、32バイトの開始トークンを端末で作ります。
2. 共有IDは32バイトのbase64url（43文字）です。AADに入れるため、作成前に決めます。
3. `POST /api/v1/qr-shares` に `id`、`mode`（`emergency`、`timed`、`unlimited`）、`public_key`、`start_token_hash`（SHA-256）、`version`（`hpke_enc` と `ciphertext`）、期限付きなら `expires_at` を送ります。`unlimited` は期限なし・複数回で、停止操作は共通です。`private_key` や完全なURLは項目として拒否します。発行者向けの `owner_secret` は暗号文のみを保存し、所有者の一覧・詳細APIだけに返します。閲覧APIには返しません。
4. 表示するURLは `createShareUrl` の形です。`/view/<id>#k=<秘密鍵>&t=<開始トークン>`。
5. 坂の表示UIは、`createQrShare` が返した `url` だけを画像にします。`privateKey` と `startToken` はサーバーへ送らず、画像の元データにも残しません。`selection` を省くと全体共有にはならず、関数が失敗します。共有前に `plaintextForSelection` で対象だけを確認できます。
6. 閲覧ページはfragmentを `sessionStorage` に移し、アドレスバーからは消します。初回表示では開始しません。利用者が「内容を開く」を押したときだけ `POST /api/v1/qr-shares/<id>/start` に `start_token` と `attempt_id` を送ります。秘密鍵は送りません。
7. 成功すると `wn_qr` Cookieが付きます。これはアカウントのセッションではありません。`/api/v1/answers/current` には使えません。
8. 同じ共有の有効なセッションがある再読込は `GET /api/v1/qr-view/current` だけで復号します。通常の再表示では新しい start を呼びません。別の共有のセッションでは復号しません。閲覧画面は共有内容、`updated_at`（共有の更新日時）、有効状態（有効・停止・期限切れ・使用済み）、終了操作を分けて出します。使用済み、期限切れ、停止、閲覧期限切れ、復号失敗は別の文言です。復号に失敗したときは内容を出しません。
9. 緊急用は最初の成功したattemptだけがセッションを得ます。同じattemptの再送は、無操作期限と絶対期限の内側なら同じセッションです。別のattemptは409です。期限付きは、QR自体の期限内なら複数のセッションを作れます。
10. 閲覧の絶対期限は30分で、再取得しても再試行しても延びません。5分操作が無いとアイドル期限で切れます。切れたセッションは同じattemptでは復活しません。緊急用は消費済みのままなので、その後の新しいattemptも拒否します。期限付きは、QR自体が有効なら新しいattemptで別セッションを始められます。
11. 更新は `POST /api/v1/qr-shares/<id>/update` です。緊急用を消費したあとも、同じ鍵対で新しい版を復号できます。消費時刻は戻りません。更新時の `selection` が狭ければ、新しい暗号文からも非対象は消えます。
12. 停止は `POST /api/v1/qr-shares/<id>/stop`、期限付きの期限変更は `POST /api/v1/qr-shares/<id>/expiry` です。発行済みセッションの次の取得にも反映されます。相手が保存済みの内容は消えません。

## 端末移行

古いパスキーは、次が全部終わるまで無効化しません。

1. 今の端末が `POST /api/v1/transfer/start`。再認証が必要です。
2. 新しい端末が一時的なHPKE鍵を作り、`POST /api/v1/transfer/bind` に `migration_id` と `target_public_key` を送ります。応答の `transfer_token` を `X-Transfer-Token` に付けます。公開鍵を別のものへ取り替えると409です。
3. 両方の画面に同じ照合コードと指紋が出ます。一致しなければ承認しません。
4. 今の端末が `POST /api/v1/transfer/<id>/approve` に、手入力したコードと表示中の指紋を送ります。
5. 解除済みの保管用鍵を、移行先の公開鍵でHPKEして `POST /api/v1/transfer/<id>/bundle` します。
6. 新しい端末が bundle を復号し、`POST /api/v1/transfer/passkey/options` と `passkey/verify` でパスキーを登録します。登録とログインだけでは完了しません。
7. その新しいパスキーでログインし、同じページで再認証します。`confirmMigrationAccess` が保管用鍵で既存の回答と、有効な受信共有を復号します。回答や共有が無いアカウントは空として扱いますが、保管用鍵を開ける確認は省略しません。平文はサーバーへ送りません。成功したときだけ `POST /api/v1/transfer/<id>/decrypt-confirmed` を呼びます。
8. 再認証済みで、復号確認済みの新しいパスキーのセッションが `POST /api/v1/transfer/<id>/finalize` に `{ "confirmed": true }` を送ったときだけ、古いパスキーと、それで発行されたセッションを無効化します。サーバーは平文を読めないので、復号確認の申告内容を独立には検証しません。保証するのは、移行ID、アカウント、新旧のパスキー、新しいパスキーのセッション、再認証、旧端末の承認、期限、一回性です。
9. 鍵束が壊れている、解錠できない、受信共有を復号できない、確認を押していない、期限切れ、別アカウント、別のパスキーでは、古いパスキーは残ります。`POST /api/v1/transfer/<id>/cancel` では新しいパスキーだけを無効化し、古いパスキーは残します。全パスキーを失ったときの運営復旧はありません。

## エラーと制限

変更系の失敗は `{ "error", "message" }` です。メッセージに秘密や入力した平文は入れません。試行回数を超えると429 `rate_limited` で、アカウントやセッションは作りません。暗号文は256KiBまで、共有の版は30までです。

監査ログには操作名、対象のID、成功または失敗、エラーコードだけを残します。PRF出力、平文、トークン、完全なURLは残しません。

## テスト

Pythonのテストは `python -m pytest` です。中で Node.js が `tests/browser_crypto.mjs`、`tests/page_flows.mjs`、`tests/family_rekey_page.mjs` を実行します。最初のものはブラウザ暗号とPythonのHPKEが同じ暗号文を開けることを見ます。次のものは、ページを開いただけではQRを開始しないこと、選択した項目だけが暗号文に入ること、確認した項目・相手・期限・鍵と違う場合は共有を確定しないこと、確認済みのバイト列を読み直して付け替えないこと、閲覧状態と復号失敗を分けて示すこと、古いrevisionを拒否すること、解錠失敗では移行を完了しないこと、壊れた共有では確認APIを呼ばないことを見ます。最後のものは、家族画面で内容を確認してから、同じ相手の共有を現在の新しい鍵IDで更新することを見ます。

SQLiteでチャレンジ表を作り直す手順は、外部キーを切るときも戻すときも、同じ接続でトランザクションの外に `PRAGMA foreign_keys` を実行します。戻したあとは `foreign_keys=1` であることを読み、違う場合は失敗させます。既存のパスキーへ `proven_at` を足す手順は、すでにある行を `created_at` で確認済みにします。

模擬パスキーは `tests/soft_passkey.py` です。実機のパスキー、PRF、Android、iOS、Windowsの認証器はこの環境では未確認です。未確認だからといって、平文やPRF出力をサーバーへ送る代替処理は入れていません。
