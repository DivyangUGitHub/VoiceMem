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

export type Me = {
  authenticated: boolean;
  accounts: boolean;
  signupAllowed: boolean;
  user?: { email: string; consented: boolean };
};

/** Turn a failed response into an Error with the server's message when it sent one. */
async function failIfBad(response: Response, fallback: string): Promise<void> {
  if (response.ok) return;
  if (response.status === 429) throw new Error("Too many attempts. Wait a minute and try again.");
  const body = await response.json().catch(() => ({}));
  throw new Error(body.message || (typeof body.detail === "string" ? body.detail : "") || fallback);
}

export const api = {
  /** Who is signed in, and what this server offers. Works whether or not you are signed in. */
  async me(): Promise<Me> {
    const response = await fetch("/auth/me", { cache: "no-store" });
    const body = await response.json().catch(() => ({}));
    return {
      authenticated: response.ok && body.authenticated !== false,
      accounts: Boolean(body.accounts),
      signupAllowed: Boolean(body.signup_allowed),
      user: body.user ? { email: String(body.user.email), consented: Boolean(body.user.consented) } : undefined,
    };
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

  async loginWithEmail(email: string, password: string): Promise<void> {
    const response = await fetch("/auth/login", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ email, password }),
    });
    if (response.status === 401) throw new Error("Wrong email or password.");
    await failIfBad(response, "Sign-in failed. Try again.");
  },

  async signup(email: string, password: string): Promise<void> {
    const response = await fetch("/auth/signup", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ email, password }),
    });
    if (response.status === 409) throw new Error("An account with this email already exists.");
    if (response.status === 403) throw new Error("Sign-up is closed. Ask the owner for an account.");
    await failIfBad(response, "Could not create the account. Try again.");
  },

  async setConsent(agree: boolean): Promise<void> {
    await failIfBad(
      await fetch("/account/consent", { method: "POST", headers: json, body: JSON.stringify({ agree }) }),
      "Could not save your choice. Try again.",
    );
  },

  async changePassword(oldPassword: string, newPassword: string): Promise<void> {
    const response = await fetch("/account/password", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ old: oldPassword, new: newPassword }),
    });
    if (response.status === 401) throw new Error("Your current password is wrong.");
    await failIfBad(response, "Could not change the password.");
  },

  async deleteAccount(password: string): Promise<void> {
    const response = await fetch("/account/delete", {
      method: "POST",
      headers: json,
      body: JSON.stringify({ password }),
    });
    if (response.status === 401) throw new Error("That password is wrong.");
    await failIfBad(response, "Could not delete the account.");
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
