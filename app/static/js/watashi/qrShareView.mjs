// qrShareView.mjs
//
// QR画像生成専用モジュール。
//
// 【重要・セキュリティ上の前提】
// - QR画像にするのは createQrShare() が返す `url` のみ。
// - `url` には秘密鍵(k)と開始トークン(t)がフラグメント(#以降)として
//   含まれるため、以下は絶対に行わない:
//     - privateKey / startToken を変数に保持し続ける・どこかへ送信する
//     - url や created オブジェクト全体を console.log 等でログ出力する
//     - url を外部API/CDN経由のQR生成サービス(Google Charts、
//       api.qrserver.com 等)へ渡す — 秘密情報が外部サーバーに
//       送信されてしまうため NG。QRエンコードは必ずローカルに
//       バンドルしたライブラリで、ネットワーク通信なしに行うこと。
// - 鍵はページを離れると失われる。この画面内で
//   unlock() → createQrShare() → QR描画 を完結させること
//   (別画面で unlock して戻ってくる、という使い方はできない)。
// - `GET /api/v1/qr-shares` では url を復元できない。画面を離れたら
//   再度このフローをやり直す必要がある(ただし updateQrShare による
//   内容更新では url は変わらないため、QR画像の再発行は不要)。

import { createQrShare, unlock } from "/static/js/watashi/client.mjs";

// QRエンコードはローカル同梱のライブラリを想定(例: npm の `qrcode` を
// バンドルして /static/js/vendor/ 以下に配置するなど)。
// 実際の配置パスはプロジェクトに合わせて書き換えること。
import QRCode from "/static/js/vendor/qrcode.min.mjs";

const MAX_TIMED_DAYS = 31;
const ISO_UTC_Z_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$/;

export const QR_SHARE_MESSAGES = Object.freeze({
  preparing: "QRコードを作成しています。この画面を閉じずにお待ちください。",
  ready: "QRコードを作成しました。共有する相手が読み取れるようにしてください。",
  failed: "QRコードを作成できませんでした。ログイン状態や共有設定を確認して、もう一度お試しください。",
});

/**
 * selection の形を検証する。
 * - 省略・不正値はエラーにする(selectionを省くと作成自体が失敗し、
 *   全体共有にフォールバックすることはない、という仕様どおり)
 * - chapters / questions は ids が1件以上必須
 *   (空配列や存在しないIDも全体共有にはならない)
 */
function assertValidSelection(selection) {
  if (!selection || typeof selection !== "object") {
    throw new Error("selection は必須です(省略すると作成に失敗します)");
  }
  const { scope, ids } = selection;
  if (scope === "all") {
    return;
  }
  if (scope === "chapters" || scope === "questions") {
    if (!Array.isArray(ids) || ids.length === 0) {
      throw new Error(
        `scope="${scope}" の場合、ids に1件以上のIDを指定してください`
      );
    }
    return;
  }
  throw new Error(`不正な scope です: ${scope}`);
}

/** mode と expiresAt の組み合わせを検証する。 */
function assertValidModeAndExpiry(mode, expiresAt) {
  if (mode === "emergency") {
    if (expiresAt !== null) {
      throw new Error(
        'mode="emergency" のとき expiresAt は null にしてください(期限は設定できません)'
      );
    }
    return;
  }
  if (mode === "timed") {
    if (typeof expiresAt !== "string" || !ISO_UTC_Z_RE.test(expiresAt)) {
      throw new Error(
        'mode="timed" のとき expiresAt は UTCの "Z" 付きISO日時文字列が必要です(例: 2026-11-01T00:00:00Z)'
      );
    }
    const limit = new Date();
    limit.setUTCDate(limit.getUTCDate() + MAX_TIMED_DAYS);
    if (new Date(expiresAt).getTime() > limit.getTime()) {
      throw new Error(`expiresAt は最長 ${MAX_TIMED_DAYS} 日先までです`);
    }
    return;
  }
  throw new Error(`不正な mode です: ${mode}`);
}

/**
 * QR共有を新規作成し、QR画像(canvas)を返す。
 *
 * 呼び出し条件:
 *   - ログイン済みであること
 *   - この画面内で呼び切ること(鍵はページ遷移で消える)
 *
 * @param {Object} opts
 * @param {"emergency"|"timed"} opts.mode
 * @param {string|null} opts.expiresAt  emergencyはnull、timedはUTC "Z" 付きISO文字列
 * @param {{scope: "all"|"chapters"|"questions", ids?: string[]}} opts.selection
 * @param {number} [opts.size=256] QR画像の一辺(px)
 * @param {(message: string) => void} [opts.onStatus] 状態メッセージの通知先
 * @returns {Promise<{canvas: HTMLCanvasElement, shareId: string}>}
 *   canvas: 画面に貼り付けるQR画像。shareId: 停止操作などUI管理用の共有ID
 *   (秘密鍵・トークンを含まないため保持してよい)
 */
export async function generateQrShareImage(opts) {
  const { mode, expiresAt = null, selection, size = 256, onStatus } = opts ?? {};

  assertValidModeAndExpiry(mode, expiresAt);
  assertValidSelection(selection);

  try {
    onStatus?.(QR_SHARE_MESSAGES.preparing);

    // この画面内で認証〜QR作成まで完結させる
    await unlock();

    const created = await createQrShare({ mode, expiresAt, selection });

    // QR化するのは created.url だけ。created.privateKey / created.startToken は
    // 変数に取り出さず、created オブジェクトごとここで用済みにする。
    const canvas = document.createElement("canvas");
    await QRCode.toCanvas(canvas, created.url, {
      width: size,
      margin: 2,
      errorCorrectionLevel: "M",
    });

    onStatus?.(QR_SHARE_MESSAGES.ready);
    return { canvas, shareId: created.id };
  } catch (error) {
    onStatus?.(QR_SHARE_MESSAGES.failed);
    throw error;
  }
}

// ---------------------------------------------------------------
// 画面側での使用例
// ---------------------------------------------------------------
//
// import { generateQrShareImage } from "/static/js/watashi/qrShareView.mjs";
//
// const container = document.getElementById("qr-share-container");
//
// async function onCreateEmergencyShare(questionIds) {
//   container.replaceChildren(); // 古いQRを残さない
//   try {
//     const { canvas } = await generateQrShareImage({
//       mode: "emergency",
//       expiresAt: null,
//       selection: { scope: "questions", ids: questionIds },
//       onStatus: (message) => {
//         document.getElementById("status").textContent = message;
//       },
//     });
//     container.appendChild(canvas);
//   } catch (err) {
//     // err.message のみログに出す。created.url を含み得る値は出さない。
//     console.error("QR共有の作成に失敗しました:", err.message);
//   }
// }
//
// // 画面離脱時は鍵が失われるので、表示も消しておく
// window.addEventListener("beforeunload", () => {
//   container.replaceChildren();
// });
//
// // 内容だけ更新したい場合(URL・QR画像は変わらないため再発行不要):
// //   await updateQrShare({ shareId, expectedVersion, publicKey, selection });