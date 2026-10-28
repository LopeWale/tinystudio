import { SimpleTable } from "../../components/studio/data-table";
import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type CSSProperties,
} from "react";
import { useResource, useStudio } from "../../app/StudioContext";
import { Button } from "../../components/studio/primitives";
import { cx } from "../../components/boardui/utils/cx";

// Preserve a successful response only while its exact resource and user/project
// scope are unchanged. Never show another cohort as this resource's stale data.
export function useRetainedResource<T>(path: string) {
  const resource = useResource<T>(path),
    { scopeKey } = useStudio();
  const key = JSON.stringify([scopeKey, path]);
  const [saved, setSaved] = useState<{ key: string; data: T }>();
  useEffect(() => {
    if (resource.data !== undefined) setSaved({ key, data: resource.data });
  }, [key, resource.data]);
  const isStale = resource.data === undefined && saved?.key === key;
  return {
    ...resource,
    data: resource.data ?? (isStale ? saved?.data : undefined),
    isStale,
  };
}

// Selection owns a retained set of records; inspection owns one visible identity.
export function RecordComparison({
  records,
  fields,
  onRemove,
  onClose,
}: {
  records: any[];
  fields: { label: string; value: (record: any) => string }[];
  onRemove: (record: any) => void;
  onClose: () => void;
}) {
  return (
    <section className="records-comparison" aria-label="Compare records">
      <header className="records-section-heading">
        <div>
          <h2>Compare records</h2>
          <p>{records.length} selected · shared fields stay aligned</p>
        </div>
        <Button onClick={onClose}>Back to collection</Button>
      </header>
      {records.length < 2 && (
        <p role="status">
          Select at least two records to compare. Your selection is retained.
        </p>
      )}
      <div className="data-actions" aria-label="Compared records">
        {records.map((record) => (
          <Button
            key={record.id ?? record.name}
            size="sm"
            variant="ghost"
            onClick={() => onRemove(record)}
          >
            Remove {record.name ?? record.id}
          </Button>
        ))}
      </div>
      <SimpleTable
        label="Record comparison"
        rows={fields}
        getRowId={(field) => field.label}
        columns={[
          {
            id: "field",
            label: "Field",
            value: (field) => field.label,
            render: (field) => (
              <>
                {field.label}
                {new Set(records.map(field.value)).size > 1 && (
                  <span className="records-field-note">Different</span>
                )}
              </>
            ),
          },
          ...records.map((record) => ({
            id: String(record.id ?? record.name),
            label: record.name ?? `Datapoint ${record.id}`,
            value: (field: (typeof fields)[number]) => field.value(record),
            render: (field: (typeof fields)[number]) => (
              <div className="records-compare-value">{field.value(record)}</div>
            ),
          })),
        ]}
        rowProps={(field) => ({
          className: cx(
            new Set(records.map(field.value)).size > 1 && "records-difference",
          ),
        })}
      />
    </section>
  );
}
export function RecordsScroll({
  children,
  label,
}: {
  children: ReactNode;
  label: string;
}) {
  const [scrolled, setScrolled] = useState(0);
  return (
    <div className="records-scroll-wrap">
      <div
        className="records-scroll-fade"
        aria-hidden="true"
        style={{ "--records-scroll": scrolled } as CSSProperties}
      >
        <i />
        <i />
        <i />
      </div>
      <div
        className="records-scroll"
        role="region"
        aria-label={label}
        tabIndex={0}
        onScroll={(event) =>
          setScrolled(Math.min(event.currentTarget.scrollTop / 24, 1))
        }
      >
        {children}
      </div>
    </div>
  );
}

export function RecordsInspector({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    if (window.matchMedia?.("(max-width: 850px)").matches) ref.current?.focus();
  }, []);
  return (
    <aside
      ref={ref}
      className="data-inspector records-local-inspector"
      aria-label={title}
      tabIndex={-1}
    >
      <header className="data-inspector-heading">
        <h2>{title}</h2>
        <Button size="sm" variant="ghost" onClick={onClose}>
          Back to results
        </Button>
      </header>
      <RecordsScroll label={`${title} detail`}>
        <div className="records-inspector-body">{children}</div>
      </RecordsScroll>
    </aside>
  );
}
