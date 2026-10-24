export function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " err" : ""); el.textContent = message;
  document.body.appendChild(el); setTimeout(() => el.remove(), isError ? 9000 : 3500);
}

// Each application owns its feedback boundary, including guarded error delivery.
export function createFeedback() {
  const feedback = {
    toast,
    guard: (fn) => async (...args) => {
      try { return await fn(...args); }
      catch (e) { if (e.name !== "StaleRenderError") feedback.toast(e.message, true); }
    },
  };
  return feedback;
}

// Standalone dialogs retain the same default browser feedback helpers.
export const guard = createFeedback().guard;
