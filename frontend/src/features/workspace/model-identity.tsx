import "./model-identity.css";

/** Repository identity is presentation only; callers retain the exact API ID. */
export function ModelIdentity({ id, alias }: { id: string; alias?: string }) {
  const slash = id.indexOf("/");
  const publisher = slash > 0 ? id.slice(0, slash) : "";
  const name = slash > 0 ? id.slice(slash + 1) : id;
  const customAlias = alias && alias !== id;
  return (
    <span className="model-identity" title={id} aria-label={id}>
      <bdi className="model-identity__name">{customAlias ? alias : name}</bdi>
      {(customAlias || publisher) && (
        <bdi className="model-identity__source">
          {customAlias ? id : publisher}
        </bdi>
      )}
    </span>
  );
}
