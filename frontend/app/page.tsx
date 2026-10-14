"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api, Me } from "../lib/api";

type Mode = "signin" | "signup" | "key";

function Auth({ me, onDone }: { me: Me; onDone: () => void }) {
  const [mode, setMode] = useState<Mode>(me.accounts ? "signin" : "key");
  const [email, setEmail] = useState("");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (mode === "key") await api.login(secret.trim());
      else if (mode === "signup") await api.signup(email.trim(), secret);
      else await api.loginWithEmail(email.trim(), secret);
      setSecret("");
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed.");
    } finally {
      setBusy(false);
    }
  };

  const tabs: [Mode, string][] = [];
  if (me.accounts) tabs.push(["signin", "Sign in"]);
  if (me.accounts && me.signupAllowed) tabs.push(["signup", "Create account"]);
  tabs.push(["key", "Access key"]);

  return (
    <main className="login">
      <form className="card" onSubmit={submit}>
        <h1>VOICEMEM</h1>
        {tabs.length > 1 && (
          <div className="tabs" role="tablist">
            {tabs.map(([id, label]) => (
              <button
                type="button"
                role="tab"
                aria-selected={mode === id}
                className={`tab ${mode === id ? "active" : ""}`}
                key={id}
                onClick={() => {
                  setMode(id);
                  setError("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        {mode !== "key" && (
          <input
            type="email"
            autoComplete="email"
            autoFocus
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="Email"
            aria-label="Email"
          />
        )}
        <input
          type="password"
          autoComplete={mode === "signup" ? "new-password" : "current-password"}
          autoFocus={mode === "key"}
          required
          minLength={mode === "signup" ? 10 : undefined}
          value={secret}
          onChange={(event) => setSecret(event.target.value)}
          placeholder={mode === "key" ? "Access key" : mode === "signup" ? "Password (10+ characters)" : "Password"}
          aria-label={mode === "key" ? "Access key" : "Password"}
        />
        <button type="submit" disabled={busy || !secret.trim() || (mode !== "key" && !email.trim())}>
          {busy ? "Please wait…" : mode === "signup" ? "Create account" : "Sign in"}
        </button>
        {error && <div className="error" role="alert">{error}</div>}
      </form>
    </main>
  );
}

const PRIVACY_URL = process.env.NEXT_PUBLIC_PRIVACY_URL;

function Consent({ email, onDone, onLogout }: { email: string; onDone: () => void; onLogout: () => void }) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const agree = async () => {
    setBusy(true);
    setError("");
    try {
      await api.setConsent(true);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save your choice.");
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <section className="card" aria-labelledby="consent-title">
        <h1 id="consent-title">Before you start</h1>
        <p className="meta">Signed in as {email}.</p>
        <p>VoiceMem remembers what you tell it. To do that it will:</p>
        <ul className="plain">
          <li>process your voice and what you say, and store the memories it draws from it;</li>
          <li>learn a voiceprint, a numerical signature of your voice, to recognise you;</li>
          <li>keep recordings of your voice turns so it can play them back.</li>
        </ul>
        <p>
          Your memories are kept separate from every other user. You can download everything, or
          delete your account and all of it, at any time from the Account menu.
          {PRIVACY_URL && (
            <>
              {" "}
              <a className="link" href={PRIVACY_URL} target="_blank" rel="noreferrer">
                Privacy policy
              </a>
              .
            </>
          )}
        </p>
        <button onClick={agree} disabled={busy}>{busy ? "Saving…" : "I agree"}</button>
        <button className="secondary" onClick={onLogout} disabled={busy}>Not now, sign out</button>
        {error && <div className="error" role="alert">{error}</div>}
      </section>
    </main>
  );
}

function AccountPanel({
  email,
  onClose,
  onSignedOut,
}: {
  email: string;
  onClose: () => void;
  onSignedOut: () => void;
}) {
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [deletePassword, setDeletePassword] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const run = async (work: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-labelledby="account-title">
      <div className="card modal">
        <div className="modal-head">
          <h2 id="account-title">Account</h2>
          <button className="secondary" onClick={onClose}>Close</button>
        </div>
        <p className="meta">{email}</p>

        <h3>Your data</h3>
        <a className="link" href="/account/export" download>Download everything as a zip →</a>
        <button
          className="secondary"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await api.setConsent(false);
              onSignedOut();
            })
          }
        >
          Withdraw consent and sign out
        </button>

        <h3>Change password</h3>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            run(async () => {
              await api.changePassword(oldPassword, newPassword);
              onSignedOut();
            });
          }}
        >
          <input
            type="password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(event) => setOldPassword(event.target.value)}
            placeholder="Current password"
            aria-label="Current password"
            required
          />
          <input
            type="password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            placeholder="New password (10+ characters)"
            aria-label="New password"
            minLength={10}
            required
          />
          <button type="submit" disabled={busy || !oldPassword || newPassword.length < 10}>
            Change password
          </button>
          <p className="meta">You will be signed out everywhere.</p>
        </form>

        <h3>Delete account</h3>
        {!confirmDelete ? (
          <button className="danger" onClick={() => setConfirmDelete(true)}>Delete my account…</button>
        ) : (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              run(async () => {
                await api.deleteAccount(deletePassword);
                onSignedOut();
              });
            }}
          >
            <p>
              This permanently deletes your account, every memory space, and all recordings. It
              cannot be undone. Download your data first if you want a copy.
            </p>
            <input
              type="password"
              autoComplete="current-password"
              value={deletePassword}
              onChange={(event) => setDeletePassword(event.target.value)}
              placeholder="Type your password to confirm"
              aria-label="Password to confirm deletion"
              required
            />
            <button className="danger" type="submit" disabled={busy || !deletePassword}>
              Delete everything
            </button>
          </form>
        )}

        {message && <div className="meta">{message}</div>}
        {error && <div className="error" role="alert">{error}</div>}
      </div>
    </div>
  );
}

// The voice console is the product. Signed-in users go straight to it; "?account" opens
// the account screen (export, password, delete) and returns to the console when closed.
const CONSOLE_URL = "/backend/";

function Account({ email, onSignedOut }: { email?: string; onSignedOut: () => void }) {
  const wantsAccount = typeof window !== "undefined" && window.location.search.includes("account");
  useEffect(() => {
    if (!wantsAccount || !email) window.location.replace(CONSOLE_URL);
  }, [wantsAccount, email]);
  if (!wantsAccount || !email) return null;
  return <AccountPanel email={email} onClose={() => window.location.replace(CONSOLE_URL)} onSignedOut={onSignedOut} />;
}

export default function Home() {
  // null = still checking
  const [me, setMe] = useState<Me | null>(null);

  const refresh = useCallback(() => api.me().then(setMe, () => setMe({ authenticated: false, accounts: false, signupAllowed: false })), []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const signedOut = useCallback(() => {
    return refresh();
  }, [refresh]);
  const logout = useCallback(async () => {
    await api.logout();
    await signedOut();
  }, [signedOut]);

  if (me === null) return null;
  if (!me.authenticated) return <Auth me={me} onDone={refresh} />;
  if (me.user && !me.user.consented) return <Consent email={me.user.email} onDone={refresh} onLogout={logout} />;
  return <Account email={me.user?.email} onSignedOut={signedOut} />;
}
