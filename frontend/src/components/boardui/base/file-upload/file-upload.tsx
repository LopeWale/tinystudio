"use client";

import { useTemplateCopy } from "@/components/boardui/foundations/template-copy/template-copy";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  RiFileExcel2Line,
  RiFileImageLine,
  RiFileTextLine,
  RiUploadCloud2Line,
} from "@remixicon/react";
import { cx } from "@/components/boardui/utils/cx";

/**
 * Figma sources: Board UI → "Upload file" (node 4105:25269),
 * "Upload in progress" (node 4107:27192), and the complete state
 * (node 4107:30456).
 *
 * Interactive drag-and-drop file upload with validation, a perimeter progress
 * indicator, animated content transitions, and a completion callback. The
 * TinyStudio integration uses controlled file selection; transport progress is
 * reported separately by UploadStatus. No simulated progress is run.
 */

const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;
const DEFAULT_EXTENSIONS = ["pdf", "jpg", "jpeg", "png", "xlsx"] as const;

type UploadPhase = "idle" | "uploading" | "complete";
type StaggerState = "shown" | "hiding" | "hidden";

export interface FileUploadProps {
  /** Called after the progress and success states finish. */
  onUploadComplete?: (file: File) => void;
  /** TinyStudio adapter: selection is not transfer success. No demonstration timers run. */
  onFilesSelected?: (files: File[]) => void;
  multiple?: boolean;
  disabled?: boolean;
  accept?: string;
  inputId?: string;
  label?: string;
  /** Compact horizontal chooser for grouped desktop forms. Selection mechanics are shared. */
  presentation?: "dropzone" | "compact";
  /** Accepted filename extensions without dots. */
  allowedExtensions?: readonly string[];
  /** Maximum accepted file size in bytes. */
  maxBytes?: number;
  /** Overrides the icon shown while a file uploads. */
  renderFileIcon?: (file: File) => ReactNode;
  className?: string;
}

export function formatFileSize(bytes: number) {
  if (bytes >= 1024 * 1024) {
    const mb = bytes / (1024 * 1024);
    return `${mb >= 10 ? Math.round(mb) : Math.round(mb * 10) / 10} MB`;
  }
  return `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

function extensionFor(fileName: string) {
  return fileName.split(".").pop()?.toLowerCase() ?? "";
}

function DefaultFileIcon({ file }: { file: File }) {
  const extension = extensionFor(file.name);
  const Icon = extension === "xlsx"
    ? RiFileExcel2Line
    : ["jpg", "jpeg", "png"].includes(extension)
      ? RiFileImageLine
      : RiFileTextLine;

  return <Icon className="size-6 shrink-0 text-foreground-icon-secondary" aria-hidden />;
}

/** Drives the Transitions.dev text-reveal states defined in globals.css. */
function useStaggerState(active: boolean): StaggerState {
  const [prevActive, setPrevActive] = useState(active);
  const [hiding, setHiding] = useState(false);

  if (prevActive !== active) {
    setPrevActive(active);
    setHiding(!active);
  }

  useEffect(() => {
    if (!hiding) return;
    const timer = setTimeout(() => setHiding(false), 200);
    return () => clearTimeout(timer);
  }, [hiding]);

  return active ? "shown" : hiding ? "hiding" : "hidden";
}

const staggerContainer = (state: StaggerState) =>
  cx("t-stagger", state === "shown" && "is-shown", state === "hiding" && "is-hiding");

const staggerLine = (state: StaggerState) =>
  cx(
    state === "shown" &&
      "translate-y-0 opacity-100 blur-0 [transition:opacity_var(--stagger-dur)_var(--stagger-ease),translate_var(--stagger-dur)_var(--stagger-ease),filter_var(--stagger-dur)_var(--stagger-ease)]",
    state === "hiding" && "translate-y-0 opacity-0 blur-0 [transition:opacity_200ms_ease]",
    state === "hidden" && "translate-y-3 opacity-0 blur-[3px] transition-none",
  );

/** Rounded-rect progress path beginning at top-center and running clockwise. */
function ringPath(width: number, height: number, inset: number, radius: number) {
  const x0 = inset;
  const y0 = inset;
  const x1 = width - inset;
  const y1 = height - inset;
  const arc = (endX: number, endY: number) =>
    `A ${radius} ${radius} 0 0 1 ${endX} ${endY}`;

  return [
    `M ${width / 2} ${y0}`,
    `H ${x1 - radius}`,
    arc(x1, y0 + radius),
    `V ${y1 - radius}`,
    arc(x1 - radius, y1),
    `H ${x0 + radius}`,
    arc(x0, y1 - radius),
    `V ${y0 + radius}`,
    arc(x0 + radius, y0),
    "Z",
  ].join(" ");
}

export function FileUpload({
  onUploadComplete, onFilesSelected, multiple, disabled, accept, inputId, label,
  presentation = "dropzone",
  allowedExtensions = DEFAULT_EXTENSIONS,
  maxBytes = DEFAULT_MAX_BYTES,
  renderFileIcon,
  className,
}: FileUploadProps) {
  const localize = useTemplateCopy();
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [progress, setProgress] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [rejection, setRejection] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const boxRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ width: 533, height: 164 });
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    const element = boxRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0) setBox({ width, height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  const startUpload = (nextFile: File) => {
    const extension = extensionFor(nextFile.name);
    if (!allowedExtensions.map((value) => value.toLowerCase()).includes(extension)) {
      setRejection(`Only ${allowedExtensions.map((value) => value.toUpperCase()).join(", ")} files are supported`);
      timers.current.push(setTimeout(() => setRejection(null), 2600));
      return;
    }
    if (nextFile.size > maxBytes) {
      setRejection(`That file is larger than ${formatFileSize(maxBytes)}`);
      timers.current.push(setTimeout(() => setRejection(null), 2600));
      return;
    }

    // A selection callback never implies that bytes were uploaded or imported.
    if (onFilesSelected) onFilesSelected([nextFile]);
    else onUploadComplete?.(nextFile);
  };

  const busy = phase !== "idle";
  const blocked = busy || disabled;
  const progressPath = ringPath(box.width, box.height, 1, 15);
  const idleReveal = useStaggerState(!busy);
  const busyReveal = useStaggerState(busy);
  const uploadingLine = useStaggerState(phase === "uploading");
  const completeLine = useStaggerState(phase === "complete");
  const allowedLabel = allowedExtensions
    .filter((extension) => extension.toLowerCase() !== "jpeg")
    .map((extension) => extension.toUpperCase())
    .join(", ");

  return localize(
    <div
      ref={boxRef}
      role="button"
      tabIndex={blocked ? -1 : 0}
      aria-label={label || "Upload a file"}
      aria-disabled={disabled}
      onClick={() => !blocked && inputRef.current?.click()}
      onKeyDown={(event) => {
        if (!blocked && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          inputRef.current?.click();
        }
      }}
      onDragOver={(event) => {
        event.preventDefault();
        if (!blocked) setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragOver(false);
        const droppedFile = event.dataTransfer.files?.[0];
        if (!blocked && onFilesSelected) onFilesSelected(Array.from(event.dataTransfer.files).slice(0, multiple ? undefined : 1));
        else if (droppedFile && !blocked) startUpload(droppedFile);
      }}
      className={cx(
        "group relative w-full shrink-0 rounded-2xl outline-none",
        presentation === "compact" ? "h-20" : "h-[164px]",
        "transition-colors duration-300 ease-out",
        busy ? "bg-background-primary-default" : "cursor-pointer bg-background-secondary-default",
        "focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-border-focus-ring",
        className,
      )}
    >
      <input
        ref={inputRef}
        id={inputId}
        type="file"
        aria-label={label || "Choose a file to upload"}
        multiple={multiple}
        disabled={disabled}
        accept={accept ?? allowedExtensions.map((extension) => `.${extension}`).join(",")}
        className="sr-only"
        tabIndex={-1}
        onChange={(event) => {
          const selected = Array.from(event.target.files || []);
          const selectedFile = selected[0];
          event.target.value = "";
          if (onFilesSelected) onFilesSelected(selected);
          else if (selectedFile) startUpload(selectedFile);
        }}
      />

      <div
        aria-hidden
        className={cx(
          "pointer-events-none absolute inset-0 rounded-2xl border-2 border-dashed",
          dragOver
            ? "border-border-button-active"
            : "border-border-checkbox-default group-hover:border-border-button-active",
          "transition-[opacity,border-color] duration-300 ease-out",
          busy ? "opacity-0" : "opacity-100",
        )}
      />

      <svg
        aria-hidden
        viewBox={`0 0 ${box.width} ${box.height}`}
        preserveAspectRatio="none"
        className={cx(
          "pointer-events-none absolute inset-0 size-full transition-opacity duration-300 ease-out",
          busy ? "opacity-100" : "opacity-0",
        )}
      >
        <rect
          x={1}
          y={1}
          width={box.width - 2}
          height={box.height - 2}
          rx={15}
          fill="none"
          stroke="var(--color-border-button-default)"
          strokeWidth={2}
        />
        <path
          d={progressPath}
          fill="none"
          stroke="var(--color-accent-400)"
          strokeWidth={2}
          strokeLinecap="round"
          pathLength={100}
          strokeDasharray={`${busy ? (progress >= 100 ? 102 : progress) : 0} 200`}
          className="transition-[stroke-dasharray] duration-200 ease-linear"
        />
      </svg>

      <div
        aria-hidden
        className={cx(
          "absolute -top-[9.5px] left-1/2 -translate-x-1/2 rounded-md bg-accent-400 px-1.5 py-0.5",
          "text-caption-1-medium whitespace-nowrap text-white tabular-nums",
          "[transition:opacity_160ms_ease-out,filter_160ms_ease-out,translate_300ms_ease-out]",
          busy ? "translate-y-0 opacity-100 blur-0" : "-translate-y-2.5 opacity-0 blur-[2px]",
        )}
      >
        {progress}%
      </div>

      <div
        className={cx(
          "absolute inset-0 flex items-center",
          presentation === "compact"
            ? "justify-start gap-3 px-4"
            : "flex-col justify-center gap-3.5",
          staggerContainer(idleReveal),
          busy && "pointer-events-none",
        )}
      >
        <span className="t-stagger-line t-stagger-line--1 flex size-10 shrink-0 items-center justify-center rounded-full bg-file-upload-icon-background p-2.5">
          <RiUploadCloud2Line
            className="size-6 shrink-0 text-file-upload-icon-foreground transition-colors duration-150 ease group-hover:text-file-upload-icon-foreground-hover"
            aria-hidden
          />
        </span>
        <div className={cx(
          "flex min-w-0 flex-col",
          presentation === "compact" ? "items-start gap-1 text-start" : "items-center gap-2 text-center",
        )}>
          <p
            className={cx(
              "t-stagger-line t-stagger-line--2",
              presentation === "compact" ? "text-body-2-medium" : "text-body-medium",
              rejection ? "text-text-error-primary" : "text-text-secondary",
            )}
          >
            {rejection ?? (
              <>
                {presentation === "compact" ? "Drop files here or " : "Drag and drop to upload or "}<span className="text-accent-500">select</span>
              </>
            )}
          </p>
          <p className={cx(
            "t-stagger-line t-stagger-line--3 text-text-tertiary",
            presentation === "compact" ? "text-caption-1-regular" : "text-body-2-regular",
          )}>
            {allowedLabel || accept || "Files"}{maxBytes < Number.MAX_SAFE_INTEGER ? ` (max ${formatFileSize(maxBytes)})` : ""}
          </p>
        </div>
      </div>

      <div
        hidden={!busy}
        style={{display: busy ? undefined : 'none'}}
        aria-hidden={!busy}
        className={cx(
          "absolute inset-0 flex flex-col items-center justify-center",
          staggerContainer(busyReveal),
          !busy && "pointer-events-none",
        )}
      >
        <span className="t-stagger-line t-stagger-line--1 flex size-10 items-center justify-center rounded-full border border-border-button-default bg-background-primary-default p-2">
          {file && (renderFileIcon ? renderFileIcon(file) : <DefaultFileIcon file={file} />)}
        </span>
        <p className="t-stagger-line t-stagger-line--2 mt-3.5 max-w-[90%] truncate text-body-medium text-text-primary">
          {file?.name}
        </p>
        <div className="t-stagger-line t-stagger-line--3 relative mt-1 h-[18px] w-full">
          <p
            className={cx(
              "absolute inset-x-0 text-center text-body-2-regular text-text-secondary",
              staggerLine(uploadingLine),
            )}
          >
            Uploading {file ? formatFileSize(file.size) : ""}
            ...
          </p>
          <p
            className={cx(
              "absolute inset-x-0 text-center text-body-2-regular text-text-secondary",
              staggerLine(completeLine),
            )}
          >
            Uploaded successfully!
          </p>
        </div>
      </div>
    </div>
  );
}
