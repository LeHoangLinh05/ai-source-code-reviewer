import { Suspense } from "react";

import { AuthCard } from "@/components/auth/auth-card";
import { LoginForm } from "@/components/auth/login-form";

export default function LoginPage() {
  return (
    <AuthCard
      title="Sign in"
      description="Access your RepoGuard AI security review workspace."
    >
      <Suspense fallback={<div className="text-sm text-muted-foreground">Loading...</div>}>
        <LoginForm />
      </Suspense>
    </AuthCard>
  );
}
