/**
 * 共有リンクのQR画像。ライブラリの判定どおりに描き、周りに余白を空け、入力の誤りは通信の前に断る。
 */
import assert from "node:assert/strict";
import qrcode from "../app/static/vendor/qrcode-generator-2.0.4.mjs";
import { generateQrShareImage, renderQrCanvas } from "../app/static/js/watashi/qrShareView.mjs";

function fakeDocument() {
  return {
    createElement(tag) {
      assert.equal(tag, "canvas");
      const fills = [];
      const canvas = {
        width: 0, height: 0, attributes: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        getContext(kind) {
          assert.equal(kind, "2d");
          return {
            fillStyle: "",
            fillRect(x, y, width, height) { fills.push({ color: this.fillStyle, x, y, width, height }); },
          };
        },
      };
      canvas.fills = fills;
      return canvas;
    },
  };
}

const url = `http://localhost:5000/view/${"A".repeat(43)}#k=${"B".repeat(43)}&t=${"C".repeat(43)}`;
const canvas = renderQrCanvas(url, { size: 256, doc: fakeDocument() });
const expected = qrcode(0, "M");
expected.addData(url);
expected.make();
const count = expected.getModuleCount();
const span = count + 8;
const cell = Math.floor(256 / span);
assert.ok(cell >= 2);
assert.equal(canvas.width, span * cell);
assert.equal(canvas.height, span * cell);
assert.equal(canvas.attributes.role, "img");
// 最初に全体を白で塗り、残りは黒いモジュールだけ。
const [background, ...dark] = canvas.fills;
assert.deepEqual(background, { color: "#ffffff", x: 0, y: 0, width: canvas.width, height: canvas.height });
const drawn = new Set(dark.map(fill => {
  assert.equal(fill.color, "#000000");
  assert.equal(fill.width, cell);
  return `${fill.y / cell - 4},${fill.x / cell - 4}`;
}));
const want = new Set();
for (let row = 0; row < count; row += 1) {
  for (let column = 0; column < count; column += 1) {
    if (expected.isDark(row, column)) want.add(`${row},${column}`);
  }
}
assert.deepEqual(drawn, want);
// 周りの4モジュール分は何も描かない（読み取り用の余白）。
assert.ok(dark.every(fill => fill.x >= 4 * cell && fill.y >= 4 * cell
  && fill.x < (count + 4) * cell && fill.y < (count + 4) * cell));

assert.throws(() => renderQrCanvas("", { doc: fakeDocument() }), /URL/);

// 入力の誤りは、鍵の解除や共有の作成を呼ぶ前に断る（通信しない）。
const later = days => new Date(Date.now() + days * 86400000).toISOString().replace(/\.\d+Z$/, "Z");
for (const [options, pattern] of [
  [{ mode: "unlimited", expiresAt: null, selection: { scope: "all" } }, /不正な mode/],
  [{ mode: "long", expiresAt: null, selection: { scope: "all" } }, /expiresAt/],
  [{ mode: "long", expiresAt: later(1200), selection: { scope: "all" } }, /1096 日/],
  [{ mode: "timed", expiresAt: later(40), selection: { scope: "all" } }, /31 日/],
  [{ mode: "emergency", expiresAt: later(1), selection: { scope: "all" } }, /null/],
  [{ mode: "emergency", expiresAt: null }, /selection/],
  [{ mode: "emergency", expiresAt: null, selection: { scope: "questions", ids: [] } }, /ids/],
]) {
  await assert.rejects(generateQrShareImage(options), pattern);
}
console.log("QR share image passed");
