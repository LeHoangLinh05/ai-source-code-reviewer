"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import {
  KeyRound,
  Mail,
  RefreshCw,
  Save,
  ShieldCheck,
  ShieldOff,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { LogoutAllButton } from "@/components/auth/logout-button";
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
  changeCurrentUserPassword,
  getCurrentUserProfile,
  updateCurrentUserProfile,
} from "@/lib/users";
import { useAppDispatch, useAppSelector } from "@/store/hooks";
import { setCredentials } from "@/store/slices/authSlice";
import type { User } from "@/types/auth";

const profileSchema = z.object({
  fullName: z.string().trim().max(255, "Name must be 255 characters or less."),
});

const passwordSchema = z
  .object({
    confirmPassword: z.string().min(8, "Confirm your new password."),
    currentPassword: z.string().min(1, "Current password is required."),
    newPassword: z.string().min(8, "Password must be at least 8 characters."),
  })
  .refine((values) => values.newPassword === values.confirmPassword, {
    message: "Passwords do not match.",
    path: ["confirmPassword"],
  });

type ProfileFormValues = z.infer<typeof profileSchema>;
type PasswordFormValues = z.infer<typeof passwordSchema>;

export default function SettingsPage() {
  const dispatch = useAppDispatch();
  const authUser = useAppSelector((state) => state.auth.user);
  const [profile, setProfile] = useState<User | null>(authUser);
  const [profileError, setProfileError] = useState<string | null>(null);
  const [isProfileLoading, setIsProfileLoading] = useState(false);
  const profileForm = useForm<ProfileFormValues>({
    resolver: zodResolver(profileSchema),
    defaultValues: {
      fullName: authUser?.full_name ?? "",
    },
  });
  const passwordForm = useForm<PasswordFormValues>({
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
      passwordForm.setError("root", { message, type: "server" });
      toast.error(message);
    }
  }

  return (
    <>
      <div className="flex justify-end">
        <Button
          disabled={isProfileLoading}
          onClick={() => void loadProfile()}
          variant="secondary"
        >
          <RefreshCw aria-hidden="true" />
          Refresh Profile
        </Button>
      </div>

      {profileError ? (
        <Card>
          <CardContent className="p-5">
            <p className="text-sm text-destructive">{profileError}</p>
          </CardContent>
        </Card>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-[0.9fr_1.1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
            <CardDescription>
              Account identity used across repository ownership and review jobs.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-5">
            <AccountSnapshot
              isLoading={isProfileLoading}
              joinedAt={joinedAt}
              user={profile}
            />
            <Form {...profileForm}>
              <form
                className="grid gap-5"
                onSubmit={profileForm.handleSubmit(handleUpdateProfile)}
              >
                <FormField
                  control={profileForm.control}
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
                {profileForm.formState.errors.root?.message ? (
                  <p className="text-sm font-medium text-destructive">
                    {profileForm.formState.errors.root.message}
                  </p>
                ) : null}
                <div className="flex justify-end">
                  <Button
                    disabled={
                      profileForm.formState.isSubmitting ||
                      !profileForm.formState.isDirty
                    }
                    type="submit"
                  >
                    <Save aria-hidden="true" />
                    Save Profile
                  </Button>
                </div>
              </form>
            </Form>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Password</CardTitle>
            <CardDescription>
              Change the password used for email and password sign-in.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Form {...passwordForm}>
              <form
                className="grid gap-5"
                onSubmit={passwordForm.handleSubmit(handleChangePassword)}
              >
                <FormField
                  control={passwordForm.control}
                  name="currentPassword"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Current password</FormLabel>
                      <FormControl>
                        <Input
                          autoComplete="current-password"
                          placeholder="Current password"
                          type="password"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <div className="grid gap-5 md:grid-cols-2">
                  <FormField
                    control={passwordForm.control}
                    name="newPassword"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>New password</FormLabel>
                        <FormControl>
                          <Input
                            autoComplete="new-password"
                            placeholder="Minimum 8 characters"
                            type="password"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={passwordForm.control}
                    name="confirmPassword"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Confirm password</FormLabel>
                        <FormControl>
                          <Input
                            autoComplete="new-password"
                            placeholder="Repeat new password"
                            type="password"
                            {...field}
                          />
                        </FormControl>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </div>
                {passwordForm.formState.errors.root?.message ? (
                  <p className="text-sm font-medium text-destructive">
                    {passwordForm.formState.errors.root.message}
                  </p>
                ) : null}
                <div className="flex justify-end">
                  <Button
                    disabled={passwordForm.formState.isSubmitting}
                    type="submit"
                  >
                    <KeyRound aria-hidden="true" />
                    Update Password
                  </Button>
                </div>
              </form>
            </Form>
          </CardContent>
        </Card>
      </section>

      <Card>
        <CardHeader>
          <CardTitle>Session Management</CardTitle>
          <CardDescription>
            End active sessions without changing repository or review data.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="rounded-md border border-border bg-background p-4">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-md border border-border bg-card text-muted-foreground">
                  <ShieldOff aria-hidden="true" className="size-4" />
                </span>
                <div>
                  <h2 className="text-sm font-semibold">Logout all sessions</h2>
                  <p className="mt-1 max-w-xl text-[15px] leading-6 text-muted-foreground">
                    Revoke server-side sessions on every device. This browser
                    returns to login after the request succeeds.
                  </p>
                </div>
              </div>
              <LogoutAllButton className="border border-rose-200 bg-rose-50 text-rose-950 hover:bg-rose-100 dark:border-rose-900/60 dark:bg-rose-950/30 dark:text-rose-100 dark:hover:bg-rose-950/50" />
            </div>
          </div>
        </CardContent>
      </Card>
    </>
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
        <div className="h-5 w-64 animate-pulse rounded bg-muted" />
        <div className="h-5 w-28 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  return (
    <div className="grid gap-3 rounded-md border border-border bg-background p-4">
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
      <SnapshotRow icon={KeyRound} label="Joined" value={joinedAt} />
    </div>
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
    <div className="grid gap-2 sm:grid-cols-[140px_1fr]">
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

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
