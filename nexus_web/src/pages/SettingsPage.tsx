import { t, useLocale } from "../localization";
import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Loader2, Save } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { useAuth } from "../app/AuthContext";
import { useApplicationDistribution } from "../app/distribution";
import { OrganizationSettings } from "../components/OrganizationSettings";
import { StatusBadge } from "../components/Badge";
import { Field } from "../components/Form";
import { NexilumeDialog, NexilumeTabs } from "../components/NexilumeControls";
import { ReadOnlyValue, LoadingState, ErrorState, InlineIssue } from "../components/SettingsPrimitives";
import { api, type ApiContext } from "../lib/api";
import { formatDate } from "../lib/format";
import type { AccountProfile } from "../lib/types";

type SettingsSection = "personal" | "organization";
type ProfileForm = { display_name: string; phone: string; company: string };
const emptyProfile: ProfileForm = { display_name: "", phone: "", company: "" };

export function SettingsPage() {
  useLocale();
  const auth = useAuth();
  const organizationSettings = useApplicationDistribution().organizationSettings;
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [profileForm, setProfileForm] = useState<ProfileForm>(emptyProfile);
  const [savedProfile, setSavedProfile] = useState<ProfileForm>(emptyProfile);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const rawSection = searchParams.get("section");
  const activeSection: SettingsSection =
    rawSection === "organization" && organizationSettings ? "organization" : "personal";

  const profile = useQuery({
    queryKey: ["account-profile", auth.apiContext],
    queryFn: () => api.accountProfile(auth.apiContext),
    enabled: auth.isContextReady,
  });

  useEffect(() => {
    if (rawSection === "developer" || searchParams.get("tab") === "developer") {
      if (organizationSettings) navigate(organizationSettings.legacyDeveloperPath, { replace: true });
      else setSearchParams({ section: "personal" }, { replace: true });
      return;
    }
    if (rawSection === "profile" || (rawSection === "organization" && !organizationSettings)) {
      const next = new URLSearchParams(searchParams);
      next.set("section", "personal");
      if (!organizationSettings) next.delete("project");
      setSearchParams(next, { replace: true });
    }
  }, [navigate, organizationSettings, rawSection, searchParams, setSearchParams]);

  useEffect(() => {
    if (!profile.data) return;
    const next = {
      display_name: profile.data.display_name || "",
      phone: profile.data.phone || "",
      company: profile.data.company || "",
    };
    setProfileForm(next);
    setSavedProfile(next);
  }, [profile.data]);

  const profileDirty = useMemo(
    () => JSON.stringify(profileForm) !== JSON.stringify(savedProfile),
    [profileForm, savedProfile],
  );
  useUnsavedChanges(profileDirty);

  const updateProfile = useMutation({
    mutationFn: () => api.updateAccountProfile(auth.apiContext, profileForm),
    onSuccess: async (updated) => {
      const next = {
        display_name: updated.display_name || "",
        phone: updated.phone || "",
        company: updated.company || "",
      };
      setProfileForm(next);
      setSavedProfile(next);
      toast.success(t("Profile updated"));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["account-profile"] }),
        queryClient.invalidateQueries({ queryKey: ["whoami"] }),
      ]);
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : t("Failed to update profile"),
      ),
  });

  function selectSection(section: SettingsSection) {
    if (section === activeSection) return;
    if (
      profileDirty &&
      !window.confirm(t("Discard your unsaved profile changes?"))
    )
      return;
    const next = new URLSearchParams(searchParams);
    next.set("section", section);
    if (section === "personal") next.delete("project");
    setSearchParams(next);
  }

  return (
    <div className="grid gap-5">
      <header>
        <p className="tech-label">{organizationSettings ? t("ACCOUNT AND STRUCTURE") : t("ACCOUNT")}</p>
        <h1 className="mt-1 text-2xl font-semibold text-ink">{t("Settings")}</h1>
        <p className="mt-1 text-sm text-muted">
          {organizationSettings ? t("Keep your personal identity separate from Organization and Project administration.") : t("Manage your personal profile and account security.")}
        </p>
      </header>

      <NexilumeTabs
        label={t("Settings sections")}
        idBase="settings-section"
        value={activeSection}
        onChange={selectSection}
        options={[
          { value: "personal", label: t("Personal") },
          ...(organizationSettings ? [{ value: "organization" as const, label: t("Organization") }] : []),
        ]}
      />

      {activeSection === "personal" ? (
        <section
          id="settings-section-panel-personal"
          role="tabpanel"
          aria-labelledby="settings-section-tab-personal"
        >
          <PersonalSection
            profile={profile.data}
            loading={profile.isLoading}
            error={profile.error}
            form={profileForm}
            setForm={setProfileForm}
            dirty={profileDirty}
            saving={updateProfile.isPending}
            onRetry={() => void profile.refetch()}
            onSave={() => updateProfile.mutate()}
            onChangePassword={() => setPasswordOpen(true)}
          />
        </section>
      ) : (
        <section
          id="settings-section-panel-organization"
          role="tabpanel"
          aria-labelledby="settings-section-tab-organization"
        >
          <OrganizationSettings />
        </section>
      )}

      <ChangePasswordDialog
        open={passwordOpen}
        apiContext={auth.apiContext}
        onClose={() => setPasswordOpen(false)}
      />
    </div>
  );
}

function PersonalSection({
  profile,
  loading,
  error,
  form,
  setForm,
  dirty,
  saving,
  onRetry,
  onSave,
  onChangePassword,
}: {
  profile?: AccountProfile;
  loading: boolean;
  error: Error | null;
  form: ProfileForm;
  setForm: Dispatch<SetStateAction<ProfileForm>>;
  dirty: boolean;
  saving: boolean;
  onRetry: () => void;
  onSave: () => void;
  onChangePassword: () => void;
}) {
  useLocale();
  if (loading) return <LoadingState label={t("Loading personal settings")} />;
  if (error && !profile) {
    return (
      <ErrorState
        title={t("Personal settings could not be loaded")}
        error={error}
        onRetry={onRetry}
      />
    );
  }
  return (
    <div className="grid gap-5">
      {error ? (
        <InlineIssue
          title={t("Profile status may be out of date")}
          error={error}
          onRetry={onRetry}
        />
      ) : null}
      <section
        className="panel p-5 sm:p-6"
        aria-labelledby="personal-identity-title"
      >
        <div className="flex flex-col gap-2 border-b border-line pb-4 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <h2 id="personal-identity-title" className="font-semibold text-ink">{t("Personal identity")}</h2>
            <p className="mt-1 text-sm text-muted">{t("Information shown to collaborators across Nexilume AI.")}</p>
          </div>
          <StatusBadge status={profile?.status || "active"} />
        </div>
        <dl className="grid gap-3 border-b border-line py-4 text-sm sm:grid-cols-3">
          <ReadOnlyValue label={t("Email")} value={profile?.email || "—"} />
          <ReadOnlyValue label={t("Username")} value={profile?.username || "—"} />
          <ReadOnlyValue
            label={t("Last sign-in")}
            value={formatDate(profile?.last_login_at)}
          />
        </dl>
        <form
          className="grid gap-4 pt-5"
          onSubmit={(event) => {
            event.preventDefault();
            onSave();
          }}
        >
          <div className="grid gap-4 md:grid-cols-2">
            <Field label={t("Display name")}>
              <input
                className="input"
                maxLength={255}
                value={form.display_name}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    display_name: event.target.value,
                  }))
                }
              />
            </Field>
            <Field label={t("Phone")}>
              <input
                className="input"
                maxLength={64}
                value={form.phone}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    phone: event.target.value,
                  }))
                }
              />
            </Field>
            <Field label={t("Company")}>
              <input
                className="input"
                maxLength={255}
                value={form.company}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    company: event.target.value,
                  }))
                }
              />
            </Field>
          </div>
          <div className="flex min-h-10 items-center justify-between gap-3 border-t border-line pt-4">
            <span className="text-sm text-muted">
              {dirty ? t("Unsaved changes") : t("Your profile is up to date.")}
            </span>
            <button
              className="btn btn-primary min-h-10"
              disabled={!dirty || saving}
            >
              {saving ? (
                <Loader2 size={16} className="animate-spin" />
              ) : (
                <Save size={16} />
              )}{t("Save changes")}</button>
          </div>
        </form>
      </section>

      <section
        className="panel flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between sm:p-6"
        aria-labelledby="account-security-title"
      >
        <div>
          <h2 id="account-security-title" className="font-semibold text-ink">{t("Account security")}</h2>
          <p className="mt-1 text-sm text-muted">{t("Change your password without leaving the current signed-in session.")}</p>
        </div>
        <button className="btn min-h-10 shrink-0" onClick={onChangePassword}>
          <KeyRound size={16} />{t("Change password")}</button>
      </section>
    </div>
  );
}

function ChangePasswordDialog({
  open,
  apiContext,
  onClose,
}: {
  open: boolean;
  apiContext: ApiContext;
  onClose: () => void;
}) {
  useLocale();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState("");
  useEffect(() => {
    if (!open) return;
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
    setErrorMessage("");
  }, [open]);
  const mutation = useMutation({
    mutationFn: () =>
      api.changePassword(apiContext, {
        current_password: currentPassword,
        new_password: newPassword,
        new_password_confirm: confirmPassword,
      }),
    onSuccess: () => {
      toast.success(t("Password changed"));
      onClose();
    },
    onError: (error) =>
      setErrorMessage(
        error instanceof Error
          ? error.message
          : "Password could not be changed",
      ),
  });
  const mismatch = Boolean(confirmPassword && newPassword !== confirmPassword);
  const disabled =
    !currentPassword ||
    !newPassword ||
    !confirmPassword ||
    mismatch ||
    mutation.isPending;
  return (
    <NexilumeDialog
      open={open}
      title={t("Change password")}
      description={t("Your current browser session remains signed in.")}
      busy={mutation.isPending}
      onClose={onClose}
      footer={
        <>
          <button
            className="btn min-h-11"
            onClick={onClose}
            disabled={mutation.isPending}
          >{t("Cancel")}</button>
          <button
            className="btn btn-primary min-h-11"
            disabled={disabled}
            onClick={() => mutation.mutate()}
          >
            {mutation.isPending ? (
              <Loader2 size={16} className="animate-spin" />
            ) : (
              <KeyRound size={16} />
            )}{t("Change password")}</button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label={t("Current password")}>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
          />
        </Field>
        <Field label={t("New password")}>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
          />
        </Field>
        <Field label={t("Confirm new password")}>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            onChange={(event) => setConfirmPassword(event.target.value)}
          />
        </Field>
        {mismatch ? (
          <p className="text-sm text-danger" role="alert">{t("New passwords do not match.")}</p>
        ) : null}
        {errorMessage ? (
          <p
            className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900"
            role="alert"
          >
            {errorMessage}
          </p>
        ) : null}
      </div>
    </NexilumeDialog>
  );
}

function useUnsavedChanges(dirty: boolean) {
  useEffect(() => {
    if (!dirty) return;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    const linkGuard = (event: MouseEvent) => {
      const target = event.target as Element | null;
      const link = target?.closest("a[href]") as HTMLAnchorElement | null;
      if (
        !link ||
        link.target === "_blank" ||
        link.origin !== window.location.origin
      )
        return;
      if (!window.confirm(t("Discard your unsaved profile changes?"))) {
        event.preventDefault();
        event.stopPropagation();
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    document.addEventListener("click", linkGuard, true);
    return () => {
      window.removeEventListener("beforeunload", beforeUnload);
      document.removeEventListener("click", linkGuard, true);
    };
  }, [dirty]);
}
