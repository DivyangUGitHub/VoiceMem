export type Memory = {
  content?: string;
  text?: string;
  source?: string;
  priority?: number;
  cluster?: string;
  slot?: string;
};

export type Space = {
  id: string;
  name: string;
  count?: number;
  active?: boolean;
  language?: string;
};

export type MemorySnapshot = {
  left?: Memory[];
  right?: Memory[];
};

/** Thrown when the server says the session is missing or expired. */
export class AuthError extends Error {
  constructor() {
    super("Please sign in again.");
    this.name = "AuthError";
  }
}

async function request(input: string, init?: RequestInit): Promise<Response> {
  const response = await fetch(input, { cache: "no-store", ...init });
  if (response.status === 401) throw new AuthError();
  if (response.status === 429) throw new Error("Too many requests. Wait a minute and try again.");
  if (!response.ok) throw new Error((await response.text()) || `Request failed (${response.status})`);
  return response;
}

const json = { "content-type": "application/json" };

export const api = {
  /** True when a valid session cookie exists, or when the server runs with auth disabled. */
  async me(): Promise<boolean> {
    const response = await fetch("/auth/me", { cache: "no-store" });
    return response.ok;
  },

  async login(key: string): Promise<void> {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ key }),
    });
    if (response.status === 401) throw new Error("That key is not valid.");
    if (response.status === 429) throw new Error("Too many attempts. Wait a minute and try again.");
    if (!response.ok) throw new Error("Sign-in failed. Try again.");
  },

  async logout(): Promise<void> {
    await fetch("/auth/logout", { method: "POST" });
  },

  async getMemories(): Promise<MemorySnapshot> {
    return (await request("/backend/api/memories")).json();
  },

  async getSpaces(): Promise<{ spaces: Space[]; active: string }> {
    return (await request("/backend/api/spaces")).json();
  },

  async createSpace(name: string, language: string): Promise<Space> {
    const response = await request("/backend/api/spaces", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ name, language }),
    });
    return response.json();
  },

  async useSpace(name: string): Promise<{ active: string }> {
    const response = await request(`/backend/api/spaces/${encodeURIComponent(name)}/use`, {
      method: "POST",
    });
    return response.json();
  },
};
