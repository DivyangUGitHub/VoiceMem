"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, AuthError, Me, Memory, Space } from "../lib/api";

type Message = { role: "user" | "assistant"; text: string };

function textOf(memory: Memory) {
  return memory.content || memory.text || "";
}

function Console({
  onUnauthorized,
  onLogout,
  onAccount,
  email,
}: {
  onUnauthorized: () => void;
  onLogout: () => void;
  onAccount?: () => void;
  email?: string;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [memories, setMemories] = useState<{ left: Memory[]; right: Memory[] }>({
    left: [],
    right: [],
  });
  const [spaces, setSpaces] = useState<Space[]>([]);
  const [activeSpace, setActiveSpace] = useState("");
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const socket = useRef<WebSocket | null>(null);

  const loadState = useCallback(async () => {
    try {
      const [snapshot, spaceState] = await Promise.all([
        api.getMemories(),
        api.getSpaces(),
      ]);
      setMemories({
        left: snapshot.left || [],
        right: snapshot.right || [],
      });
      setSpaces(spaceState.spaces || []);
      setActiveSpace(spaceState.active || "");
    } catch (err) {
      if (err instanceof AuthError) return onUnauthorized();
      setError(err instanceof Error ? err.message : "Unable to load VoiceMem");
    }
  }, [onUnauthorized]);

  const connect = useCallback(() => {
    socket.current?.close();
    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/backend/ws`,
    );
    socket.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setError("");
    };

    ws.onclose = () => {
      setConnected(false);
      setBusy(false);
    };

    ws.onerror = () => {
      setConnected(false);
      setError("VoiceMem connection failed. Check the backend.");
    };

    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      let message: any;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }

      if (message.type === "answer_start") {
        setBusy(true);
        setMessages((current) => [...current, { role: "assistant", text: "" }]);
      }

      if (message.type === "answer_delta") {
        // Append to the last assistant message. Reading a ref here lost text when the final
        // chunk and "done" arrived in the same tick.
        setMessages((current) => {
          const last = current.length - 1;
          if (last < 0 || current[last].role !== "assistant") return current;
          const next = [...current];
          next[last] = { ...next[last], text: next[last].text + String(message.text || "") };
          return next;
        });
      }

      if (message.type === "answer_done") {
        setBusy(false);
      }

      if (message.type === "memory_hits") {
        setMemories({
          left: message.left || [],
          right: message.right || [],
        });
      }

      if (message.type === "error") {
        setBusy(false);
        setError(String(message.message || "VoiceMem returned an error"));
      }
    };
  }, []);

  useEffect(() => {
    loadState();
    connect();
    const timer = window.setInterval(loadState, 10000);
    return () => {
      window.clearInterval(timer);
      socket.current?.close();
    };
  }, [connect, loadState]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const text = input.trim();
    if (!text || !socket.current || socket.current.readyState !== WebSocket.OPEN || busy) {
      return;
    }
    setMessages((current) => [...current, { role: "user", text }]);
    socket.current.send(JSON.stringify({ type: "user_text", text }));
    setInput("");
    setBusy(true);
  };

  const switchSpace = async (space: string) => {
    try {
      await api.useSpace(space);
      setActiveSpace(space);
      setMessages([]);
      await loadState();
    } catch (err) {
      if (err instanceof AuthError) return onUnauthorized();
      setError(err instanceof Error ? err.message : "Unable to switch memory space");
    }
  };

  const createSpace = async () => {
    const name = window.prompt("Memory space name");
    if (!name?.trim()) return;
    try {
      const created = await api.createSpace(name.trim(), "en");
      await switchSpace(created.name);
      await loadState();
    } catch (err) {
      if (err instanceof AuthError) return onUnauthorized();
      setError(err instanceof Error ? err.message : "Unable to create memory space");
    }
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <h1>VOICEMEM</h1>
          <span className="badge">{connected ? "LIVE" : "OFFLINE"}</span>
        </div>

        <div>
          <p className="section-title">Memory spaces</p>
          <div className="space-list">
            {spaces.map((space) => (
              <button
                className={`space ${space.name === activeSpace ? "active" : ""}`}
                key={space.id}
                onClick={() => switchSpace(space.name)}
              >
                {space.name}
                <small>{space.count ?? 0} memories</small>
              </button>
            ))}
          </div>
          <button className="secondary" style={{ width: "100%", marginTop: 9 }} onClick={createSpace}>
            + New space
          </button>
        </div>

        <div style={{ marginTop: "auto" }}>
          <p className="section-title">Voice</p>
          <a className="link" href="/backend/" target="_blank" rel="noreferrer">
            Open real-time voice console →
          </a>
          {onAccount && (
            <button className="secondary" style={{ width: "100%", marginTop: 9 }} onClick={onAccount} title={email}>
              Account
            </button>
          )}
          <button className="secondary" style={{ width: "100%", marginTop: 9 }} onClick={onLogout}>
            Sign out
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h2>{activeSpace || "VoiceMem"}</h2>
            <div className="connection">
              {connected ? "Connected to the VoiceMem runtime" : "Connecting to the VoiceMem runtime…"}
            </div>
          </div>
          <button className="secondary" onClick={connect}>Reconnect</button>
        </header>

        <section className="messages">
          {messages.length === 0 ? (
            <div className="empty">
              <h2>Long-term memory, ready to query.</h2>
              <p>
                Ask about facts, preferences, people, experiences, or anything VoiceMem has
                already learned in this memory space.
              </p>
            </div>
          ) : (
            messages.map((message, index) => (
              <article className={`message ${message.role}`} key={index}>
                <div className="role">{message.role}</div>
                <div className="bubble">{message.text || (busy && message.role === "assistant" ? "…" : "")}</div>
              </article>
            ))
          )}
        </section>

        <form className="composer" onSubmit={submit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                submit(event);
              }
            }}
            placeholder="Ask VoiceMem something…"
            disabled={!connected || busy}
          />
          <button type="submit" disabled={!connected || busy || !input.trim()}>
            Send
          </button>
        </form>
      </main>

      <aside className="inspector">
        <p className="section-title">Retrieved memory</p>

        <div className="card">
          <h3>Left brain</h3>
          {memories.left.length === 0 ? (
            <div className="meta">No factual memories retrieved yet.</div>
          ) : (
            memories.left.slice(0, 12).map((memory, index) => (
              <div className="memory" key={index}>
                {textOf(memory)}
                {(memory.source || memory.priority !== undefined) && (
                  <div className="meta">
                    {memory.source || "memory"}{memory.priority !== undefined ? ` · priority ${memory.priority}` : ""}
                  </div>
                )}
              </div>
            ))
          )}
        </div>

        <div className="card">
          <h3>Right brain</h3>
          {memories.right.length === 0 ? (
            <div className="meta">No emotional or personality memories retrieved yet.</div>
          ) : (
            memories.right.slice(0, 12).map((memory, index) => (
              <div className="memory" key={index}>
                {textOf(memory)}
                <div className="meta">
                  {[memory.cluster, memory.slot, memory.source].filter(Boolean).join(" · ")}
                </div>
              </div>
            ))
          )}
        </div>

        {error && <div className="error">{error}</div>}
      </aside>
    </div>
  );
}

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

export default function Home() {
  // null = still checking
  const [me, setMe] = useState<Me | null>(null);
  const [showAccount, setShowAccount] = useState(false);

  const refresh = useCallback(() => api.me().then(setMe, () => setMe({ authenticated: false, accounts: false, signupAllowed: false })), []);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const signedOut = useCallback(() => {
    setShowAccount(false);
    return refresh();
  }, [refresh]);
  const logout = useCallback(async () => {
    await api.logout();
    await signedOut();
  }, [signedOut]);

  if (me === null) return null;
  if (!me.authenticated) return <Auth me={me} onDone={refresh} />;
  if (me.user && !me.user.consented) return <Consent email={me.user.email} onDone={refresh} onLogout={logout} />;
  return (
    <>
      <Console
        onUnauthorized={signedOut}
        onLogout={logout}
        onAccount={me.user ? () => setShowAccount(true) : undefined}
        email={me.user?.email}
      />
      {showAccount && me.user && (
        <AccountPanel email={me.user.email} onClose={() => setShowAccount(false)} onSignedOut={signedOut} />
      )}
    </>
  );
}
