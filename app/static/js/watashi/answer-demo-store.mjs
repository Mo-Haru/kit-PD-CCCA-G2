// Only fixture IDs are edited; unrelated active and historical answers survive.
export function mergeDemoAnswers(existing, edited, questionIds) {
  const merged = structuredClone(existing);
  for (const id of questionIds) {
    delete merged[id];
    if (Object.hasOwn(edited, id)) merged[id] = structuredClone(edited[id]);
  }
  return merged;
}

export function createDemoAnswerStore(client, questionIds) {
  let revision = null;
  let ownerId = null;
  let base = {};
  let busy = false;
  async function session() {
    const current = await client.api("/api/v1/auth/session");
    if (!current.authenticated || !client.getUnlockState().unlocked) {
      throw new Error("この画面でパスキーの鍵を解除してください。");
    }
    return current;
  }
  return {
    get revision() { return revision; },
    invalidate() { revision = null; ownerId = null; base = {}; },
    async load() {
      if (busy) throw new Error("処理が終わるまでお待ちください。");
      busy = true;
      revision = null;
      try {
        const before = await session();
        const loaded = await client.loadAnswerDocument();
        const after = await session();
        if (before.user_id !== after.user_id) throw new Error("アカウントが切り替わりました。ページを開き直してください。");
        if (loaded.document && (loaded.document.schema !== 1 || !loaded.document.items
          || typeof loaded.document.items !== "object" || Array.isArray(loaded.document.items))) {
          throw new Error("保存された回答の形式を読み込めません。");
        }
        base = structuredClone(loaded.document?.items || {});
        ownerId = after.user_id;
        revision = loaded.revision;
        return { revision, items: structuredClone(base) };
      } finally { busy = false; }
    },
    async save(edited) {
      if (busy) throw new Error("処理が終わるまでお待ちください。");
      if (revision === null) throw new Error("先に保存済みの回答を読み込んでください。");
      busy = true;
      try {
        const current = await session();
        if (current.user_id !== ownerId) {
          revision = null;
          throw new Error("アカウントが切り替わりました。入力は保存せず、ページを開き直してください。");
        }
        const items = mergeDemoAnswers(base, edited, questionIds);
        const saved = await client.saveAnswerDocument(items, revision);
        revision = saved.revision;
        base = items;
        return saved;
      } finally { busy = false; }
    },
  };
}
