// Form rendering stays separate from encrypted persistence.
export function readAnswers(form) {
  return [...form.querySelectorAll("[data-demo-question]")].map(question => {
    const values = [...question.querySelectorAll("input, select, textarea")].flatMap(control => {
      if (control.type === "radio" || control.type === "checkbox") {
        return control.checked ? [control.dataset.answerLabel] : [];
      }
      if (!control.value.trim()) return [];
      return [control.tagName === "SELECT"
        ? control.selectedOptions[0].textContent.trim()
        : control.value.trim()];
    });
    return { id: question.dataset.questionId, title: question.dataset.questionTitle, values };
  });
}

export function fillSampleAnswers(form) {
  for (const control of form.querySelectorAll("input, select, textarea")) {
    if (control.type === "radio" || control.type === "checkbox") {
      control.checked = control.dataset.demoSample === "true";
    } else {
      control.value = control.dataset.demoSample || "";
    }
  }
}

export function demoAnswerItems(form) {
  const items = {};
  for (const answer of readAnswers(form)) {
    if (!answer.values.length) continue;
    const question = [...form.querySelectorAll("[data-demo-question]")]
      .find(element => element.dataset.questionId === answer.id);
    const multiple = [...question.querySelectorAll("input, select, textarea")]
      .some(control => control.type === "checkbox");
    items[answer.id] = { state: "answered", value: multiple ? answer.values : answer.values[0] };
  }
  return items;
}

export function applyDemoItems(form, items) {
  const changes = [];
  for (const question of form.querySelectorAll("[data-demo-question]")) {
    const item = items[question.dataset.questionId];
    if (item && item.state !== "answered" && item.state !== "unanswered") {
      throw new Error("このテスト画面では編集できない回答状態があります。保存は行っていません。");
    }
    const controls = [...question.querySelectorAll("input, select, textarea")];
    const multiple = controls.some(control => control.type === "checkbox");
    const value = item?.state === "answered" ? item.value : multiple ? [] : "";
    if (multiple ? !Array.isArray(value) || value.some(part => typeof part !== "string") : typeof value !== "string") {
      throw new Error("保存された回答の形式を読み込めません。保存は行っていません。");
    }
    const choices = controls.filter(control => ["checkbox", "radio"].includes(control.type));
    if (choices.length) {
      const values = multiple ? value : value ? [value] : [];
      if (values.some(part => !choices.some(control => control.dataset.answerLabel === part))) {
        throw new Error("保存された選択肢を読み込めません。保存は行っていません。");
      }
      for (const control of choices) changes.push(() => { control.checked = values.includes(control.dataset.answerLabel); });
    } else {
      const control = controls[0];
      const option = control.tagName === "SELECT"
        ? [...control.options].find(candidate => candidate.textContent.trim() === value) : null;
      if (control.tagName === "SELECT" && value && !option) {
        throw new Error("保存された選択肢を読み込めません。保存は行っていません。");
      }
      changes.push(() => { control.value = control.tagName === "SELECT" ? option?.value || "" : value; });
    }
  }
  for (const change of changes) change();
}

export function initializeAnswerDemo(root, { editable = true, onChange = () => {} } = {}) {
  const form = root.querySelector("#demo-answer-form");
  if (!form) return;
  const progress = root.querySelector("#demo-progress");
  const count = root.querySelector("#demo-count");
  const status = root.querySelector("#demo-status");
  const review = root.querySelector("#demo-review");
  const reviewList = root.querySelector("#demo-review-list");

  function updateProgress() {
    const answers = readAnswers(form);
    const answered = answers.filter(answer => answer.values.length > 0).length;
    progress.max = answers.length;
    progress.value = answered;
    const label = `${answered} / ${answers.length}問`;
    if (count.textContent !== label) count.textContent = label;
  }

  function changed() {
    updateProgress();
    onChange();
  }
  form.addEventListener("input", changed);
  form.addEventListener("change", changed);
  root.querySelector("#demo-fill").addEventListener("click", () => {
    fillSampleAnswers(form);
    updateProgress();
    onChange();
    status.textContent = "サンプル回答を入れました。自由に書き換えて試せます。";
  });
  root.querySelector("#demo-reset").addEventListener("click", () => {
    form.reset();
    updateProgress();
    onChange();
    status.textContent = "入力をリセットしました。";
  });
  form.addEventListener("submit", event => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    const rows = readAnswers(form).map(answer => {
      const row = root.createElement("div");
      const title = root.createElement("dt");
      const value = root.createElement("dd");
      title.textContent = answer.title;
      value.textContent = answer.values.join("、") || "未回答";
      if (!answer.values.length) value.className = "demo-unanswered";
      row.append(title, value);
      return row;
    });
    reviewList.replaceChildren(...rows);
    status.textContent = "";
    form.hidden = true;
    review.hidden = false;
    review.focus();
  });
  root.querySelector("#demo-edit").addEventListener("click", () => {
    review.hidden = true;
    form.hidden = false;
    // Remove the copied summary when returning to editing.
    reviewList.replaceChildren();
    form.querySelector("input, select, textarea")?.focus();
  });
  root.querySelector("#demo-fields").disabled = !editable;
  form.reset();
  updateProgress();
  return {
    items: () => demoAnswerItems(form),
    setEditable(enabled) { root.querySelector("#demo-fields").disabled = !enabled; },
    restore(items) {
      applyDemoItems(form, items);
      review.hidden = true;
      form.hidden = false;
      reviewList.replaceChildren();
      status.textContent = "";
      updateProgress();
    },
  };
}
