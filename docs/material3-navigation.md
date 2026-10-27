# Material Design 3 と共通レイアウト

## 対象と方針

共通ヘッダー、左ナビゲーション、角丸の本文パネル、ログイン・アカウント画面に、Material Design 3（M3）のデザイントークンとコンポーネント指針を適用する。
React用のMaterial UI（MUI）を導入するものではない。Flask/Jinjaと既存CSS・JavaScriptを使用し、ビルド工程や外部配信のフォント・アイコンは追加しない。

- 外側の背景・ヘッダー・メニューは Surface Container Low、本文パネルと独立した `/auth` のカードは Surface に統一する。
- 認証・暗号化・データ処理・ログイン後の戻り先は変更しない。既存の機能画面は本文パネルと共通パレットに合わせ、操作内容は変更しない。
- 既存の緑を基調にした静的なライトテーマ。OSの壁紙などから色を生成するダイナミックカラーや、ダークテーマは未実装。
- アプリ全体のM3完全準拠や、Googleによる認証を示すものではない。

## 適用する仕様

| 要素 | 適用内容 |
| --- | --- |
| Top app bar | 基本行64px、Title Large（22px / 28px、400）、24pxアイコン、48pxの操作領域 |
| Header action | Label Large（14px / 20px、500）、丸いtonalボタン、48px以上の操作領域 |
| Standard drawer | 幅360px、ヘッダーと同じ Surface Container Low、外周の角丸・隙間なし、elevation 0 |
| Modal drawer | 最大幅360px、Surface Container Low、終端の角丸16px、elevation 1、黒32%のscrim |
| Destination | 高さ56px以上、左右16pxの内側余白、24pxアイコン、Label Large |
| Selected destination | Secondary Container / On Secondary Container、完全に丸い選択表示、`aria-current="page"` |
| State layers | hover 8%、focus / pressed 12%。キーボードフォーカスには輪郭も表示 |
| Motion | 開くとき300ms・emphasized decelerate、閉じるとき200ms・emphasized accelerate。PCでもメニューのスライドと本文幅の変化を連動させ、ボタンは三本線と閉じるアイコンの間で変形する。`prefers-reduced-motion` では遷移なし |
| Content surface | 四隅28pxの角丸、全ページ共通の Surface。ヘッダー直下に接して配置し、本文内で余白を確保 |
| Auth / account | 56px以上の入力欄、48px以上のボタン・チェック項目、緑系のfilled / tonal / outlinedボタン、状態・エラー用のM3ロール |
| Input fields | 共通のOutlined型をベースに、ボタンに合わせて1行欄はカプセル型、複数行欄は28pxの角丸へ調整。高さ56px・通常1px枠、フォーカス時はPrimaryの2px枠。文字入力のラベルはフォーカス時・入力済みで枠線上に移動し、日時・選択・複数行欄では常時上げる。補助文は12px / 16px、無効・入力エラーの状態色も共通化 |

色・形状・寸法・モーションは `app/static/css/style.css` の `--md-*` 変数に集約する。既存のページ用変数（`--bg`、`--paper` など）も同じM3ロールを参照し、ページごとの青・ベージュの混在をなくす。ログイン・アカウント画面の詳細は `app/static/css/account.css` で定義する。
アイコンは `app/templates/_navigation_icons.html` の自前SVGで、読み上げ対象から外し、隣の日本語ラベルやボタンの `aria-label` を利用する。
入力欄は `style.css` の `.md-field`・`.md-field-control`・`.md-field-label`・`.md-field-help` を共有する。ネイティブのlabel関連付け、入力制約、日付選択・selectの操作を維持し、長い注意書きは枠内ラベルと分けて補助文に置く。入力欄用の外部ライブラリは追加しない。

## 既存要件に合わせた調整

- 幅960px以上をPC用の折りたたみ可能な左メニューとし、それ未満はモーダルドロワーとする。既存の切替境界を維持する。
- 狭い画面ではドロワーの幅を「画面幅 − 48px」までに制限し、外側をタップする領域を残す。
- スマホのapp barは1列に保ち、認証済みの場合だけ「ログイン済み」のバッジを表示する。名前・詳細な状態・アカウントへのリンク、未ログイン時のログインリンクはドロワー内に表示する。PCでは従来どおりヘッダーにアカウント情報を表示する。
- ヘッダー・メニュー・本文の間に外側の隙間を置かず、共通の背景の上に角丸の本文を載せる。本文とヘッダーは最大1440pxの領域で揃える。
- 共通画面は表示領域の高さに収め、ヘッダーとメニューを残して本文パネル内だけをスクロールする。ヘッダーが折り返した場合も、その実際の高さを除いた領域を本文とメニューへ割り当てる。開発用バナーとフッターも本文パネル内でスクロールする。
- 縦幅が短い場合はドロワー内のアカウント情報とナビゲーション一覧をまとめてスクロールし、見出しと閉じるボタンを表示し続ける。スマホのドロワーを開いている間は、背景の本文スクロールも停止する。
- `/auth` は引き続き共通ヘッダー・メニューなしの独立ページ。`/account` は共通ヘッダーと本文パネル内の管理画面とする。同意必須・暗号化の範囲・パスキー紛失時の注意は維持する。
- キーボード操作、フォーカス制御、背景のinert化、スクロール停止、戻る操作時の後始末を維持する。
- 本文のスクロール領域にはキーボードでフォーカスでき、「本文へ」リンクも本文へフォーカスを移す。JavaScript無効時は固定レイアウトにせず、通常のナビゲーションとページ全体のスクロールを利用する。

## 参照

- [Material Web: Text field](https://material-web.dev/components/text-field/)
- [Material Web: Outlined text field tokens](https://github.com/material-components/material-web/blob/main/tokens/versions/v0_192/_md-comp-outlined-text-field.scss)
- [Navigation drawer](https://m3.material.io/components/navigation-drawer/specs)
- [Top app bar](https://m3.material.io/components/top-app-bar/specs)
- [AndroidX NavigationDrawerTokens](https://github.com/androidx/androidx/blob/androidx-main/compose/material3/material3/src/commonMain/kotlin/androidx/compose/material3/tokens/NavigationDrawerTokens.kt)
- [AndroidX AppBarSmallTokens](https://github.com/androidx/androidx/blob/androidx-main/compose/material3/material3/src/commonMain/kotlin/androidx/compose/material3/tokens/AppBarSmallTokens.kt)
- [Material Web state-layer tokens](https://github.com/material-components/material-web/blob/main/tokens/versions/v0_192/_md-sys-state.scss)
