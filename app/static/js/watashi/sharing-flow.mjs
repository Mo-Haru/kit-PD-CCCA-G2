// 共有リンク（QR）を作る手順で使う、期限と確認の判定。
// サーバーの上限（QR_TIMED_MAX_SECONDS / QR_LONG_MAX_SECONDS）と同じ日数。
export const MAX_DAYS = { timed: 31, long: 1096 };

export const modeLabel = mode => ({
  emergency: "緊急用（最初の閲覧のみ）", timed: "期限付き", long: "長期（最長3年）",
}[mode] || mode);

// datetime-local に入れる形（端末の時刻、分まで）。
function localInputValue(date) {
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
}

export function longExpiryDefault(now = new Date()) {
  const date = new Date(now.getTime());
  date.setFullYear(date.getFullYear() + 3);
  date.setSeconds(0, 0);
  return localInputValue(date);
}

// 期限として選べる範囲。下限は次の分、上限は切り捨てた分にして、範囲内ならサーバーでも通るようにする。
export function expiryBounds(mode, now = new Date()) {
  const min = new Date(now.getTime() + 60000);
  const max = new Date(now.getTime() + MAX_DAYS[mode] * 86400000);
  return { min: localInputValue(min), max: localInputValue(max) };
}

// 確認画面で見せた内容（範囲・種類・期限・アカウント）と、今の内容が同じかを比べる鍵。
export function qrConfirmationKey(selection, mode, expiresAt, ownerId) {
  return JSON.stringify({ selection, mode, expiresAt, ownerId });
}

export function shareSteps() {
  return [
    { id: "unlock", label: "ロック解除" },
    { id: "select", label: "共有内容の選択" },
    { id: "type", label: "リンクの種類と期限" },
    { id: "confirm", label: "再確認" },
  ];
}
