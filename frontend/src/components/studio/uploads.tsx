import { Button } from "./primitives";
import "./uploads.css";
import { FileUpload } from "../boardui/base/file-upload/file-upload";
import { useId, useState } from "react";
import { cx } from "../boardui/utils/cx";
import {
  validateFiles,
  type UploadPhase,
  type UploadProgress,
} from "../../controllers/uploads";

export interface FileUploadFieldProps {
  label: string;
  accept?: string;
  multiple?: boolean;
  files: File[];
  onChange(files: File[]): void;
  maxBytes?: number;
  disabled?: boolean;
  presentation?: "dropzone" | "compact";
}
export function FileUploadField({
  label,
  accept,
  multiple,
  files,
  onChange,
  maxBytes,
  disabled,
  presentation = "dropzone",
}: FileUploadFieldProps) {
  const id = useId();
  const [errors, setErrors] = useState<string[]>([]);
  const select = (incoming: File[]) => {
    const checked = validateFiles(incoming, accept, maxBytes);
    setErrors(checked.filter((x) => x.error).map((x) => x.error));
    const valid = checked.filter((x) => !x.error).map((x) => x.file);
    onChange(
      multiple
        ? [...files, ...valid].filter(
            (file, index, all) =>
              all.findIndex(
                (x) =>
                  x.name === file.name &&
                  x.size === file.size &&
                  x.lastModified === file.lastModified,
              ) === index,
          )
        : valid.slice(0, 1),
    );
  };
  return (
    <div
      className={cx(
        "upload-field",
        presentation === "compact" &&
          "upload-field--compact grid min-w-0 gap-2",
      )}
    >
      <label
        htmlFor={id}
        className={
          presentation === "compact"
            ? "text-body-2-medium text-text-primary"
            : undefined
        }
      >
        {label}
      </label>
      <FileUpload
        inputId={id}
        label={label}
        onFilesSelected={select}
        allowedExtensions={(accept || "")
          .split(",")
          .filter((x) => x.startsWith("."))
          .map((x) => x.slice(1))}
        accept={accept}
        multiple={multiple}
        maxBytes={maxBytes ?? Number.MAX_SAFE_INTEGER}
        disabled={disabled}
        presentation={presentation}
      />
      {maxBytes !== undefined && (
        <p
          className={
            presentation === "compact"
              ? "text-caption-1-regular text-text-tertiary"
              : undefined
          }
        >
          Up to {maxBytes.toLocaleString()} bytes each
        </p>
      )}
      {errors.map((error) => (
        <p
          role="alert"
          key={error}
          className={
            presentation === "compact"
              ? "text-body-2-regular text-text-error-primary"
              : undefined
          }
        >
          {error}
        </p>
      ))}
      <ul
        hidden={!files.length}
        className={
          presentation === "compact" ? "grid min-w-0 gap-2" : undefined
        }
      >
        {files.map((file, i) => (
          <li
            key={`${file.name}-${file.lastModified}-${i}`}
            className={
              presentation === "compact"
                ? "flex min-w-0 items-center justify-between gap-2 text-body-2-regular text-text-secondary"
                : undefined
            }
          >
            <span
              className={
                presentation === "compact" ? "min-w-0 break-all" : undefined
              }
            >
              {file.name} · {file.size.toLocaleString()} bytes
            </span>{" "}
            <Button
              type="button"
              disabled={disabled}
              aria-label={`Remove ${file.name}`}
              onClick={() => onChange(files.filter((_, index) => index !== i))}
            >
              Remove
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
export interface UploadStatusProps {
  phase: UploadPhase | string;
  progress?: UploadProgress | number;
  error?: string;
  onCancel?(): void;
  onRetry?(): void;
}
export function UploadStatus({
  phase,
  progress,
  error,
  onCancel,
  onRetry,
}: UploadStatusProps) {
  const measured =
    typeof progress === "object" && progress.total && progress.total > 0
      ? Math.min(100, (progress.loaded / progress.total) * 100)
      : typeof progress === "number"
        ? progress
        : undefined;
  return (
    <div className="upload-status" aria-live="polite">
      <span>
        {phase === "importing"
          ? "Transferred · importing on server"
          : phase === "indexing"
            ? "Transferred · indexing on server"
            : phase}
      </span>
      {phase === "uploading" && (
        <progress aria-label="Bytes transferred" value={measured} max={100} />
      )}{" "}
      {typeof progress === "object" && (
        <span>
          {progress.loaded.toLocaleString()}
          {progress.total ? ` / ${progress.total.toLocaleString()}` : ""} bytes
        </span>
      )}
      {error && <p role="alert">{error}</p>}
      {onCancel && ["uploading", "importing", "indexing"].includes(phase) && (
        <Button type="button" onClick={onCancel}>
          Cancel
        </Button>
      )}
      {onRetry && ["failed", "cancelled"].includes(phase) && (
        <Button type="button" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}
