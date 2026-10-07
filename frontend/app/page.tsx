"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, Memory, Space } from "../lib/api";

type Message = { role: "user" | "assistant"; text: string };

function textOf(memory: Memory) {
  return memory.content || memory.text || "";
}

export default function Home() {
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
  const assistantIndex = useRef<number | null>(null);

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
      setError(err instanceof Error ? err.message : "Unable to load VoiceMem");
    }
  }, []);

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
        setMessages((current) => {
          const next = [...current, { role: "assistant", text: "" }];
          assistantIndex.current = next.length - 1;
          return next;
        });
      }

      if (message.type === "answer_delta") {
        setMessages((current) => {
          const index = assistantIndex.current;
          if (index === null || !current[index]) return current;
          const next = [...current];
          next[index] = {
            ...next[index],
            text: next[index].text + String(message.text || ""),
          };
          return next;
        });
      }

      if (message.type === "answer_done") {
        setBusy(false);
        assistantIndex.current = null;
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
