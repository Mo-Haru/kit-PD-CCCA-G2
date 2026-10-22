# kit-PD-CCCA-G2

本人の意思や回答を記録し、家族や医療関係者などへ共有するアプリの開発用リポジトリです。

この README だけで、リポジトリの取得、環境構築、初回起動、自分の担当機能を書き始める場所まで分かります。完成したアプリではなく、同じ手順で起動して機能を追加するための土台です。

## このアプリの目的

本人の意思や回答を残し、共有先へ渡せるようにします。今後入れる予定の機能は次のとおりです。

- 回答の入力・保存・閲覧
- パスキーによる認証
- ブラウザ側での暗号化・復号
- QRコードによる共有
- 本人による共有の有効・無効と期限の管理

運営サーバーには回答の内容を読ませない方針です。回答を更新したときに共有先へ自動反映する方式ではなく、本人が明示的に最新の意思を共有する方式を検討しています。

LINE API を使うかどうかは未決定です。

実在の個人情報や回答本文は、開発データに使わないでください。確認はダミーデータだけで行います。

## 確定している構成と暫定設定

### 確定

- 開発環境は Windows
- バックエンドは Python の Flask
- Python は 3.14（`Pipfile` の `python_version` と `.python-version`）
- 依存管理は Pipenv。宣言は `Pipfile` だけにする
- 直接使うライブラリのバージョンまたは許容範囲は `Pipfile` に指定する
- `Pipfile.lock` は Git に含めない。既存の `.gitignore` がロックファイルを無視していたため、その設定を維持する
- DB 操作は Flask-SQLAlchemy
- コード共有は GitHub（このリポジトリ）
- アプリ生成は `create_app` にまとめる
- `db` は `app/extensions.py` で定義し、`db.init_app` で初期化する
- 起動入口は `run.py`。アプリケーション本体は `app` パッケージ

ルートに `app.py` を置くと、`app` パッケージの import と衝突します。確認用の `Hello, World!` は `GET /hello` で返します。

### 暫定（本番の決定ではない）

| 項目 | 暫定の内容 | 判断理由 |
| --- | --- | --- |
| データベース | SQLite（`instance/dev.db`） | 既存の接続設定がなかった。追加の DB ソフトなしで、ローカルの起動確認ができる。 |
| 秘密情報 | 開発用のダミー `dev-change-me` | 外部サービスの鍵はまだ無い。鍵が無くてもこの土台は起動できる。この値は本番の秘密鍵ではない。 |
| 業務モデル | 永続化層のモデルを定義済み | 回答・共有内容は暗号文として保存する。パスキー、家族共有、QR閲覧、端末移行の処理は入っている。回答入力画面とQR画像は未実装。 |

直接使うライブラリは `Pipfile` で次のバージョンまたは許容範囲に指定しています。

- Flask 3.1.3
- Flask-SQLAlchemy 3.1.1
- SQLAlchemy `>=2.0,<2.1`
- python-dotenv 1.2.4
- webauthn 2.7.1（パスキー検証）
- Click 8.5.0（`init-db` コマンド）
- pytest 9.1.1（開発用）
- pyhpke 0.6.5（開発用。テストがブラウザの HPKE と相互に復号できることを確認する）
- cbor2 6.1.5（開発用。模擬パスキーの認証データ）
- cryptography 50.0.2（開発用。模擬パスキーの署名）
- pip-audit 2.10.1（開発用。依存関係の脆弱性確認）

インストール時に入る間接的な依存のバージョンは、`Pipfile.lock` に記録できます。そのファイルは既存の設定どおりリポジトリへは含めません。

## Windows で必要なソフト

| ソフト | バージョン | 用途 |
| --- | --- | --- |
| Git | 2 系 | リポジトリの取得、ブランチ、Pull Request |
| Python | 3.14 系 | アプリの実行。インストール時に Python launcher（`py`）を有効にする |
| Pipenv | Python 3.14 に入れる | 仮想環境と依存のインストール。コマンドは `py -3.14 -m pipenv` |
| Web ブラウザ | パスキーとPRFに対応したもの | 登録と鍵の解除。非対応の場合、画面は登録を完了しません |

Python は [python.org の 3.14 系](https://www.python.org/downloads/) を使ってください。確認済みのパッチ番号は、この README 末尾の「動作確認した環境」だけです。

次のコマンドでバージョンを確認します。

```powershell
git --version
py -0p
py -3.14 --version
```

`py` が無い場合は、Python のインストール時に launcher を外していないか確認し、代わりに `python --version` を見てください。3.14 系でない場合は、3.14 系を入れてから進めてください。

## 初回起動（PowerShell）

リポジトリのフォルダで実行します。仮想環境の `Activate.ps1` は使わなくて構いません。Pipenv は `py -3.14 -m pipenv` で実行します。

仮想環境はリポジトリ内の `.venv` に作ります。新しい PowerShell を開いたら、Pipenv を使う前に次を実行してください。この指定は、そのウィンドウの中だけで有効です。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
```

初回は、Pipenv 自体を Python 3.14 に入れてから依存を入れます。

```powershell
git clone https://github.com/Mo-Haru/kit-PD-CCCA-G2.git
cd kit-PD-CCCA-G2
py -3.14 -m pip install --user pipenv
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv install --dev --python 3.14
Copy-Item .env.example .env
py -3.14 -m pipenv run python -m flask --app run init-db
py -3.14 -m pipenv run python -m flask --app run run --debug --host localhost --port 5000
```

`py` が使えない場合は、`python --version` が 3.14 系であることを確認したうえで、`py -3.14` を `python` に置き換えます。

```powershell
python -m pip install --user pipenv
$env:PIPENV_VENV_IN_PROJECT = "1"
python -m pipenv install --dev --python 3.14
```

`.env` のコピーは省略できます。省略しても、開発用の暫定値（SQLite とダミーの `SECRET_KEY`）で起動します。設定を変えるときだけ `.env` を編集します。`.env.example` に秘密情報は入っていません。メール招待の照合に使う `CONTACT_HASH_KEY` には暫定値を置いていません。未設定のままではメール招待を作らず、単純なSHA-256へは落としません。

`init-db` は、未作成のテーブルを作り、その後 `app/schema_upgrade.py` の未適用手順だけを実行します。既存の行は削除しません。列の追加や、チャレンジ用途の許可値を広げる手順は、既存行をコピーして残します。このコマンドを二回実行しても、既存データは消えません。

同じウィンドウで仮想環境に入ってから短くすることもできます。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv shell
python -m flask --app run init-db
python -m flask --app run run --debug --host localhost --port 5000
```

`pipenv shell` が止まった場合は、実行ポリシーを変更せず、`py -3.14 -m pipenv run ...` の形を使ってください。

サーバーを止めるときは、そのウィンドウで Ctrl+C を押します。

起動したままテストする場合は、別の PowerShell を開いてリポジトリのフォルダへ移動し、次を実行します。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv run python -m pytest
```

pytest は Node.js も使います。`tests/browser_crypto.mjs` と `tests/page_flows.mjs` が、ブラウザ内と同じ暗号関数を実行します。Node.js が無いと、そのテストは失敗します。

## 起動できたかの確認

パスキーのRP IDは `localhost`、originは `http://localhost:5000` です。ブラウザで開くURLもこれにしてください。`http://127.0.0.1:5000/` は別のoriginなので、パスキー登録は失敗します。ポートを変えたときは `.env` の `WEBAUTHN_ORIGIN` も同じURLにします。

| URL | 正常なとき |
| --- | --- |
| http://localhost:5000/ | 「意思の記録」と、回答・共有・認証・家族・移行へのリンクが見える |
| http://localhost:5000/hello | 本文が `Hello, World!` |
| http://localhost:5000/answers | 回答入力は「未実装」。保存APIは別で、`docs/morishita.md` の契約を使う |
| http://localhost:5000/sharing | QR画像は「未実装」。閲覧開始APIは別 |
| http://localhost:5000/auth | パスキーの登録、ログイン、鍵の解除ができる |
| http://localhost:5000/family | この画面でログインし、鍵を解除してから家族共有を作れる |
| http://localhost:5000/transfer | この画面でログインし、鍵を解除してから端末移行を進められる |
| http://localhost:5000/health | JSON で `status` と `database` がどちらも `ok` |

`/hello` は、以前の単一ファイルのアプリが返していた確認用の応答です。HTML の画面ではなく、本文 `Hello, World!` をそのまま返します。

画面上部には、回答の入力画面とQRコードの画像表示が未実装であり、パスキー、家族共有、QRの閲覧、端末移行は使える旨が出ます。未実装のボタンは押せません。

ポート 5000 が使用中のときは、次でポートを変え、`.env` の `WEBAUTHN_ORIGIN` を `http://localhost:5001` にします。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv run python -m flask --app run run --debug --host localhost --port 5001
```

その場合の URL は `http://localhost:5001/` です。

ターミナルの日本語が文字化けするときは、そのウィンドウで `chcp 65001` を実行してから起動し直してください。

`/health` の `database` が `error` のときは、リポジトリのフォルダでコマンドを実行しているか、`.env` の `DATABASE_URL` が空や誤った値になっていないかを確認してください。既定の SQLite に戻すには、`DATABASE_URL` の行を削除するか、行頭に `#` を付けてコメントにします。

## フォルダと主要ファイル

```
app/
  __init__.py          create_app、Blueprint 登録、init-db、404
  extensions.py        Flask-SQLAlchemy の db
  blueprints/          機能ごとのルート
    main.py            ホーム、/hello、/health
    answers.py         回答
    sharing.py         共有
    auth.py            認証
  models/              DB モデル（認証、暗号化文書、共有、質問、通知等の19モデル）
  templates/           共通レイアウトと各画面
  static/css/          共通の CSS
config.py              開発用設定とテスト用設定
run.py                 起動用のアプリオブジェクト
Pipfile                直接使う依存のバージョンと許容範囲を指定
.python-version        3.14
.env.example           秘密情報を含まない設定例
tests/                 起動、ルート、DB 接続の確認
```

DB モデルは `app/models/` に機能別で配置し、`app/models/__init__.py` で全19モデルを読み込みます。`db` は `app.extensions` の共通インスタンスを使います。
`app/services/` はまだ作りません。ルートから呼び出す処理をファイルに分けたくなった時点で、例えば `app/services/answers.py` を追加します。最初から層を増やさないでください。

`instance/` は起動時に作られます。SQLite の `instance/dev.db` が入ります。`.venv/`、`.env`、`instance/`、ローカル DB、`Pipfile.lock` は Git に含めません。

## 画面、Blueprint、モデルの追加

コマンドは、リポジトリのフォルダで `$env:PIPENV_VENV_IN_PROJECT = "1"` を設定したあと、`py -3.14 -m pipenv run ...` を使う前提です。

### 画面と Blueprint

1. `app/blueprints/feature.py` を作り、GET だけの Blueprint を置く。URL はルートに書く。
2. `app/__init__.py` の `register_blueprints` で、その Blueprint を登録する。このファイルは共通ファイルです。
3. `app/templates/feature/index.html` を作り、`base.html` を継承する。
4. ナビゲーションが必要なら `app/templates/base.html` にリンクを足す。このファイルも共通ファイルです。
5. `tests/test_routes.py` に、その URL が 200 で開けることを足す。
6. まだ操作できない機能は、画面に「未実装」と書き、`fieldset disabled` のように実行できない状態にする。成功したように見せる仮実装は入れない。

`app/blueprints/feature.py` の例です。`strict_slashes=False` にすると、`/feature` と `/feature/` のどちらでも開けます。リンクの基準は末尾スラッシュなしです。

```python
from flask import Blueprint, render_template

bp = Blueprint("feature", __name__)


@bp.get("/feature", strict_slashes=False)
def index():
    return render_template("feature/index.html")
```

`app/__init__.py` の `register_blueprints` へ足す例です。

```python
from app.blueprints.feature import bp as feature_bp

app.register_blueprint(feature_bp)
```

### モデル

1. `app/models/feature.py` に、`app.extensions` の `db` を継承したモデルを書く。
2. `app/models/__init__.py` で、そのモデルを import する。import しないと `init-db` から見えません。
3. 次を実行する。既存データは削除されません。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv run python -m flask --app run init-db
```

回答・共有内容は暗号文（`ciphertext`）と `nonce` のみを保存し、平文の回答・復号鍵・PRF 出力は保存しません。暗号の契約は `app/crypto_contract.py` と `docs/morishita.md` にあります。

`init-db` は `db.create_all()` のあと、`app/schema_upgrade.py` の未適用手順だけを実行します。既存テーブルの削除や既存行の消去はしません。新しいテーブルはモデルを足して手順を追加します。既存テーブルの列を変えるときも、そのファイルに明示的な変更手順を足します。テーブルを消す手順は書きません。

テーブルを削除するコマンドは用意していません。`db.drop_all()` や DB ファイルの削除を、共有の手順に加えないでください。

## 担当機能の編集場所

担当者の割り当ては決めていません。機能ごとの作業範囲の例です。

| 機能 | まず編集する場所 | 現時点で置かないもの |
| --- | --- | --- |
| 回答の入力・保存・閲覧 | `app/blueprints/answers.py`、`app/templates/answers/index.html`。保存APIは `PUT /api/v1/answers/current` | サーバーが読める平文の回答列。入力フォームの実装 |
| ブラウザでの暗号化・復号 | `app/static/js/watashi/crypto.mjs` と `client.mjs` | サーバー側での復号 |
| 家族共有 | `app/services/family_service.py`、`/family` | 回答更新の自動反映。LINE送信 |
| QRの閲覧開始と停止 | `app/services/qr_service.py`、`/view/<id>` | QR画像の生成と表示（坂担当の画面） |
| パスキー認証 | `app/blueprints/auth.py`、`/auth`、`app/services/auth_service.py` | ログインできたことにする仮処理 |
| LINE API | 利用が決まってから `app/services/line.py` を新設 | ソースへの API キーの直書き |

## 共通ファイルを変えるとき

次のファイルは全員の起動や画面に影響します。機能用ブランチで変えて構いませんが、Pull Request の説明に、変える理由と確認したことを書いてください。

- `app/__init__.py`
- `app/extensions.py`
- `config.py`
- `run.py`
- `Pipfile`
- `.python-version`
- `app/templates/base.html`
- `app/static/css/style.css`
- `.gitignore`
- `.env.example`
- この README の起動手順

依存を追加するときも、管理方法は Pipenv と `Pipfile` だけにします。`requirements.txt`、Poetry、uv は併用しないでください。追加するパッケージはバージョンを指定し、`Pipfile` の差分を確認してからコミットします。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv install "追加するパッケージ==バージョン"
```

開発時だけ使うパッケージは次です。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv install --dev "追加するパッケージ==バージョン"
```

この操作で `Pipfile.lock` も更新されることがあります。そのファイルは既存の設定どおり Git に含めません。コミットするのは `Pipfile` の変更です。

やってはいけないことは次のとおりです。

- `.env`、`.venv/`、`instance/`、ローカル DB、`Pipfile.lock` をコミットしない
- 実在の個人情報や回答を入れない
- 未実装の回答入力画面、QR画像、LINE送信が動いているように見せるコードを入れない
- 既存 DB のデータを消す処理を実行しない
- 回答本文を、サーバーが読める形でログに出さない
- API の鍵が無いとアプリが起動しない状態にしない
- ルートに `app.py` を置かない。起動入口は `run.py` のままにする

## GitHub での作業手順

作業は `main` から分岐したブランチで行い、Pull Request で `main` へ取り込みます。`main` へ直接コミットしないでください。

```powershell
git checkout main
git pull origin main
git checkout -b feature/answers-draft
```

ブランチ名は機能が分かる短い名前にします。例として `feature/answers-draft`、`feature/sharing-settings`、`feature/passkey` があります。担当者名はブランチ名に必須ではありません。

```powershell
git status
git add app/blueprints/answers.py app/templates/answers/index.html tests/test_routes.py
git commit -m "回答画面の下書きを追加"
git push -u origin feature/answers-draft
```

`git add` の対象は、その作業で変更したファイルだけにしてください。`git status` に `.env`、`.venv/`、`instance/`、`Pipfile.lock` が出ていないことを確認します。

push のあと、GitHub のリポジトリページで **Compare & pull request** を開きます。base を `main`、compare を作業ブランチにします。説明には、変更した機能、確認した URL、共通ファイルを変えた理由を書きます。レビューする人はチームで決めてください。

指摘を反映するときは、同じブランチにコミットして、もう一度 `git push` します。

## 実装済みと、まだ未実装のもの

パスキー、ブラウザ内の暗号、家族共有、QRの閲覧セッション、端末移行は使えます。接続方法は `docs/morishita.md` です。回答の入力画面（今牧）、ノートと履歴（沖、`note/`）、坂担当のQR画像、小橋担当のLINEは未実装のままです。森下側の画面からは変更しません。

| 機能 | 状態 | 場所 |
| --- | --- | --- |
| 回答の入力画面 | 未実装 | `app/templates/answers/`。保存は `PUT /api/v1/answers/current` を呼ぶ |
| ブラウザ側の暗号化・復号 | 実装済み | `app/static/js/watashi/crypto.mjs`。サーバーは復号しない |
| パスキー認証 | 実装済み | `/auth` と `/api/v1/auth/` |
| 家族共有 | 実装済み | `/family` と `/api/v1/family/` |
| QRコードの画像 | 未実装 | 画像と表示は坂担当。閲覧開始APIは `/api/v1/qr-shares/` |
| 回答更新の自動反映 | 採用しない | 共有の更新APIを明示的に呼ぶ |
| LINE API | 未決定 | 使うと決まってから `app/services/line.py` |
| 本番データベース | 未決定 | ローカルは SQLite の暫定のまま |
| Pipfile.lock を Git に含めるか | 含めない | `.gitignore` |
| 公開方法 | 未決定 | デプロイ設定は置いていない |

ブラウザのHPKEは `app/static/vendor/hpke-js-1.8.0.esm.js`（同じ内容の `.mjs`）を同一オリジンから読みます。CDNは使いません。

## 設定の切り替え

`APP_ENV` で設定を選びます。

| APP_ENV | 用途 |
| --- | --- |
| `development` | 通常の起動。未設定時もこれ。既定の DB は `instance/dev.db` |
| `testing` | `pytest` が使うインメモリ SQLite。開発用 DB ファイルは使わない |
| `production` | 未決定。この値では起動しない |

開発用 DB を別の場所にするときは、`.env` の `DATABASE_URL` に SQLAlchemy の接続文字列を書きます。空欄のままにすると、暫定の SQLite を使います。SQLite 以外へ変えても、本番 DB が決まったことにはなりません。

`.env` を変えたあとは、起動中のサーバーを止めて、もう一度起動してください。

## 動作確認した環境

次の確認は Linux 上の Python 3.14.8 と Pipenv 2026.9.1 で行いました。仮想環境は `PIPENV_VENV_IN_PROJECT=1` によりリポジトリ内の `.venv` に作り、`python3.14 -m pipenv install --dev --python 3.14` で依存を入れています。Windows での起動確認はまだです。PowerShell の `py -3.14 -m pipenv` は、この Linux 環境では `python3.14 -m pipenv` に読み替えて確認しています。

- `Pipfile` から Flask 3.1.3、Flask-SQLAlchemy 3.1.1、python-dotenv 1.2.4、Click 8.5.0、pytest 9.1.1、cbor2 6.1.5、cryptography 50.0.2、pip-audit 2.10.1 をインストールできる
- `python -m pytest` は 67 件成功する。Node.js が暗号テストと家族画面の再確認操作を実行する
- `instance/dev.db` に確認用の行を入れてから `init-db` を 2 回実行し、その行が残る
- `http://localhost:5000/` で起動し、`/`、`/hello`、`/answers`、`/sharing`、`/auth`、`/family`、`/transfer`、`/health` が 200 になる
- `/` は「意思の記録」を表示する
- `/hello` の本文は `Hello, World!`
- `/answers` と `/sharing` の画面操作は未実装のまま
- `/auth`、`/family`、`/transfer` はパスキーと共有、移行の画面を返す
- `/health` は `status` と `database` がどちらも `ok`
- 実機のパスキーとPRFは未確認。テストは模擬パスキーとNode上の暗号関数

Windows でまだ見るものは、`py -3.14` の有無、`py -3.14 -m pip install --user pipenv`、`$env:PIPENV_VENV_IN_PROJECT = "1"`、コンソールの文字コード、ブラウザでの画面表示です。
