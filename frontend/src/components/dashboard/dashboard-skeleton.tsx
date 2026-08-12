import { Card, CardContent, CardHeader } from "@/components/ui/card";

function Bar({ className }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-muted ${className ?? ""}`} />;
}

function ListCardSkeleton({ rows = 4 }: { rows?: number }) {
  return (
    <Card>
      <CardHeader className="space-y-2">
        <Bar className="h-5 w-40" />
        <Bar className="h-4 w-56" />
      </CardHeader>
      <CardContent className="grid gap-2.5">
        {Array.from({ length: rows }).map((_, index) => (
          <Bar className="h-14 w-full" key={index} />
        ))}
      </CardContent>
    </Card>
  );
}

export function DashboardSkeleton() {
  return (
    <>
      {/* Risk posture */}
      <Card>
        <CardContent className="grid gap-6 p-6">
          <div className="flex gap-4">
            <Bar className="size-14 shrink-0" />
            <div className="w-full space-y-2">
              <Bar className="h-3 w-24" />
              <Bar className="h-8 w-72 max-w-full" />
              <Bar className="h-4 w-full max-w-md" />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, index) => (
              <Bar className="h-16 w-full" key={index} />
            ))}
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] xl:items-start">
        <div className="grid gap-4">
          <ListCardSkeleton rows={4} />
          <ListCardSkeleton rows={2} />
          <ListCardSkeleton rows={5} />
        </div>
        <div className="grid gap-4">
          <ListCardSkeleton rows={3} />
          <ListCardSkeleton rows={4} />
        </div>
      </div>
    </>
  );
}
