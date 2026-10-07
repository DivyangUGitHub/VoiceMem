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

export const api = {
  async getMemories(): Promise<MemorySnapshot> {
    const response = await fetch("/backend/api/memories", { cache: "no-store" });
    if (!response.ok) throw new Error("Unable to load memories");
    return response.json();
  },

  async getSpaces(): Promise<{ spaces: Space[]; active: string }> {
    const response = await fetch("/backend/api/spaces", { cache: "no-store" });
    if (!response.ok) throw new Error("Unable to load memory spaces");
    return response.json();
  },

  async createSpace(name: string, language: string): Promise<Space> {
    const response = await fetch("/backend/api/spaces", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ name, language }),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },

  async useSpace(name: string): Promise<{ active: string }> {
    const response = await fetch(
      `/backend/api/spaces/${encodeURIComponent(name)}/use`,
      { method: "POST" },
    );
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  },
};
