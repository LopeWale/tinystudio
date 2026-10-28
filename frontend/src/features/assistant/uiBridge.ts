import type { ComponentActionRegistry } from "../../controllers/componentActions";
import type { EvidenceRegistry } from "../../app/evidence";
const sensitive = /password|token|secret|credential|api.?key|authorization/i;
/** References belong to the current route and registered DOM, never a CSS selector supplied by a model. */
export class AssistantUiBridge {
  private refs = new Map<string, Element>();
  private route = "";
  constructor(
    private runtime: {
      navigate(hash: string): void;
      evidence: EvidenceRegistry;
      actions?: ComponentActionRegistry;
    },
  ) {}
  private privateRoute() {
    return /^#\/(?:api-keys|settings|users)(?:[/?]|$)/.test(location.hash);
  }
  private blocked(element: Element) {
    return (
      !!element.closest(
        "[data-assistant-surface],[data-assistant-sensitive],input[type=password]",
      ) ||
      sensitive.test(
        ["id", "name", "aria-label", "placeholder"]
          .map((key) => element.getAttribute(key) || "")
          .join(" "),
      )
    );
  }
  /** Recheck retained panes at use time; connection alone does not imply availability. */
  private visible(element: Element) {
    if (
      !element.isConnected ||
      element.getAttribute("type") === "hidden" ||
      element.closest('[hidden],[inert],[aria-hidden="true"]') ||
      !element.getClientRects().length
    )
      return false;
    for (
      let ancestor: Element | null = element;
      ancestor;
      ancestor = ancestor.parentElement
    ) {
      const style = getComputedStyle(ancestor);
      if (
        style.display === "none" ||
        style.visibility === "hidden" ||
        style.visibility === "collapse"
      )
        return false;
    }
    return true;
  }
  private disabled(element: Element) {
    return (
      element.matches(":disabled") ||
      element.hasAttribute("disabled") ||
      !!element.closest('[aria-disabled="true"]')
    );
  }
  private label(element: Element) {
    return (
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.getAttribute("placeholder") ||
      element.textContent ||
      element.tagName
    )
      .trim()
      .replace(/\s+/g, " ")
      .slice(0, 150);
  }
  snapshot() {
    this.refs.clear();
    this.route = location.hash;
    if (this.privateRoute())
      return {
        route: this.route,
        note: "Account and credential pages are omitted from assistant context.",
      };
    const root = document.querySelector("main,[role=main]");
    const controls: any[] = [];
    for (const element of root?.querySelectorAll(
      "button,a[href],input,select,textarea,[role=button]",
    ) || []) {
      if (this.blocked(element) || !this.visible(element)) continue;
      const ref = `control_${this.refs.size + 1}`;
      this.refs.set(ref, element);
      controls.push({
        ref,
        kind: element.tagName.toLowerCase(),
        label: this.label(element),
        disabled: this.disabled(element),
        ...(element instanceof HTMLSelectElement
          ? {
              options: Array.from(element.options)
                .slice(0, 40)
                .map((option) => ({ value: option.value, label: option.text })),
            }
          : {}),
      });
    }
    return {
      route: this.route,
      components: this.runtime.actions?.inspect(),
      controls,
      evidence: this.runtime.evidence.snapshot(),
    };
  }
  async perform(action: string, args: any, allowed: boolean): Promise<any> {
    if (action === "list_actions")
      return this.runtime.actions?.inspect() || { actions: [] };
    if (action === "execute_action") {
      if (!this.runtime.actions)
        throw new Error("Component actions unavailable");
      return this.runtime.actions.execute(args.ref, args.value, allowed);
    }
    if (["click", "fill", "select"].includes(action) && !allowed)
      throw new Error(
        "Interface changes require explicit actions/deletions opt-in for this turn",
      );
    if (action === "read_page" || action === "get_text") return this.snapshot();
    if (action === "navigate") {
      let hash = String(args.hash || "");
      if (!hash.startsWith("#/")) hash = "#/" + hash.replace(/^#?\/?/, "");
      this.refs.clear();
      this.runtime.navigate(hash);
      return {
        navigated: hash,
        note: "Read the page again after loading to obtain current controls.",
      };
    }
    if (this.privateRoute())
      throw new Error(
        "Account and credential controls must be operated directly by the user",
      );
    const element = this.refs.get(args.ref);
    if (
      this.route !== location.hash ||
      !element?.isConnected ||
      this.blocked(element) ||
      !this.visible(element) ||
      this.disabled(element)
    )
      throw new Error(
        "Control reference is stale or unavailable; read the current page again",
      );
    if (action === "click") {
      if (!(element instanceof HTMLElement))
        throw new Error("Control cannot be clicked");
      element.click();
      return { clicked: args.ref };
    }
    if (action === "fill") {
      if (
        !(
          element instanceof HTMLInputElement ||
          element instanceof HTMLTextAreaElement
        ) ||
        (element instanceof HTMLInputElement &&
          ["file", "password", "hidden"].includes(element.type))
      )
        throw new Error("Reference is not an editable text control");
      const prototype =
        element instanceof HTMLTextAreaElement
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(
        element,
        String(args.value ?? ""),
      );
      element.dispatchEvent(new Event("input", { bubbles: true }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return { filled: args.ref };
    }
    if (action === "select") {
      if (!(element instanceof HTMLSelectElement))
        throw new Error("Reference is not a select control");
      const option = Array.from(element.options).find(
        (option) =>
          option.value === String(args.value) ||
          option.text === String(args.value),
      );
      if (!option) throw new Error("Requested option is unavailable");
      Object.getOwnPropertyDescriptor(
        HTMLSelectElement.prototype,
        "value",
      )!.set!.call(element, option.value);
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return { selected: args.ref, value: option.value };
    }
    throw new Error(`Unknown interface action ${action}`);
  }
}
