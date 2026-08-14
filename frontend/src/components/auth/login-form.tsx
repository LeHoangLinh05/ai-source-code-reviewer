"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { PasswordInput } from "@/components/auth/password-input";
import { Button } from "@/components/ui/button";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { publishAuthEvent } from "@/lib/auth-events";
import {
  emailSchema,
  loginPasswordSchema,
} from "@/lib/auth-validation";
import { setSessionMarker } from "@/lib/session-marker";
import { useAppDispatch } from "@/store/hooks";
import { setCredentials } from "@/store/slices/authSlice";
import type { AuthTokenResponse, LoginPayload } from "@/types/auth";

const loginSchema = z.object({
  email: emailSchema,
  password: loginPasswordSchema,
});

type LoginFormValues = z.infer<typeof loginSchema>;

export function LoginForm() {
  const dispatch = useAppDispatch();
  const router = useRouter();
  const searchParams = useSearchParams();
  const nextPath = searchParams.get("next") ?? "/dashboard";
  const form = useForm<LoginFormValues>({
    mode: "onChange",
    resolver: zodResolver(loginSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  });

  async function onSubmit(values: LoginFormValues) {
    form.clearErrors("root");

    try {
      const payload: LoginPayload = values;
      const response = await api.post<AuthTokenResponse>("/auth/login", payload, {
        skipAuthRefresh: true,
      });

      dispatch(
        setCredentials({
          user: response.data.user,
        }),
      );
      setSessionMarker();
      publishAuthEvent("session-updated");
      toast.success("Signed in.");
      router.replace(nextPath);
    } catch (error) {
      const errorMessage = getApiErrorMessage(error, "Unable to sign in.");

      form.setError("root", {
        message: errorMessage,
        type: "server",
      });
      toast.error(errorMessage);
    }
  }

  return (
    <Form {...form}>
      <form
        className="space-y-5"
        noValidate
        onSubmit={form.handleSubmit(onSubmit)}
      >
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input
                  autoComplete="email"
                  placeholder="you@example.com"
                  type="email"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        <FormField
          control={form.control}
          name="password"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Password</FormLabel>
              <FormControl>
                <PasswordInput
                  autoComplete="current-password"
                  placeholder="********"
                  {...field}
                />
              </FormControl>
              <FormMessage />
            </FormItem>
          )}
        />
        {form.formState.errors.root?.message ? (
          <p className="text-sm font-medium text-destructive" role="alert">
            {form.formState.errors.root.message}
          </p>
        ) : null}
        <Button
          className="w-full"
          disabled={form.formState.isSubmitting || !form.formState.isValid}
          type="submit"
        >
          {form.formState.isSubmitting ? "Signing in..." : "Sign in"}
        </Button>
        <p className="text-center text-sm text-muted-foreground">
          New to RepoGuard AI?{" "}
          <Link className="font-medium text-primary hover:underline" href="/register">
            Create an account
          </Link>
        </p>
      </form>
    </Form>
  );
}
