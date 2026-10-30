import { shareFingerprint } from "./crypto.mjs";

// 確認画面で見せた内容（範囲・回答・相手・鍵・期限）と、今の内容が同じか。
export function confirmationStillMatches(rememberedKey, selection, payload, context) {
  return typeof rememberedKey === "string"
    && rememberedKey !== ""
    && rememberedKey === shareFingerprint(selection, payload, context);
}

// 更新は、確認した相手・接続の共有だけに行う。
export function confirmationCoversShare(context, share) {
  return Boolean(context && context.connectionId && context.recipientKeyId)
    && context.recipientUserId === share.recipient_user_id
    && context.connectionId === share.connection_id;
}

// datetime-local の値を、サーバーへ送る形（秒まで、UTC）にする。空欄は期限なし。
export function toIso(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) throw new Error("期限の形式が正しくありません。");
  return date.toISOString().replace(/\.\d{3}Z$/, "Z");
}

// 共有の流れ。更新は相手と期限を変えないので、相手・期限の手順を省く。
export function shareSteps(updating) {
  const steps = [
    { id: "unlock", label: "ロック解除" },
    { id: "select", label: "共有内容の選択" },
    { id: "target", label: "家族と期限" },
    { id: "confirm", label: "再確認" },
  ];
  return updating ? steps.filter(step => step.id !== "target") : steps;
}
