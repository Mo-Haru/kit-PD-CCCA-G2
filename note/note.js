/* =====================================================================
   note.js ： 「わたしノート・進捗表示画面」の動き（回答入力つき）
   ---------------------------------------------------------------------
   やっていること
     1. 質問を縦に並べ、それぞれに「答えを入力する欄」を出す
     2. 「保存する」を押すと答えを保存し、回答済みの表示に切り替える
     3. 回答済みの数から、進捗バー（%）を自動で計算して反映する
     4. 回答済みの答えは「書き直す」で、あとから直せる

   【結合するときのメモ】
   ・質問の中身は、下の「A. 質問の一覧」を書き換えるだけで変わります。
   ・答えの保存先は、いまは「このブラウザの中（localStorage）」です。
     サーバーにつなぐときは「C. 保存と読み込み」の2つの関数だけを
     差し替えれば、他の部分はそのまま動きます。
   ・変数名・関数名はすべて「note」で始めています（他メンバーのJSとの衝突防止）。
   ・全体を即時実行関数 (function () { ... })() で包んでいるので、
     グローバル変数を汚しません。
   ===================================================================== */

(function () {
  'use strict'; // 書き間違いを見つけやすくするおまじない


  /* -------------------------------------------------------------------
     A. 質問の一覧（ここを本物の質問に差し替える）
     -------------------------------------------------------------------
     id   : 質問ごとの番号。重複させないこと。
            保存データとの紐づけに使うので、いちど決めたら変えないでください。
     text : 画面に出す質問文
     ※ 質問の数を増やす・減らすと、進捗の%も自動で変わります。
     ------------------------------------------------------------------- */
  var noteQuestions = [
    { id: 'q1', text: '質問1（内容はあとで決めます）' },
    { id: 'q2', text: '質問2（内容はあとで決めます）' },
    { id: 'q3', text: '質問3（内容はあとで決めます）' },
    { id: 'q4', text: '質問4（内容はあとで決めます）' },
    { id: 'q5', text: '質問5（内容はあとで決めます）' }
  ];


  /* -------------------------------------------------------------------
     B. 画面の状態（プログラムの中で覚えておくもの）
     ------------------------------------------------------------------- */

  // 保存に使う名前（localStorage の引き出しの名前）
  var NOTE_STORAGE_KEY = 'noteAnswers';

  // 答えのデータ。形は { 'q1': { text: '答え', answeredAt: '2026-10-14' }, ... }
  var noteAnswers = {};

  // 「書き直し中」の質問を覚えておく。形は { 'q1': true }
  var noteEditing = {};

  // 「保存しました」を表示中の質問ID（1つだけ）
  var noteSavedId = null;

  // ブラウザへの保存に失敗したかどうか（失敗時は注意書きを出す）
  var noteStorageFailed = false;


  /* -------------------------------------------------------------------
     C. 保存と読み込み
     ★ サーバー連携するときは、この2つの関数だけ差し替えてください。
     ------------------------------------------------------------------- */

  // 保存したデータを読み込む
  function loadNoteAnswers() {
    try {
      var raw = window.localStorage.getItem(NOTE_STORAGE_KEY);
      noteAnswers = raw ? JSON.parse(raw) : {};
    } catch (error) {
      noteAnswers = {}; // 読み込めなければ、空から始める
    }
    if (!noteAnswers || typeof noteAnswers !== 'object') {
      noteAnswers = {};
    }
  }

  // 答えのデータを保存する（成功したら true、失敗したら false を返す）
  function saveNoteAnswers() {
    try {
      window.localStorage.setItem(NOTE_STORAGE_KEY, JSON.stringify(noteAnswers));
      return true;
    } catch (error) {
      return false;
    }
  }


  /* -------------------------------------------------------------------
     D. 便利な小さい関数
     ------------------------------------------------------------------- */

  // 今日の日付を '2026-10-14' の形で返す
  function getTodayText() {
    var now = new Date();
    var month = String(now.getMonth() + 1).padStart(2, '0');
    var day = String(now.getDate()).padStart(2, '0');
    return now.getFullYear() + '-' + month + '-' + day;
  }

  // '2026-10-14' → '2026年10月14日' に変換する
  function formatNoteDate(dateText) {
    var parts = dateText.split('-');
    return parts[0] + '年' + Number(parts[1]) + '月' + Number(parts[2]) + '日';
  }

  // 要素を作って、クラス名と文字を付ける
  // textContent を使うので、入力に < や > が入っても安全に表示される
  function createNoteElement(tagName, className, text) {
    var element = document.createElement(tagName);
    if (className) {
      element.className = className;
    }
    if (text !== undefined) {
      element.textContent = text;
    }
    return element;
  }

  // ボタンを作る（type="button" にして、フォーム送信と間違えないようにする）
  function createNoteButton(label, extraClass) {
    var button = createNoteElement('button', 'note-btn ' + extraClass, label);
    button.type = 'button';
    return button;
  }

  // この質問に、保存済みの答えがあるか
  function isNoteAnswered(questionId) {
    return Boolean(noteAnswers[questionId] && noteAnswers[questionId].text);
  }

  // id から質問データを探す
  function findNoteQuestion(questionId) {
    for (var i = 0; i < noteQuestions.length; i++) {
      if (noteQuestions[i].id === questionId) {
        return noteQuestions[i];
      }
    }
    return null;
  }


  /* -------------------------------------------------------------------
     E. 進捗バーの表示
     ------------------------------------------------------------------- */
  function renderNoteProgress() {
    var total = noteQuestions.length;

    // 回答済みの数を数える
    var answeredCount = 0;
    noteQuestions.forEach(function (question) {
      if (isNoteAnswered(question.id)) {
        answeredCount++;
      }
    });
    var remaining = total - answeredCount;

    // パーセントを計算（質問が0件のときは0%。0〜100の整数にそろえる）
    var percent = total > 0 ? Math.round((answeredCount / total) * 100) : 0;
    percent = Math.max(0, Math.min(100, percent));

    // HTML側の部品を取得
    var percentEl = document.getElementById('note-percent');
    var barEl     = document.getElementById('note-bar');
    var fillEl    = document.getElementById('note-bar-fill');
    var textEl    = document.getElementById('note-progress-text');

    // 数字・読み上げ用の値・説明文を反映
    percentEl.textContent = percent;
    barEl.setAttribute('aria-valuenow', percent);

    if (total === 0) {
      textEl.textContent = '質問は、まだ準備中です。';
    } else if (answeredCount === 0) {
      textEl.textContent = total + '問中、まだ回答していません。最初の質問から答えてみましょう。';
    } else if (remaining > 0) {
      textEl.textContent = total + '問中' + answeredCount + '問に回答済みです。あと' + remaining + '問で完了です。';
    } else {
      textEl.textContent = 'すべての質問に回答しました。おつかれさまでした。';
    }

    // バーの長さを反映。
    // 1コマ待ってから幅を変えると、CSSの transition でスーッと伸びる
    window.requestAnimationFrame(function () {
      fillEl.style.width = percent + '%';
    });
  }


  /* -------------------------------------------------------------------
     F. 質問カード（1件ぶん）
     -------------------------------------------------------------------
     カードの状態は2つあります。
       ① 回答済み（書き直し中ではない）… 答えを表示し、「書き直す」ボタンを出す
       ② 未回答、または書き直し中 …… 入力欄と「保存する」ボタンを出す
     ------------------------------------------------------------------- */
  function createNoteItem(question) {
    var item = createNoteElement('li', 'note-item');
    item.setAttribute('data-note-id', question.id); // あとでこのカードを探すための目印

    var answered = isNoteAnswered(question.id);
    var editing = Boolean(noteEditing[question.id]);

    // --- 上の小さい文字：回答した日 or 未回答 ---
    var statusText = answered
      ? formatNoteDate(noteAnswers[question.id].answeredAt) + 'に回答'
      : 'まだ回答していません';
    item.appendChild(createNoteElement('p', 'note-item-date', statusText));

    // --- 質問文 ---
    item.appendChild(createNoteElement('h3', 'note-question', question.text));

    if (answered && !editing) {
      // ① 回答済みの表示
      item.appendChild(createNoteElement('p', 'note-answer-label', 'あなたの答え'));
      item.appendChild(createNoteElement('p', 'note-answer', noteAnswers[question.id].text));

      // 保存した直後だけ「保存しました」を出す
      if (noteSavedId === question.id) {
        var savedText = noteStorageFailed
          ? '保存しました。（このブラウザでは端末への保存ができないため、ページを閉じると消えます）'
          : '保存しました。';
        item.appendChild(createNoteElement('p', 'note-saved', savedText));
      }

      var editButton = createNoteButton('書き直す', 'note-btn-sub');
      editButton.addEventListener('click', function () {
        clearNoteSavedMessage();
        noteEditing[question.id] = true;
        refreshNoteItem(question.id);
        // 書き直しやすいよう、入力欄にカーソルを置く
        var textarea = findNoteItem(question.id).querySelector('textarea');
        if (textarea) {
          textarea.focus();
        }
      });
      var editActions = createNoteElement('div', 'note-actions');
      editActions.appendChild(editButton);
      item.appendChild(editActions);

    } else {
      // ② 入力欄の表示
      var textareaId = 'note-input-' + question.id;

      var label = createNoteElement('label', 'note-answer-label', 'あなたの答え');
      label.setAttribute('for', textareaId); // ラベルと入力欄を結びつける
      item.appendChild(label);

      var textarea = createNoteElement('textarea', 'note-textarea');
      textarea.id = textareaId;
      textarea.rows = 5;
      textarea.maxLength = 500; // 長すぎる入力を防ぐ（必要に応じて変更）
      textarea.placeholder = 'ここに答えを書いてください';
      if (answered) {
        textarea.value = noteAnswers[question.id].text; // 書き直しのときは今の答えを入れておく
      }
      item.appendChild(textarea);

      // 入力が空のときなどのエラーメッセージ置き場
      var errorEl = createNoteElement('p', 'note-error');
      errorEl.setAttribute('role', 'alert'); // 読み上げ対応
      item.appendChild(errorEl);

      var actions = createNoteElement('div', 'note-actions');

      // 「保存する」ボタン
      var saveButton = createNoteButton('保存する', 'note-btn-main');
      saveButton.addEventListener('click', function () {
        var value = textarea.value.trim(); // 前後の空白は取り除く

        // 何も書かれていなければ、保存せずにお知らせする
        if (value === '') {
          errorEl.textContent = '答えを書いてから「保存する」を押してください。';
          textarea.focus();
          return;
        }

        clearNoteSavedMessage();
        noteAnswers[question.id] = { text: value, answeredAt: getTodayText() };
        noteStorageFailed = !saveNoteAnswers();
        noteSavedId = question.id;
        noteEditing[question.id] = false;

        refreshNoteItem(question.id); // このカードだけ作り直す
        renderNoteProgress();         // 進捗バーを更新する
      });
      actions.appendChild(saveButton);

      // 書き直し中のときだけ「やめる」ボタンも出す
      if (answered) {
        var cancelButton = createNoteButton('やめる', 'note-btn-sub');
        cancelButton.addEventListener('click', function () {
          noteEditing[question.id] = false;
          refreshNoteItem(question.id);
        });
        actions.appendChild(cancelButton);
      }

      item.appendChild(actions);
    }

    return item;
  }

  // 画面上のカードを探す
  function findNoteItem(questionId) {
    return document.querySelector('[data-note-id="' + questionId + '"]');
  }

  // 1枚のカードだけ作り直す。
  // （全部作り直すと、ほかのカードに書きかけの文章が消えてしまうため）
  function refreshNoteItem(questionId) {
    var question = findNoteQuestion(questionId);
    var oldItem = findNoteItem(questionId);
    if (question && oldItem) {
      oldItem.parentNode.replaceChild(createNoteItem(question), oldItem);
    }
  }

  // 「保存しました」の表示を消す（別のカードを操作したときに呼ぶ）
  function clearNoteSavedMessage() {
    var previousId = noteSavedId;
    noteSavedId = null;
    if (previousId) {
      refreshNoteItem(previousId);
    }
  }


  /* -------------------------------------------------------------------
     G. 質問リスト全体の表示
     ------------------------------------------------------------------- */
  function renderNoteList() {
    var listEl = document.getElementById('note-list');
    listEl.innerHTML = ''; // 作り直すときのために、いったん空にする

    // 質問が0件のとき
    if (noteQuestions.length === 0) {
      listEl.appendChild(createNoteElement('li', 'note-empty', '質問は、まだ準備中です。'));
      return;
    }

    noteQuestions.forEach(function (question) {
      listEl.appendChild(createNoteItem(question));
    });
  }


  /* -------------------------------------------------------------------
     H. 画面が読み込まれたら実行
     ------------------------------------------------------------------- */
  document.addEventListener('DOMContentLoaded', function () {
    loadNoteAnswers();    // 保存済みの答えを読み込む
    renderNoteProgress(); // 進捗バーを表示
    renderNoteList();     // 質問カードを表示
  });

})();
