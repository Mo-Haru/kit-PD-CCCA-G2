import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { initializeAnswerDemo, readAnswers } from "../app/static/js/watashi/answer-demo.mjs";

// Minimal DOM harness for the interaction contract; native layout is checked separately.
class Element {
  dataset = {};
  children = [];
  listeners = {};
  textContent = "";
  hidden = false;
  disabled = true;
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  fire(name, event = {}) { this.listeners[name](event); }
  focus() { focused = this; }
  set innerHTML(value) { throw new Error("Answers must be rendered as text"); }
}

let focused;
const fixture = JSON.parse(readFileSync(new URL("../app/data/answer-demo.json", import.meta.url)));
const questions = fixture.questions.map(question => {
  const group = new Element();
  group.dataset = { questionId: question.id, questionTitle: question.text };
  const choice = ["single_choice", "multiple_choice"].includes(question.input_type);
  group.controls = (choice ? question.options : [{}]).map(option => {
    const control = new Element();
    control.tagName = question.input_type === "select" ? "SELECT" : "INPUT";
    control.type = choice ? (question.input_type === "single_choice" ? "radio" : "checkbox") : "text";
    control.checked = false;
    control.value = "";
    control.dataset = {
      answerLabel: option.text,
      demoSample: choice ? String(Array.isArray(question.sample)
        ? question.sample.includes(option.value) : question.sample === option.value) : question.sample,
    };
    Object.defineProperty(control, "selectedOptions", { get() {
      return [{ textContent: question.options.find(item => item.value === control.value)?.text || "" }];
    } });
    return control;
  });
  group.querySelectorAll = () => group.controls;
  return group;
});
const controls = questions.flatMap(question => question.controls);
const ids = ["demo-answer-form", "demo-fields", "demo-progress", "demo-count", "demo-status",
  "demo-review", "demo-review-list", "demo-fill", "demo-reset", "demo-edit"];
const elements = Object.fromEntries(ids.map(id => [id, new Element()]));
const form = elements["demo-answer-form"];
form.querySelectorAll = selector => selector === "[data-demo-question]" ? questions : controls;
form.querySelector = () => controls[0];
form.reset = () => controls.forEach(control => { control.value = ""; control.checked = false; });
form.reportValidity = () => true;
const root = { querySelector: selector => elements[selector.slice(1)], createElement: () => new Element() };
for (const name of ["fetch", "localStorage", "sessionStorage"]) {
  Object.defineProperty(globalThis, name, { configurable: true, get() {
    throw new Error(`Demo must not use ${name}`);
  } });
}

initializeAnswerDemo(root);
assert.equal(elements["demo-fields"].disabled, false);
assert.equal(elements["demo-count"].textContent, "0 / 6問");
elements["demo-fill"].fire("click");
assert.equal(elements["demo-count"].textContent, "6 / 6問");
assert.deepEqual(readAnswers(form)[2].values, ["家族や友人との時間", "自分のペースで過ごすこと"]);
assert.deepEqual(readAnswers(form)[3].values, ["自宅"]);
controls[0].value = "<img src=x onerror=alert(1)>";
let prevented = false;
form.fire("submit", { preventDefault() { prevented = true; } });
assert.ok(prevented);
assert.ok(form.hidden);
assert.equal(elements["demo-review"].hidden, false);
assert.equal(focused, elements["demo-review"]);
assert.equal(elements["demo-review-list"].children.length, 6);
assert.equal(elements["demo-review-list"].children[0].children[1].textContent, controls[0].value);
elements["demo-edit"].fire("click");
assert.equal(form.hidden, false);
assert.equal(elements["demo-review"].hidden, true);
assert.equal(focused, controls[0]);
assert.equal(controls[0].value, "<img src=x onerror=alert(1)>");
elements["demo-reset"].fire("click");
assert.equal(elements["demo-progress"].value, 0);
controls[0].value = "   ";
form.fire("input");
assert.equal(elements["demo-count"].textContent, "0 / 6問");
form.fire("submit", { preventDefault() {} });
assert.ok(elements["demo-review-list"].children.every(row => row.children[1].textContent === "未回答"));
console.log("Answer demo interactions passed");
