import { createConversations } from "./conversations.js?v=82e74d4b5ecb";
import { createContext } from "./context.js?v=82e74d4b5ecb";
import { createChat } from "./chat.js?v=82e74d4b5ecb";
import { createAssistantView } from "./view.js?v=82e74d4b5ecb";

// Construct without network activity; mounting/scope activation starts synchronization.
export function createAssistant({ state, http, feedback, dialogs, shell, navigation, ensureOverview, pollTasks }) {
  let view;
  const conversation = createConversations({ state, http, presentation: { refresh: () => view.assistRefreshUI(), remote: () => view.assistRenderRemote(), scopeChanged: () => view.assistScopeChanged() } });
  const ASSIST = conversation.state;
  const context = createContext({ state, assistantState: ASSIST, navigation, redraw: (...args) => view.renderAssistant(...args) });
  const chat = createChat({ state, assistantState: ASSIST, http, feedback, conversation, context, refresh: () => view.assistRefreshUI(), pollTasks });
  view = createAssistantView({ state, assistantState: ASSIST, http, feedback, dialogs, shell, conversation, context, chat, ensureOverview });
  function beginRender(route = location.hash || "#/") {
    conversation.syncAssistantScope();
    state.pageContext = { route, project: state.project, api: [] };
    ASSIST.selection = null; context.clearRefs();
    return state.pageContext;
  }
  return { state: ASSIST, context, conversation, chat, view, beginRender, syncAssistantScope: (...args) => conversation.syncAssistantScope(...args), pageAssistant: (...args) => view.pageAssistant(...args), mountAssistant: (...args) => view.mountAssistant(...args), destroy() { conversation.destroy(); context.destroy(); view.destroy(); } };
}
