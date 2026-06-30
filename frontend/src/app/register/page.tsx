import { AuthCard } from "@/components/auth/auth-card";
import { RegisterForm } from "@/components/auth/register-form";

export default function RegisterPage() {
  return (
    <AuthCard
      title="Create account"
      description="Start a protected session for source code review."
    >
      <RegisterForm />
    </AuthCard>
  );
}
