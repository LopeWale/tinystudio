interface ModalEntry {
  id: string;
  parentId: string | null;
  backdrop: HTMLElement;
  dialog: HTMLElement;
  close: () => void;
  opener: HTMLElement | null;
  fallbackFocus: HTMLElement | null;
  lastFocused: HTMLElement | null;
}
interface BackgroundState {
  inert: boolean;
  ariaHidden: string | null;
}
const selector =
  'a[href],button,input:not([type="hidden"]),select,textarea,[tabindex],[contenteditable="true"]';
/** Owns the single document-level focus/scroll lease shared by all open dialogs.
 * Layer state is restored from snapshots, including pre-existing inert regions. */
export class ModalStack {
  private entries: ModalEntry[] = [];
  private background = new Map<HTMLElement, BackgroundState>();
  private overflow: { value: string; priority: string } | null = null;
  private observer: MutationObserver | null = null;
  constructor(private document: Document) {}
  isTop(id: string) {
    return this.entries.at(-1)?.id === id;
  }
  private focusable(entry: ModalEntry) {
    return [...entry.dialog.querySelectorAll<HTMLElement>(selector)].filter(
      (node) => {
        if (
          (node as HTMLButtonElement).disabled ||
          node.tabIndex < 0 ||
          node.closest("[hidden],[inert]")
        )
          return false;
        for (
          let current: HTMLElement | null = node;
          current;
          current = current.parentElement
        ) {
          const style = this.document.defaultView?.getComputedStyle(current);
          if (style?.display === "none" || style?.visibility === "hidden")
            return false;
          if (current === entry.dialog) break;
        }
        return true;
      },
    );
  }
  private focus(entry: ModalEntry) {
    const options = this.focusable(entry);
    const target =
      entry.lastFocused?.isConnected && options.includes(entry.lastFocused)
        ? entry.lastFocused
        : (options.find((node) => node.hasAttribute("autofocus")) ??
          options[0] ??
          entry.dialog);
    target.focus();
  }
  private keydown = (event: KeyboardEvent) => {
    const top = this.entries.at(-1);
    if (!top || event.defaultPrevented) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      top.close();
    } else if (event.key === "Tab") {
      const options = this.focusable(top),
        index = options.indexOf(this.document.activeElement as HTMLElement);
      if (
        !options.length ||
        index < 0 ||
        (!event.shiftKey && index === options.length - 1) ||
        (event.shiftKey && index === 0)
      ) {
        event.preventDefault();
        (
          options[event.shiftKey ? options.length - 1 : 0] ?? top.dialog
        ).focus();
      }
    }
  };
  private focusin = (event: FocusEvent) => {
    const top = this.entries.at(-1);
    if (!top) return;
    if (top.dialog.contains(event.target as Node))
      top.lastFocused = event.target as HTMLElement;
    else this.focus(top);
  };
  private restore(node: HTMLElement, state: BackgroundState) {
    node.inert = state.inert;
    node.toggleAttribute("inert", state.inert);
    if (state.ariaHidden === null) node.removeAttribute("aria-hidden");
    else node.setAttribute("aria-hidden", state.ariaHidden);
  }
  private syncBackground() {
    const top = this.entries.at(-1);
    for (const child of this.document.body.children) {
      if (!(child instanceof HTMLElement)) continue;
      if (child === top?.backdrop) {
        const previous = this.background.get(child);
        if (previous) {
          this.restore(child, previous);
          this.background.delete(child);
        }
        continue;
      }
      if (!this.background.has(child))
        this.background.set(child, {
          inert: child.inert || child.hasAttribute("inert"),
          ariaHidden: child.getAttribute("aria-hidden"),
        });
      child.inert = true;
      child.setAttribute("inert", "");
      child.setAttribute("aria-hidden", "true");
    }
  }
  register({
    id,
    parentId = null,
    backdrop,
    dialog,
    close,
    fallbackFocus = null,
  }: {
    id: string;
    parentId?: string | null;
    backdrop: HTMLElement;
    dialog: HTMLElement;
    close: () => void;
    fallbackFocus?: HTMLElement | null;
  }) {
    const entry: ModalEntry = {
      id,
      parentId,
      backdrop,
      dialog,
      close,
      fallbackFocus,
      opener: this.document.activeElement as HTMLElement | null,
      lastFocused: null,
    };
    if (!this.entries.length) {
      this.overflow = {
        value: this.document.body.style.getPropertyValue("overflow"),
        priority: this.document.body.style.getPropertyPriority("overflow"),
      };
      this.document.body.style.setProperty("overflow", "hidden");
      this.document.addEventListener("keydown", this.keydown);
      this.document.addEventListener("focusin", this.focusin, true);
      this.observer = new MutationObserver(() => {
        if (this.entries.length) this.syncBackground();
      });
      this.observer.observe(this.document.body, { childList: true });
    }
    const childIndex = this.entries.findIndex((child) => child.parentId === id);
    if (childIndex < 0) this.entries.push(entry);
    else {
      entry.opener = this.entries[childIndex].opener;
      this.entries.splice(childIndex, 0, entry);
    }
    this.syncBackground();
    if (this.isTop(id)) this.focus(entry);
    let registered = true;
    return () => {
      if (!registered) return;
      registered = false;
      const index = this.entries.indexOf(entry);
      if (index < 0) return;
      const wasTop = index === this.entries.length - 1;
      this.entries.splice(index, 1);
      for (const child of this.entries) {
        if (entry.backdrop.contains(child.opener)) child.opener = entry.opener;
      }
      if (this.entries.length) this.syncBackground();
      else {
        this.observer?.disconnect();
        this.observer = null;
        this.document.removeEventListener("keydown", this.keydown);
        this.document.removeEventListener("focusin", this.focusin, true);
        for (const [node, previous] of this.background)
          this.restore(node, previous);
        this.background.clear();
        if (this.overflow?.value)
          this.document.body.style.setProperty(
            "overflow",
            this.overflow.value,
            this.overflow.priority,
          );
        else this.document.body.style.removeProperty("overflow");
        this.overflow = null;
      }
      if (wasTop) {
        const top = this.entries.at(-1),
          destination = [entry.opener, entry.fallbackFocus].find(
            (node) =>
              node?.isConnected &&
              !node.matches(":disabled") &&
              !node.closest("[inert],[hidden]") &&
              (!top || top.dialog.contains(node)),
          );
        if (destination) destination.focus();
        else if (top) this.focus(top);
      }
    };
  }
}
const documents = new WeakMap<Document, ModalStack>();
export function modalStack(document: Document) {
  let stack = documents.get(document);
  if (!stack) {
    stack = new ModalStack(document);
    documents.set(document, stack);
  }
  return stack;
}
