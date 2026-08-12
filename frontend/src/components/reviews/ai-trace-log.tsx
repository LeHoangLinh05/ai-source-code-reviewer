"use client";

import { Activity, ChevronDown } from "lucide-react";

import type { AITrace, AIToolCallTrace } from "@/types/review-job";

export function TraceTokenSummary({ trace }: { trace: AITrace }) {
  const totals = trace.token_totals;

  return (
    <section className="grid gap-3 md:grid-cols-4">
      <TraceStat label="Input tokens" value={totals.input_tokens} />
      <TraceStat label="Output tokens" value={totals.output_tokens} />
      <TraceStat label="Total tokens" value={totals.total_tokens} />
      <TraceStat
        label="Estimated embed tokens"
        value={totals.estimated_input_tokens}
      />
    </section>
  );
}

export function TraceEventList({
  events,
  isCompact = false,
}: {
  events: AIToolCallTrace[];
  isCompact?: boolean;
}) {
  if (events.length === 0) {
    return (
      <p className="rounded-md border border-dashed border-border bg-background p-4 text-sm text-muted-foreground">
        No trace events have been recorded yet.
      </p>
    );
  }

  return (
    <ol className="grid gap-3">
      {events.map((event, index) => (
        <TraceEventRow
          event={event}
          isCompact={isCompact}
          key={`${event.event_type}-${event.sequence}-${event.called_at}-${index}`}
        />
      ))}
    </ol>
  );
}

function TraceEventRow({
  event,
  isCompact,
}: {
  event: AIToolCallTrace;
  isCompact: boolean;
}) {
  return (
    <li className="overflow-hidden rounded-md border border-border bg-background">
      <details className="group/event">
        <summary className="flex cursor-pointer list-none items-center gap-3 p-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
          <Activity
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground"
          />
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-2 text-sm font-semibold text-foreground">
              <span className="uppercase text-muted-foreground">
                {event.event_type}
              </span>
              <span className="text-xs font-normal text-muted-foreground">
                #{event.sequence}
              </span>
            </span>
            <span className="mt-1 block truncate font-mono text-xs text-muted-foreground">
              {event.tool_name}
            </span>
          </span>
          <span className="inline-flex h-8 shrink-0 items-center rounded-md border border-border px-2 text-xs font-semibold capitalize">
            {event.status}
          </span>
          <ChevronDown
            aria-hidden="true"
            className="size-4 shrink-0 text-muted-foreground transition-transform duration-150 group-open/event:rotate-180"
          />
        </summary>

        <div className="border-t border-border p-4">
          <p className="text-xs text-muted-foreground">
            {formatDate(event.called_at)} · {event.duration_ms}ms
            {event.provider ? ` · ${event.provider}` : ""}
            {event.model ? ` · ${event.model}` : ""}
          </p>
          {event.phase ? (
            <p className="mt-1 text-xs text-muted-foreground">{event.phase}</p>
          ) : null}

          {event.token_usage ? (
            <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
              {Object.entries(event.token_usage).map(([key, value]) => (
                <span
                  className="rounded-md border border-border px-2 py-1"
                  key={key}
                >
                  {key.replaceAll("_", " ")}: {value}
                </span>
              ))}
            </div>
          ) : null}

          <TraceEventSummary event={event} />

          {!isCompact ? (
            <div className="mt-3 grid gap-3 lg:grid-cols-3">
              <TraceJsonBlock
                emptyMessage={emptyTraceMessage(event, "input")}
                label="Input"
                value={event.input}
              />
              <TraceJsonBlock
                emptyMessage={emptyTraceMessage(event, "output")}
                label="Output"
                value={event.output}
              />
              <TraceJsonBlock label="Metadata" value={event.metadata} />
            </div>
          ) : null}
        </div>
      </details>
    </li>
  );
}

function TraceEventSummary({ event }: { event: AIToolCallTrace }) {
  if (event.event_type !== "llm") {
    return null;
  }

  const inputSummary = llmInputSummary(event.input);
  const outputSummary = llmOutputSummary(event.output);
  if (!inputSummary && !outputSummary) {
    return (
      <p className="mt-3 rounded-md border border-amber-400/40 bg-amber-400/10 p-3 text-xs leading-5 text-amber-800 dark:text-amber-100">
        This LLM call was recorded before request/response summaries were captured.
        Duration and token usage still came from the provider response.
      </p>
    );
  }

  return (
    <div className="mt-3 grid gap-3 lg:grid-cols-2">
      {inputSummary ? (
        <TraceSummaryPanel label="Request summary" summary={inputSummary} />
      ) : null}
      {outputSummary ? (
        <TraceSummaryPanel label="Response summary" summary={outputSummary} />
      ) : null}
    </div>
  );
}

function TraceSummaryPanel({
  label,
  summary,
}: {
  label: string;
  summary: TraceSummary;
}) {
  return (
    <div className="rounded-md border border-border bg-muted/20 p-3">
      <p className="text-xs font-semibold uppercase text-muted-foreground">
        {label}
      </p>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        {summary.detail}
      </p>
      {summary.preview ? (
        <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-md bg-background p-2 font-mono text-xs leading-5 text-foreground">
          {summary.preview}
        </pre>
      ) : null}
    </div>
  );
}

function TraceJsonBlock({
  emptyMessage,
  label,
  value,
}: {
  emptyMessage?: string;
  label: string;
  value: Record<string, unknown>;
}) {
  const isEmpty = Object.keys(value).length === 0;

  return (
    <details className="min-w-0 rounded-md bg-muted/30 p-3" open={false}>
      <summary className="cursor-pointer text-xs font-semibold uppercase text-muted-foreground">
        {label}
      </summary>
      {isEmpty && emptyMessage ? (
        <p className="mt-2 text-xs leading-5 text-muted-foreground">
          {emptyMessage}
        </p>
      ) : (
        <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-xs leading-5 text-muted-foreground">
          {JSON.stringify(value, null, 2)}
        </pre>
      )}
    </details>
  );
}

function TraceStat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border bg-background p-4">
      <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
      <p className="mt-2 text-2xl font-semibold tracking-normal">{value}</p>
    </div>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(new Date(value));
}

type TraceSummary = {
  detail: string;
  preview: string | null;
};

function llmInputSummary(value: Record<string, unknown>): TraceSummary | null {
  if (Object.keys(value).length === 0) {
    return null;
  }

  if (value.kind === "messages") {
    const messages = Array.isArray(value.messages) ? value.messages : [];
    const previews = messages
      .map((message) => messagePreview(message))
      .filter((preview): preview is string => Boolean(preview));

    return {
      detail: `${value.message_count ?? messages.length} messages${
        value.truncated_messages ? `, ${value.truncated_messages} truncated` : ""
      }`,
      preview: previews.join("\n\n") || null,
    };
  }

  return {
    detail: traceSizeDetail(value),
    preview: typeof value.preview === "string" ? value.preview : null,
  };
}

function llmOutputSummary(value: Record<string, unknown>): TraceSummary | null {
  if (Object.keys(value).length === 0) {
    return null;
  }

  const content = value.content;
  if (isRecord(content)) {
    return {
      detail: traceSizeDetail(content),
      preview: typeof content.preview === "string" ? content.preview : null,
    };
  }

  return {
    detail: traceSizeDetail(value),
    preview:
      typeof value.content_preview === "string"
        ? value.content_preview
        : typeof value.preview === "string"
          ? value.preview
          : null,
  };
}

function messagePreview(value: unknown) {
  if (!isRecord(value)) {
    return null;
  }
  const role = typeof value.role === "string" ? value.role : "message";
  const content = value.content;
  if (!isRecord(content) || typeof content.preview !== "string") {
    return null;
  }
  return `${role}: ${content.preview}`;
}

function traceSizeDetail(value: Record<string, unknown>) {
  const size =
    typeof value.size_chars === "number" ? `${value.size_chars} chars` : "captured";
  const sha = typeof value.sha256 === "string" ? ` · sha256 ${value.sha256.slice(0, 12)}` : "";
  return `${size}${sha}`;
}

function emptyTraceMessage(
  event: AIToolCallTrace,
  side: "input" | "output",
) {
  if (event.event_type !== "llm") {
    return undefined;
  }

  return side === "input"
    ? "Request summary was not captured for this older LLM event. New LLM events include message counts, size, hash, and preview."
    : "Response summary was not captured for this older LLM event. Token usage and duration still indicate that the model returned a response.";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
