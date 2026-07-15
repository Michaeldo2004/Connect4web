import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

let session = null;
let latestSocket;

class FakeSocket {
  constructor() {
    this.connected = true;
    this.handlers = new Map();
    this.emitted = [];
    latestSocket = this;
  }

  on(event, handler) {
    this.handlers.set(event, handler);
    return this;
  }

  emit(event, payload) {
    this.emitted.push([event, payload]);
    return this;
  }

  timeout() {
    return {
      emit: (event, payload) => {
        this.emit(event, payload);
      },
    };
  }

  trigger(event, payload) {
    this.handlers.get(event)?.(payload);
  }

  disconnect() {
    this.connected = false;
  }
}

vi.mock("socket.io-client", () => ({ io: vi.fn(() => new FakeSocket()) }));
vi.mock("@supabase/supabase-js", () => ({
  createClient: vi.fn(() => ({
    from: vi.fn(() => {
      const query = {
        select: vi.fn(() => query),
        eq: vi.fn(() => query),
        single: vi.fn(async () => ({ data: { username: "PlayerOne", display_name: "Player One" }, error: null })),
        maybeSingle: vi.fn(async () => ({ data: { username: "PlayerOne", display_name: "Player One" }, error: null })),
        upsert: vi.fn(async () => ({ error: null })),
      };
      return query;
    }),
    auth: {
      getSession: vi.fn(async () => ({ data: { session } })),
      onAuthStateChange: vi.fn(() => ({ data: { subscription: { unsubscribe: vi.fn() } } })),
      signOut: vi.fn(async () => ({})),
    },
  })),
}));

import App from "../src/App.jsx";

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("application behavior", () => {
  beforeEach(() => {
    session = null;
    latestSocket = undefined;
  });

  test("navigates to login and traps modal focus until Escape closes it", async () => {
    const user = userEvent.setup();
    renderApp("/game/example");

    await user.click(await screen.findByRole("button", { name: "Login" }));
    expect(await screen.findByRole("heading", { name: "Login" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Sign up / Login" }));
    const dialog = screen.getByRole("dialog", { name: "Login" });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toContainElement(document.activeElement);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  test("applies a player move optimistically after a socket-created AI game", async () => {
    session = {
      access_token: "token",
      user: { id: "profile-1", email: "player@example.com", user_metadata: { username: "PlayerOne" } },
    };
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });

    act(() =>
      latestSocket.trigger("game_created", {
        gameId: "game-1",
        playerId: "player-1",
        mode: "ai",
        playerNumber: 1,
        aiNumber: 2,
        currentPlayer: 1,
        board: Array.from({ length: 6 }, () => Array(7).fill(0)),
        status: "playing",
        message: "Your turn",
        difficulty: "medium",
      }),
    );

    await user.click(await screen.findByRole("button", { name: "Drop in column 1" }));
    expect(screen.getByRole("gridcell", { name: "Row 6, column 1: Yellow piece" })).toBeInTheDocument();
    expect(latestSocket.emitted.some(([event, payload]) => event === "player_move" && payload.column === 0)).toBe(true);
  });

  test("shows a recoverable socket error without discarding the current game", async () => {
    session = { access_token: "token", user: { id: "profile-1", email: "player@example.com" } };
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    act(() =>
      latestSocket.trigger("game_created", {
        gameId: "game-2",
        playerId: "player-2",
        mode: "ai",
        playerNumber: 1,
        aiNumber: 2,
        currentPlayer: 1,
        board: Array.from({ length: 6 }, () => Array(7).fill(0)),
        status: "playing",
        message: "Your turn",
        difficulty: "medium",
      }),
    );
    await screen.findByRole("grid", { name: "Connect 4 board" });
    act(() => latestSocket.trigger("connect_error"));
    expect(await screen.findByText(/Flask SocketIO is not responding/)).toBeInTheDocument();
    expect(screen.getByRole("grid", { name: "Connect 4 board" })).toBeInTheDocument();
  });
});
