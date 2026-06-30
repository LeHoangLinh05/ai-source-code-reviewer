import { LogoutButton } from "@/components/auth/logout-button";

export default function DashboardPage() {
  return (
    <main className="min-h-screen bg-background px-4 py-6 sm:px-6 lg:px-8">
      <div className="flex items-center justify-end">
        <LogoutButton />
      </div>
    </main>
  );
}
