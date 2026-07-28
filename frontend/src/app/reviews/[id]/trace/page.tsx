"use client";

import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

import { TraceEventList, TraceTokenSummary } from "@/components/reviews/ai-trace-log";
import { ReviewWorkspaceTabs } from "@/components/reviews/review-workspace-tabs";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { TechnicalDetails } from "@/components/ui/technical-details";
import { getApiErrorMessage } from "@/lib/api-error";
import { getReviewJobAiTrace } from "@/lib/review-jobs";
import type { AITrace } from "@/types/review-job";

const POLLING_INTERVAL_MS = 4_000;

export default function ReviewTracePage() {
  const params = useParams<{ id: string }>();
  const jobId = params.id;
  const [trace, setTrace] = useState<AITrace | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [eventType, setEventType] = useState("");
  const [status, setStatus] = useState("");

  const loadTrace = useCallback(async () => {
    setIsLoading(true);
    try {
      setTrace(await getReviewJobAiTrace(jobId));
      setError(null);
    } catch (requestError) {
      setError(getApiErrorMessage(requestError, "Unable to load trace."));
    } finally {
      setIsLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    void loadTrace();
    const intervalId = window.setInterval(() => void loadTrace(), POLLING_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [loadTrace]);

  const filteredEvents = useMemo(() => {
    return (trace?.events ?? []).filter((event) => {
      const matchesType = eventType ? event.event_type === eventType : true;
      const matchesStatus = status ? event.status === status : true;
      return matchesType && matchesStatus;
    });
  }, [eventType, status, trace?.events]);

  const eventTypes = useMemo(
    () => uniqueSorted((trace?.events ?? []).map((event) => event.event_type)),
    [trace?.events],
  );
  const statuses = useMemo(
    () => uniqueSorted((trace?.events ?? []).map((event) => event.status)),
    [trace?.events],
  );

  return (
    <>
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <Button asChild size="sm" variant="ghost">
          <Link href={`/reviews/${jobId}`}>
            <ArrowLeft aria-hidden="true" />
            Review Job
          </Link>
        </Button>
        <Button disabled={isLoading} onClick={() => void loadTrace()}>
          <RefreshCw aria-hidden="true" />
          Refresh
        </Button>
      </div>

      <ReviewWorkspaceTabs activeTab="trace" jobId={jobId} />

      {error ? (
        <Card>
          <CardContent className="p-6">
            <p className="text-sm text-destructive">{error}</p>
          </CardContent>
        </Card>
      ) : null}

      {trace ? (
        <>
          <TechnicalDetails
            description="Input, output, total, and estimated embedding token counts."
            title="Token usage"
          >
            <TraceTokenSummary trace={trace} />
          </TechnicalDetails>

          <Card>
            <CardHeader>
              <CardTitle>Event Filters</CardTitle>
              <CardDescription>
                Narrow the trace without hiding the raw event payloads.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-3">
              <TraceSelect
                label="Event type"
                onChange={setEventType}
                options={eventTypes}
                value={eventType}
              />
              <TraceSelect
                label="Status"
                onChange={setStatus}
                options={statuses}
                value={status}
              />
              <div className="flex items-end">
                <Button
                  onClick={() => {
                    setEventType("");
                    setStatus("");
                  }}
                  variant="secondary"
                >
                  Clear filters
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Events</CardTitle>
              <CardDescription>
                Showing {filteredEvents.length} of {trace.events.length} events.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <TraceEventList events={filteredEvents} />
            </CardContent>
          </Card>
        </>
      ) : null}
    </>
  );
}

function TraceSelect({
  label,
  onChange,
  options,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  options: string[];
  value: string;
}) {
  return (
    <div className="grid gap-2">
      <label className="text-sm font-medium">{label}</label>
      <select
        className="h-10 rounded-md border border-input bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        onChange={(event) => onChange(event.target.value)}
        value={value}
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option}
          </option>
        ))}
      </select>
    </div>
  );
}

function uniqueSorted(values: string[]) {
  return Array.from(new Set(values)).sort((left, right) =>
    left.localeCompare(right),
  );
}
