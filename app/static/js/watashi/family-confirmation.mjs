import qrcode from "../../vendor/qrcode-generator-2.0.4.mjs";

// 申請した人が招待した人へ渡す確認リンク。トークンはfragmentだけに入れ、サーバーへのURLには載せない。
export function confirmationUrl(confirmation, origin) {
  const url = new URL(confirmation.confirm_path, origin);
  url.hash = new URLSearchParams({ token: confirmation.token }).toString();
  return url.toString();
}

export function qrDataUrl(text) {
  const code = qrcode(0, "M");
  code.addData(text);
  code.make();
  return code.createDataURL(6, 4);
}

// 画面の固定要素（confirmation-*）へ確認情報を出す。返り値は確認リンク。
export function showConfirmation(node, confirmation, origin, makeQr = qrDataUrl) {
  const url = confirmationUrl(confirmation, origin);
  node("confirmation-code").textContent = confirmation.code;
  node("confirmation-link").textContent = url;
  node("confirmation-qr").src = makeQr(url);
  node("confirmation-expiry").textContent = `有効期限: ${new Date(confirmation.expires_at).toLocaleString()}`;
  node("confirmation-output").hidden = false;
  return url;
}

export function hideConfirmation(node) {
  node("confirmation-output").hidden = true;
  node("confirmation-code").textContent = "";
  node("confirmation-link").textContent = "";
  node("confirmation-qr").removeAttribute?.("src");
}
