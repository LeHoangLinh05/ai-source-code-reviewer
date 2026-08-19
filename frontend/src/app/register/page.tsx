import { AuthCard } from "@/components/auth/auth-card";
import { GuestOnly } from "@/components/auth/guest-only";
import { RegisterForm } from "@/components/auth/register-form";

export default function RegisterPage() {
  return (
    <GuestOnly>
      <AuthCard
        title="Sign up"
        description="Start a protected session for source code review."
      >
        <RegisterForm />
      </AuthCard>
    </GuestOnly>
  );
}
