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
- 直接使うライブラリのバージョンは `Pipfile` に固定する
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
| 業務モデル | まだ作らない | テーブル設計が未確定。回答本文をサーバーが読める列にすると、方針と矛盾する。`app/models/user.py` には、ルートの `models.py` から移したコメントだけの User 下書きがある。クラスはコメントのままなので、テーブルは作られない。 |

直接使うライブラリは `Pipfile` で次のバージョンに固定しています。

- Flask 3.1.3
- Flask-SQLAlchemy 3.1.1
- python-dotenv 1.2.4
- pytest 9.1.1（開発用）

インストール時に入る間接的な依存のバージョンは、`Pipfile.lock` に記録できます。そのファイルは既存の設定どおりリポジトリへは含めません。

## Windows で必要なソフト

| ソフト | バージョン | 用途 |
| --- | --- | --- |
| Git | 2 系 | リポジトリの取得、ブランチ、Pull Request |
| Python | 3.14 系 | アプリの実行。インストール時に Python launcher（`py`）を有効にする |
| Pipenv | Python 3.14 に入れる | 仮想環境と依存のインストール。コマンドは `py -3.14 -m pipenv` |
| Web ブラウザ | HTML を表示できるもの | 画面の確認。パスキー用のブラウザ要件は未決定 |

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
py -3.14 -m pipenv run python -m flask --app run run --debug
```

`py` が使えない場合は、`python --version` が 3.14 系であることを確認したうえで、`py -3.14` を `python` に置き換えます。

```powershell
python -m pip install --user pipenv
$env:PIPENV_VENV_IN_PROJECT = "1"
python -m pipenv install --dev --python 3.14
```

`.env` のコピーは省略できます。省略しても、開発用の暫定値（SQLite とダミーの `SECRET_KEY`）で起動します。設定を変えるときだけ `.env` を編集します。`.env.example` に秘密情報は入っていません。

`init-db` は、登録済みモデルのテーブルを作ります。現時点では有効な業務用モデルが無いため、接続の確認と、以後モデルを足したときの手順として実行します。既存のテーブルや行は削除しません。このコマンドを二回実行しても、既存データは消えません。

同じウィンドウで仮想環境に入ってから短くすることもできます。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv shell
python -m flask --app run init-db
python -m flask --app run run --debug
```

`pipenv shell` が止まった場合は、実行ポリシーを変更せず、`py -3.14 -m pipenv run ...` の形を使ってください。

サーバーを止めるときは、そのウィンドウで Ctrl+C を押します。

起動したままテストする場合は、別の PowerShell を開いてリポジトリのフォルダへ移動し、次を実行します。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv run python -m pytest
```

## 起動できたかの確認

開発サーバーは `http://127.0.0.1:5000/` で待ち受けます。

| URL | 正常なとき |
| --- | --- |
| http://127.0.0.1:5000/ | 「意思の記録」と、回答・共有・認証へのリンクが見える |
| http://127.0.0.1:5000/hello | 本文が `Hello, World!` |
| http://127.0.0.1:5000/answers | 「未実装」と表示され、保存は実行できない |
| http://127.0.0.1:5000/sharing | 「未実装」と表示され、共有操作は実行できない |
| http://127.0.0.1:5000/auth | 「未実装」と表示され、ログインした状態にならない |
| http://127.0.0.1:5000/health | JSON で `status` と `database` がどちらも `ok` |

`/hello` は、以前の単一ファイルのアプリが返していた確認用の応答です。HTML の画面ではなく、本文 `Hello, World!` をそのまま返します。

画面上部に、保存・認証・暗号化・共有が未実装である旨が出ます。未実装のボタンは押せません。認証できたように見せる画面や、暗号化できたように見せる処理はありません。

ポート 5000 が使用中のときは、次でポートを変えます。

```powershell
$env:PIPENV_VENV_IN_PROJECT = "1"
py -3.14 -m pipenv run python -m flask --app run run --debug --port 5001
```

その場合の URL は `http://127.0.0.1:5001/` です。

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
  models/              DB モデル。有効な業務モデルはまだ無い
    user.py            コメントだけの User 下書き。テーブルは作られない
  templates/           共通レイアウトと各画面
  static/css/          共通の CSS
config.py              開発用設定とテスト用設定
run.py                 起動用のアプリオブジェクト
Pipfile                直接使う依存のバージョンを固定
.python-version        3.14
.env.example           秘密情報を含まない設定例
tests/                 起動、ルート、DB 接続の確認
```

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

`app/models/user.py` は、ルートにあった `models.py` の下書きです。中身はコメントだけで、テーブルは作られません。有効にするときは、このファイルだけで `SQLAlchemy()` を新しく作らず、`app.extensions` の `db` を使ってください。テーブル設計が決まるまでは、コメントのままにします。

回答本文の列は、ブラウザ側の暗号化方式が決まるまで作りません。サーバーが内容を読める保存やログは、方針と矛盾します。

テーブルを削除するコマンドは用意していません。`db.drop_all()` や DB ファイルの削除を、共有の手順に加えないでください。

## 担当機能の編集場所

担当者の割り当ては決めていません。機能ごとの作業範囲の例です。

| 機能 | まず編集する場所 | 現時点で置かないもの |
| --- | --- | --- |
| 回答の入力・保存・閲覧 | `app/blueprints/answers.py`、`app/templates/answers/index.html`。モデルが必要になったら `app/models/` | サーバーが読める平文の回答列 |
| ブラウザでの暗号化・復号 | 方式が決まってから `app/static/js/` を新設 | サーバー側での復号。暗号化できたように見せる仮処理 |
| 共有の有効・無効、期限 | `app/blueprints/sharing.py`、`app/templates/sharing/index.html` | 回答更新の自動反映 |
| QRコードによる共有 | `app/blueprints/sharing.py` と `app/templates/sharing/` | 未選定の QR ライブラリの先行追加 |
| パスキー認証 | `app/blueprints/auth.py`、`app/templates/auth/index.html`。ブラウザ側が必要になったら `app/static/js/` | ログインできたことにする仮処理 |
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
- 未実装の認証、暗号化、共有が動いているように見せるコードを入れない
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

## 未実装の機能と、先に決めること

画面上の入口はあります。処理は未実装で、POST も受け付けません。

| 機能 | 状態 | 追加場所 | 実装前に決めること |
| --- | --- | --- | --- |
| 回答の入力・保存・閲覧 | 未実装 | `app/blueprints/answers.py`、`app/templates/answers/`、必要なら `app/models/` | 保存する項目。本文をサーバーに持たない形 |
| ブラウザ側の暗号化・復号 | 未実装 | 決定後に `app/static/js/` | 暗号方式、鍵の生成と保管、共有先が復号する手順。サーバーは復号しない |
| パスキー認証 | 未実装 | `app/blueprints/auth.py`、`app/templates/auth/` | 使用するライブラリ、対応ブラウザ、パスキーの登録単位 |
| 共有の有効・無効と期限 | 未実装 | `app/blueprints/sharing.py`、`app/templates/sharing/` | 明示的な共有の単位、期限の表し方、無効化したあとの見え方 |
| QRコード | 未実装 | 共有機能と同じ場所 | QR に載せる内容。回答本文そのものを載せるかどうか |
| 回答更新の自動反映 | 採用しない方針 | なし | 自動反映する処理は追加しない |
| LINE API | 未決定 | 使うと決まってから `app/services/line.py` | 使うかどうか、アカウント、メッセージに載せる範囲 |
| 本番データベース | 未決定 | `DATABASE_URL` と `config.py` | 製品、接続情報の渡し方、バックアップ。ローカルは SQLite の暫定のまま |
| Pipfile.lock を Git に含めるか | 含めない | `.gitignore` | 既存の無視設定を維持している。含めると決めたときだけ無視を外す |
| 公開方法 | 未決定 | なし | ホスティング。この土台にはデプロイ設定を置いていない |

LINE、パスキー、暗号化、QR のライブラリや鍵は、今回の起動には不要です。

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

- `Pipfile` から Flask 3.1.3、Flask-SQLAlchemy 3.1.1、python-dotenv 1.2.4、pytest 9.1.1 をインストールできる
- `python -m pytest` は 16 件成功する
- `instance/dev.db` に確認用の行を入れてから `init-db` を 2 回実行し、その行が残る
- `http://127.0.0.1:5000/` で起動し、`/`、`/hello`、`/answers`、`/sharing`、`/auth`、`/health` が 200 になる
- `/` は「意思の記録」と「未実装」を表示する
- `/hello` の本文は `Hello, World!`
- `/answers`、`/sharing`、`/auth` は「未実装」と「この操作は実行できません」を表示する
- `/health` は `status` と `database` がどちらも `ok`

Windows でまだ見るものは、`py -3.14` の有無、`py -3.14 -m pip install --user pipenv`、`$env:PIPENV_VENV_IN_PROJECT = "1"`、コンソールの文字コード、ブラウザでの画面表示です。
