import {
  Body1,
  Button,
  Caption1,
  Card,
  Divider,
  Field,
  Input,
  MessageBar,
  MessageBarBody,
  Spinner,
  Tab,
  TabList,
  Text,
  Title2,
} from "@fluentui/react-components";
import { Key20Regular, PersonAdd20Regular, SignOut20Regular } from "@fluentui/react-icons";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";
import { Navigate, useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { api, type AuthProvider, type AuthState } from "../api";
import { useAuth, useAuthChanged } from "../auth";
import { usePageTitle } from "../components/ui";
import { LanguageMenu, useI18n } from "../i18n";
import { ThemeMenu } from "../theme";

/** Centered card used by every page shown before signing in. */
function AuthFrame({ title, children }: { title: string; children: ReactNode }) {
  usePageTitle(title);
  return (
    <div className="auth-page">
      <div className="auth-corner">
        <ThemeMenu />
        <LanguageMenu />
      </div>
      <Card className="auth-card">
        <Text weight="semibold" size={500}>
          🦊 Media Streamer
        </Text>
        <Title2 as="h1">{title}</Title2>
        {children}
      </Card>
    </div>
  );
}

function ErrorBar({ error }: { error: unknown }) {
  if (!error) return null;
  return (
    <MessageBar intent="error">
      <MessageBarBody>{error instanceof Error ? error.message : String(error)}</MessageBarBody>
    </MessageBar>
  );
}

/** "Continue with Google" etc. `invite` / `next` are passed through the OAuth round trip. */
function ProviderButtons({
  providers,
  next,
  invite,
}: {
  providers: AuthProvider[];
  next?: string;
  invite?: string;
}) {
  const { t } = useI18n();
  if (!providers.length) return null;
  const query = new URLSearchParams();
  if (next) query.set("next", next);
  if (invite) query.set("invite", invite);
  const qs = query.toString();
  return (
    <div className="form-stack">
      {providers.map((p) => (
        <Button
          key={p.id}
          as="a"
          icon={<Key20Regular />}
          href={`/api/auth/oauth/${encodeURIComponent(p.id)}/start${qs ? `?${qs}` : ""}`}
        >
          {t("login.continueWith", { name: p.name })}
        </Button>
      ))}
    </div>
  );
}

function Or() {
  const { t } = useI18n();
  return <Divider>{t("login.or")}</Divider>;
}

function LoginForm({ onDone }: { onDone: (s: AuthState) => void }) {
  const { t } = useI18n();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({ mutationFn: () => api.login(email, password), onSuccess: onDone });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };
  return (
    <form className="form-stack" onSubmit={submit}>
      <Field label={t("login.email")} required>
        <Input
          type="email"
          autoComplete="username"
          value={email}
          onChange={(_, d) => setEmail(d.value)}
        />
      </Field>
      <Field label={t("login.password")} required>
        <Input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(_, d) => setPassword(d.value)}
        />
      </Field>
      <ErrorBar error={login.error} />
      <Button type="submit" appearance="primary" disabled={!email || !password || login.isPending}>
        {t("login.signIn")}
      </Button>
    </form>
  );
}

function RegisterForm({ invite, onDone }: { invite?: string; onDone: (s: AuthState) => void }) {
  const { t } = useI18n();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const register = useMutation({
    mutationFn: () => api.register({ name, email, password, invite }),
    onSuccess: onDone,
  });
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        register.mutate();
      }}
    >
      <Field label={t("login.name")}>
        <Input autoComplete="name" value={name} onChange={(_, d) => setName(d.value)} />
      </Field>
      <Field label={t("login.email")} required>
        <Input
          type="email"
          autoComplete="username"
          value={email}
          onChange={(_, d) => setEmail(d.value)}
        />
      </Field>
      <Field label={t("login.password")} hint={t("login.passwordHint")} required>
        <Input
          type="password"
          autoComplete="new-password"
          value={password}
          onChange={(_, d) => setPassword(d.value)}
        />
      </Field>
      <ErrorBar error={register.error} />
      <Button
        type="submit"
        appearance="primary"
        icon={<PersonAdd20Regular />}
        disabled={!email || password.length < 8 || register.isPending}
      >
        {t("login.createAccount")}
      </Button>
    </form>
  );
}

function SetupForm({ onDone }: { onDone: (s: AuthState) => void }) {
  const { t, rich } = useI18n();
  const [form, setForm] = useState({ name: "", email: "", password: "", code: "" });
  const set = (key: keyof typeof form) => (_: unknown, d: { value: string }) =>
    setForm((f) => ({ ...f, [key]: d.value }));
  const setup = useMutation({ mutationFn: () => api.setup(form), onSuccess: onDone });
  return (
    <form
      className="form-stack"
      onSubmit={(e) => {
        e.preventDefault();
        setup.mutate();
      }}
    >
      <Body1 className="muted">{rich("setup.intro", { file: <code>data/setup-code</code> })}</Body1>
      <Field label={t("setup.code")} required>
        <Input value={form.code} onChange={set("code")} autoComplete="one-time-code" />
      </Field>
      <Field label={t("login.name")}>
        <Input value={form.name} onChange={set("name")} autoComplete="name" />
      </Field>
      <Field label={t("login.email")} required>
        <Input type="email" value={form.email} onChange={set("email")} autoComplete="username" />
      </Field>
      <Field label={t("login.password")} hint={t("login.passwordHint")} required>
        <Input
          type="password"
          value={form.password}
          onChange={set("password")}
          autoComplete="new-password"
        />
      </Field>
      <ErrorBar error={setup.error} />
      <Button
        type="submit"
        appearance="primary"
        disabled={!form.code || !form.email || form.password.length < 8 || setup.isPending}
      >
        {t("setup.create")}
      </Button>
    </form>
  );
}

/** Sign-in (or first-run setup) in place of the page that was asked for. */
export function AuthScreen() {
  const { t } = useI18n();
  const { data: state } = useAuth();
  const changed = useAuthChanged();
  const location = useLocation();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<"login" | "register">("login");
  if (!state) return null;
  const next = location.pathname === "/login" ? (params.get("next") ?? "/") : location.pathname;
  const error = params.get("error");

  if (state.setup_required) {
    return (
      <AuthFrame title={t("setup.title")}>
        <SetupForm onDone={changed} />
        {state.providers.length > 0 && (
          <Caption1 className="muted">{t("setup.oauthHint")}</Caption1>
        )}
        <ProviderButtons providers={state.providers} next={next} />
      </AuthFrame>
    );
  }
  const canRegister = state.registration === "open" && state.password_login;
  return (
    <AuthFrame title={mode === "register" ? t("login.createAccount") : t("login.title")}>
      {error && <ErrorBar error={error} />}
      {canRegister && (
        <TabList
          selectedValue={mode}
          onTabSelect={(_, d) => setMode(d.value as "login" | "register")}
        >
          <Tab value="login">{t("login.signIn")}</Tab>
          <Tab value="register">{t("login.createAccount")}</Tab>
        </TabList>
      )}
      {state.password_login &&
        (mode === "register" ? <RegisterForm onDone={changed} /> : <LoginForm onDone={changed} />)}
      {state.password_login && state.providers.length > 0 && <Or />}
      <ProviderButtons providers={state.providers} next={next} />
      {state.registration === "invite" && (
        <Caption1 className="muted">{t("login.inviteOnly")}</Caption1>
      )}
    </AuthFrame>
  );
}

/** /login: back to where the user wanted to go once signed in. */
export function LoginPage() {
  const { data: state, isPending } = useAuth();
  const [params] = useSearchParams();
  if (isPending) return <Spinner className="block" />;
  if (state?.signed_in) return <Navigate to={params.get("next") ?? "/"} replace />;
  return <AuthScreen />;
}

/** /invite/<token>: accept an invite, signing in or creating an account first. */
export function InvitePage() {
  const { t } = useI18n();
  const { token = "" } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { data: state } = useAuth();
  const changed = useAuthChanged();
  const preview = useQuery({
    queryKey: ["invite", token],
    queryFn: () => api.invitePreview(token),
    retry: false,
  });
  const [mode, setMode] = useState<"register" | "login">("register");
  const accept = useMutation({
    mutationFn: () => api.acceptInvite(token),
    onSuccess: () => {
      changed();
      void navigate("/cameras");
    },
  });
  const signedIn = (s: AuthState) => {
    changed(s);
    accept.mutate();
  };
  const roleName = (role: string) =>
    role === "owner"
      ? t("share.role.owner")
      : role === "editor"
        ? t("share.role.editor")
        : t("share.role.viewer");

  if (!state || preview.isPending) {
    return (
      <AuthFrame title={t("invite.title")}>
        <Spinner />
      </AuthFrame>
    );
  }
  const inv = preview.data;
  return (
    <AuthFrame title={t("invite.title")}>
      {params.get("error") && <ErrorBar error={params.get("error")} />}
      {preview.error ? (
        <ErrorBar error={preview.error} />
      ) : (
        inv && (
          <>
            <Body1>
              {inv.from ? t("invite.from", { name: inv.from }) : t("invite.generic")}
              {inv.is_admin && <> {t("invite.admin")}</>}
            </Body1>
            {inv.note && <Body1 className="muted">“{inv.note}”</Body1>}
            {inv.cameras.length > 0 && (
              <ul className="invite-cameras">
                {inv.cameras.map((c) => (
                  <li key={c.camera_id}>
                    <Text weight="semibold">{c.name}</Text>{" "}
                    <Caption1 className="muted">{roleName(c.role)}</Caption1>
                  </li>
                ))}
              </ul>
            )}
          </>
        )
      )}
      {inv && !state.enabled && <ErrorBar error={t("invite.authOff")} />}
      {inv && state.enabled && state.signed_in && (
        <>
          <Body1 className="muted">
            {t("invite.signedInAs", { name: state.user?.display_name ?? "" })}
          </Body1>
          <ErrorBar error={accept.error} />
          <Button appearance="primary" disabled={accept.isPending} onClick={() => accept.mutate()}>
            {t("invite.accept")}
          </Button>
          <Button
            appearance="subtle"
            icon={<SignOut20Regular />}
            onClick={() => void api.logout().then(() => changed())}
          >
            {t("invite.otherAccount")}
          </Button>
        </>
      )}
      {inv && state.enabled && !state.signed_in && (
        <>
          {state.password_login && (
            <>
              <TabList
                selectedValue={mode}
                onTabSelect={(_, d) => setMode(d.value as "register" | "login")}
              >
                <Tab value="register">{t("login.createAccount")}</Tab>
                <Tab value="login">{t("invite.haveAccount")}</Tab>
              </TabList>
              {mode === "register" ? (
                <RegisterForm
                  invite={token}
                  onDone={(s) => {
                    changed(s);
                    void navigate("/cameras");
                  }}
                />
              ) : (
                <LoginForm onDone={signedIn} />
              )}
              <ErrorBar error={accept.error} />
            </>
          )}
          {state.password_login && state.providers.length > 0 && <Or />}
          <ProviderButtons providers={state.providers} next="/cameras" invite={token} />
        </>
      )}
    </AuthFrame>
  );
}
