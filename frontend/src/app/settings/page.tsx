"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  CalendarDays,
  GitPullRequest,
  KeyRound,
  Mail,
  Save,
  ShieldCheck,
  ShieldOff,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm, useWatch, type UseFormReturn } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { LogoutAllButton } from "@/components/auth/logout-button";
import { PasswordInput } from "@/components/auth/password-input";
import { ProviderConnectionsPanel } from "@/components/providers/provider-connections-panel";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { getApiErrorMessage } from "@/lib/api-error";
import {
  MAX_PASSWORD_LENGTH,
  strongPasswordSchema,
} from "@/lib/auth-validation";
import {
  changeCurrentUserPassword,
  getCurrentUserProfile,
  updateCurrentUserProfile,
} from "@/lib/users";
import { cn } from "@/lib/utils";
import { getUserDisplayName, getUserInitials } from "@/lib/user-display";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { setCredentials } from "@/store/slices/authSlice";
import type { User } from "@/types/auth";

const profileSchema = z.object({
  fullName: z.string().trim().max(255, "Name must be 255 characters or less."),
});

const passwordSchema = z
  .object({
    confirmPassword: z.string().min(1, "Confirm your new password."),
    currentPassword: z
      .string()
      .min(1, "Current password is required.")
      .max(MAX_PASSWORD_LENGTH, "Password is too long."),
    newPassword: strongPasswordSchema,
  })
  .refine((values) => values.newPassword === values.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  })
  .refine((values) => values.newPassword !== values.currentPassword, {
    message: "New password must be different from the current password.",
    path: ["newPassword"],
  });

type ProfileFormValues = z.infer<typeof profileSchema>;
type PasswordFormValues = z.infer<typeof passwordSchema>;
type SettingsTab = "profile" | "connections" | "password" | "sessions";

const SETTINGS_TABS: Array<{
  icon: LucideIcon;
  id: SettingsTab;
  label: string;
}> = [
  { icon: UserRound, id: "profile", label: "Profile" },
  { icon: GitPullRequest, id: "connections", label: "Connections" },
  { icon: KeyRound, id: "password", label: "Password" },
  { icon: ShieldOff, id: "sessions", label: "Sessions" },
];

export default function SettingsPage() {
  const dispatch = useAppDispatch();
  const authUser = useAppSelector((state) => state.auth.user);
  const [activeTab, setActiveTab] = useState<SettingsTab>("profile");
  const [profile, setProfile] = useState<User | null>(authUser);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [isProfileLoading, setIsProfileLoading] = useState(false);
  const profileForm = useForm<ProfileFormValues>({
    mode: "onChange",
    resolver: zodResolver(profileSchema),
    defaultValues: {
      fullName: authUser?.full_name ?? "",
    },
  });
  const passwordForm = useForm<PasswordFormValues>({
    mode: "onChange",
    resolver: zodResolver(passwordSchema),
    defaultValues: {
      confirmPassword: "",
      currentPassword: "",
      newPassword: "",
    },
  });
  const joinedAt = useMemo(
    () => (profile?.created_at ? formatDate(profile.created_at) : "Not available"),
    [profile?.created_at],
  );

  async function loadProfile() {
    setIsProfileLoading(true);
    setProfileError(null);

    try {
      const user = await getCurrentUserProfile();
      setProfile(user);
      dispatch(setCredentials({ user }));
      profileForm.reset({ fullName: user.full_name ?? "" });
    } catch (requestError) {
      setProfileError(
        getApiErrorMessage(requestError, "Unable to load profile."),
      );
    } finally {
      setIsProfileLoading(false);
    }
  }

  useEffect(() => {
    void loadProfile();
    // profileForm.reset is stable through react-hook-form's form instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleUpdateProfile(values: ProfileFormValues) {
    profileForm.clearErrors("root");

    try {
      const user = await updateCurrentUserProfile({
        full_name: values.fullName.trim() || null,
      });
      setProfile(user);
      dispatch(setCredentials({ user }));
      profileForm.reset({ fullName: user.full_name ?? "" });
      toast.success("Profile updated.");
    } catch (requestError) {
      const message = getApiErrorMessage(
        requestError,
        "Unable to update profile.",
      );
      profileForm.setError("root", { message, type: "server" });
      toast.error(message);
    }
  }

  async function handleChangePassword(values: PasswordFormValues) {
    passwordForm.clearErrors(["currentPassword", "newPassword"]);
    passwordForm.clearErrors("root");

    try {
      await changeCurrentUserPassword({
        current_password: values.currentPassword,
        new_password: values.newPassword,
      });
      passwordForm.reset();
      toast.success("Password updated.");
    } catch (requestError) {
      const message = getApiErrorMessage(
        requestError,
        "Unable to update password.",
      );
      if (message === "Current password is incorrect") {
        passwordForm.setError("currentPassword", {
          message,
          type: "server",
        });
      } else if (message === "New password must be different") {
        passwordForm.setError("newPassword", { message, type: "server" });
      } else {
        passwordForm.setError("root", { message, type: "server" });
      }
      toast.error(message);
    }
  }

  return (
    <section className="mx-auto flex w-full max-w-5xl flex-col gap-5">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-2xl font-bold tracking-normal">Settings</h1>
          <p className="mt-1 truncate text-sm text-muted-foreground">
            {profile?.email ?? "Account preferences"}
          </p>
        </div>
      </div>

      {profileError ? <ErrorNotice message={profileError} /> : null}

      <SettingsTabs activeTab={activeTab} onChange={setActiveTab} />

      {activeTab === "profile" ? (
        <ProfilePanel
          form={profileForm}
          isLoading={isProfileLoading}
          joinedAt={joinedAt}
          onSubmit={handleUpdateProfile}
          user={profile}
        />
      ) : null}

      {activeTab === "password" ? (
        <PasswordPanel form={passwordForm} onSubmit={handleChangePassword} />
      ) : null}

      {activeTab === "connections" ? <ProviderConnectionsPanel /> : null}

      {activeTab === "sessions" ? <SessionsPanel /> : null}
    </section>
  );
}

function SettingsTabs({
  activeTab,
  onChange,
}: {
  activeTab: SettingsTab;
  onChange: (tab: SettingsTab) => void;
}) {
  return (
    <div
      aria-label="Settings sections"
      className="flex flex-wrap gap-1 rounded-md border border-border bg-card p-1"
      role="tablist"
    >
      {SETTINGS_TABS.map((tab) => {
        const Icon = tab.icon;
        const isActive = activeTab === tab.id;

        return (
          <button
            aria-selected={isActive}
            className={cn(
              "flex h-10 min-w-32 flex-1 items-center justify-center gap-2 rounded-md px-3 text-sm font-semibold text-muted-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-none",
              isActive
                ? "bg-primary text-primary-foreground"
                : "hover:bg-accent hover:text-accent-foreground",
            )}
            key={tab.id}
            onClick={() => onChange(tab.id)}
            role="tab"
            type="button"
          >
            <Icon aria-hidden="true" className="size-4" />
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

function ProfilePanel({
  form,
  isLoading,
  joinedAt,
  onSubmit,
  user,
}: {
  form: UseFormReturn<ProfileFormValues>;
  isLoading: boolean;
  joinedAt: string;
  onSubmit: (values: ProfileFormValues) => Promise<void>;
  user: User | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Profile</CardTitle>
        <CardDescription>
          Account identity used across repository ownership and review jobs.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-6 lg:grid-cols-[minmax(0,0.95fr)_minmax(0,1.05fr)]">
        <div className="grid content-start gap-4">
          <div className="flex items-center gap-4 rounded-md border border-border bg-background p-4">
            <span className="flex size-14 shrink-0 items-center justify-center rounded-full bg-primary text-base font-bold text-primary-foreground">
              {getUserInitials(user)}
            </span>
            <div className="min-w-0">
              <p className="truncate text-base font-bold">
                {getUserDisplayName(user)}
              </p>
              <p className="truncate text-sm text-muted-foreground">
                {user?.email ?? "Loading profile..."}
              </p>
            </div>
          </div>

          <AccountSnapshot
            isLoading={isLoading}
            joinedAt={joinedAt}
            user={user}
          />
        </div>

        <Form {...form}>
          <form
            className="grid content-start gap-5"
            onSubmit={form.handleSubmit(onSubmit)}
          >
            <FormField
              control={form.control}
              name="fullName"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Display name</FormLabel>
                  <FormControl>
                    <Input
                      autoComplete="name"
                      placeholder="Security reviewer"
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
            <div className="flex justify-end">
              <Button
                disabled={
                  form.formState.isSubmitting ||
                  !form.formState.isDirty ||
                  !form.formState.isValid
                }
                type="submit"
              >
                <Save aria-hidden="true" />
                {form.formState.isSubmitting ? "Saving..." : "Save profile"}
              </Button>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

function PasswordPanel({
  form,
  onSubmit,
}: {
  form: UseFormReturn<PasswordFormValues>;
  onSubmit: (values: PasswordFormValues) => Promise<void>;
}) {
  const currentPassword = useWatch({
    control: form.control,
    name: "currentPassword",
  });
  const newPassword = useWatch({
    control: form.control,
    name: "newPassword",
  });

  useEffect(() => {
    if (form.getValues("confirmPassword")) {
      void form.trigger("confirmPassword");
    }
  }, [form, newPassword]);

  useEffect(() => {
    if (form.getValues("newPassword")) {
      void form.trigger("newPassword");
    }
  }, [currentPassword, form]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Password</CardTitle>
        <CardDescription>
          Change the password used for email and password sign-in.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Form {...form}>
          <form
            className="grid gap-5"
            noValidate
            onSubmit={form.handleSubmit(onSubmit)}
          >
            <FormField
              control={form.control}
              name="currentPassword"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Current password</FormLabel>
                  <FormControl>
                    <PasswordInput
                      autoComplete="current-password"
                      placeholder="Current password"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="grid gap-5 md:grid-cols-2">
              <FormField
                control={form.control}
                name="newPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>New password</FormLabel>
                    <FormControl>
                      <PasswordInput
                        autoComplete="new-password"
                        placeholder="Strong password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="confirmPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Confirm password</FormLabel>
                    <FormControl>
                      <PasswordInput
                        autoComplete="new-password"
                        placeholder="Repeat new password"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            {form.formState.errors.root?.message ? (
              <p className="text-sm font-medium text-destructive" role="alert">
                {form.formState.errors.root.message}
              </p>
            ) : null}
            <div className="flex justify-end">
              <Button
                disabled={
                  form.formState.isSubmitting ||
                  !form.formState.isDirty ||
                  !form.formState.isValid
                }
                type="submit"
              >
                <KeyRound aria-hidden="true" />
                {form.formState.isSubmitting
                  ? "Updating..."
                  : "Update password"}
              </Button>
            </div>
          </form>
        </Form>
      </CardContent>
    </Card>
  );
}

function SessionsPanel() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Sessions</CardTitle>
        <CardDescription>
          End active sessions without changing repository or review data.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4 rounded-md border border-border bg-background p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-border bg-card text-muted-foreground">
              <ShieldOff aria-hidden="true" className="size-4" />
            </span>
            <div>
              <h2 className="text-sm font-semibold">Logout all sessions</h2>
              <p className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground">
                Revoke server-side sessions on every device. This browser
                returns to login after the request succeeds.
              </p>
            </div>
          </div>
          <LogoutAllButton className="border border-rose-200 bg-rose-50 text-rose-950 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-100 dark:hover:bg-rose-950/50" />
        </div>
      </CardContent>
    </Card>
  );
}

function AccountSnapshot({
  isLoading,
  joinedAt,
  user,
}: {
  isLoading: boolean;
  joinedAt: string;
  user: User | null;
}) {
  if (isLoading && user === null) {
    return (
      <div className="grid gap-3 rounded-md border border-border bg-background p-4">
        <div className="h-5 w-40 animate-pulse rounded bg-muted" />
        <div className="h-5 w-64 max-w-full animate-pulse rounded bg-muted" />
        <div className="h-5 w-28 animate-pulse rounded bg-muted" />
        <div className="h-5 w-48 max-w-full animate-pulse rounded bg-muted" />
      </div>
    );
  }

  return (
    <dl className="grid gap-3 rounded-md border border-border bg-background p-4">
      <SnapshotRow
        icon={UserRound}
        label="Name"
        value={user?.full_name ?? "Not set"}
      />
      <SnapshotRow icon={Mail} label="Email" value={user?.email ?? "Unknown"} />
      <SnapshotRow
        icon={ShieldCheck}
        label="Role"
        value={user?.role ?? "unknown"}
      />
      <SnapshotRow icon={CalendarDays} label="Joined" value={joinedAt} />
    </dl>
  );
}

function SnapshotRow({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
}) {
  return (
    <div className="grid gap-1 sm:grid-cols-[120px_1fr]">
      <dt className="flex items-center gap-2 text-sm text-muted-foreground">
        <Icon aria-hidden="true" className="size-4" />
        {label}
      </dt>
      <dd className="min-w-0 break-all text-sm font-semibold text-foreground">
        {value}
      </dd>
    </div>
  );
}

function ErrorNotice({ message }: { message: string }) {
  return (
    <div
      className="rounded-md border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm font-medium text-destructive"
      role="alert"
    >
      {message}
    </div>
  );
}

function formatDate(value: string) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "Not available";
  }

  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}
