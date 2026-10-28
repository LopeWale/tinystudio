import { Button } from "../../components/studio/primitives";
import "./assistant.css";
import type {
  Attachment,
  Message,
  Source,
  ToolCall,
} from "../../controllers/conversation";

/** TinyStudio-owned patterns composed with the free BoardUI visual system. */
export function AssistantActivity({
  calls = [],
  searches = [],
  uiRequests = [],
}: {
  calls?: readonly ToolCall[];
  searches?: NonNullable<Message["searches"]>;
  uiRequests?: Array<{
    id?: string;
    action: string;
    state?: string;
    error?: string;
  }>;
}) {
  const searchSteps = new Map<
    string,
    { record: NonNullable<Message["searches"]>[number]; states: string[] }
  >();
  searches.forEach((search, index) => {
    const key = search.id || `search-${index}`,
      previous = searchSteps.get(key);
    searchSteps.set(key, {
      record: {
        ...previous?.record,
        ...Object.fromEntries(
          Object.entries(search).filter(([, value]) => value !== undefined),
        ),
      } as NonNullable<Message["searches"]>[number],
      states: [...(previous?.states || []), search.state],
    });
  });
  const phase = (call: ToolCall) =>
    call.output === undefined
      ? "running"
      : (typeof call.output === "object" &&
            call.output !== null &&
            "error" in call.output) ||
          (typeof call.output === "string" &&
            /^\s*\{\s*"error"\s*:/.test(call.output))
        ? "failed"
        : "completed";
  return (
    <div className="assistant-activity">
      {calls.map((call, index) => (
        <details className="assistant-operation" key={`${call.id}-${index}`}>
          <summary>
            {call.kind === "ui" ? "Interface" : "Tool"} · {call.name} ·{" "}
            {phase(call)}
          </summary>
          <pre className="text-body-2-regular">
            {JSON.stringify(
              {
                input: call.input,
                ...(call.output === undefined ? {} : { output: call.output }),
              },
              null,
              2,
            )}
          </pre>
        </details>
      ))}
      {Array.from(searchSteps, ([id, { record: search, states }]) => (
        <details key={id}>
          <summary>Web search · {search.state}</summary>
          {search.query && <p>{search.query}</p>}
          {search.error && <p role="alert">{search.error}</p>}
          {states.length > 1 && (
            <ol aria-label="Observed search states">
              {states.map((state, index) => (
                <li key={index}>{state}</li>
              ))}
            </ol>
          )}
        </details>
      ))}
      {uiRequests.map((request, index) => (
        <details key={`ui-${request.id}-${index}`}>
          <summary>
            Interface · {request.action} · {request.state || "requested"}
          </summary>
          {request.error && <p role="alert">{request.error}</p>}
        </details>
      ))}
    </div>
  );
}
export function AssistantAttachments({
  attachments,
  onRemove,
  onDelete,
  disabled,
}: {
  attachments: readonly Attachment[];
  onRemove?: (id: string) => void;
  onDelete?: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="assistant-attachments">
      {attachments.map((attachment) => (
        <span
          className="assistant-attachment text-caption-1-regular"
          key={attachment.id}
        >
          {attachment.name} · {attachment.size.toLocaleString()} bytes ·
          retained {attachment.retained_chars.toLocaleString()} /{" "}
          {attachment.extracted_chars.toLocaleString()} characters
          {onRemove && (
            <Button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${attachment.name} from message`}
              onClick={() => onRemove(attachment.id)}
            >
              Remove from message
            </Button>
          )}
          {onDelete && (
            <Button
              type="button"
              disabled={disabled}
              aria-label={`Delete upload ${attachment.name}`}
              onClick={() => onDelete(attachment.id)}
            >
              Delete upload
            </Button>
          )}
        </span>
      ))}
    </div>
  );
}
export function AssistantSources({ sources }: { sources: readonly Source[] }) {
  const safe = sources.filter((source) => /^https?:\/\//.test(source.url));
  return safe.length ? (
    <ul aria-label="Web sources">
      {safe.map((source, index) => (
        <li key={`${source.url}-${index}`}>
          <a href={source.url} target="_blank" rel="noopener noreferrer">
            {source.title || source.url}
          </a>
        </li>
      ))}
    </ul>
  ) : null;
}
