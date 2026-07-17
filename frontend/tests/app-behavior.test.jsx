import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, test, vi } from "vitest";

let session = null;
let latestSocket;

class FakeSocket {
  constructor() {
    this.connected = true;
    this.handlers = new Map();
    this.callbacks = new Map();
    this.emitted = [];
    latestSocket = this;
  }

  on(event, handler) {
    this.handlers.set(event, handler);
    return this;
  }

  emit(event, payload, callback) {
    this.emitted.push([event, payload]);
    if (callback) {
      this.callbacks.set(event, callback);
    }
    return this;
  }

  timeout() {
    return {
      emit: (event, payload, callback) => {
        this.emit(event, payload, callback);
      },
    };
  }

  trigger(event, payload) {
    this.handlers.get(event)?.(payload);
  }

  respond(event, payload) {
    const callback = this.callbacks.get(event);
    this.callbacks.delete(event);
    callback?.(null, payload);
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

function createTestAuthClient() {
  return {
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
  };
}

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App authClient={createTestAuthClient()} />
    </MemoryRouter>,
  );
}

function authenticatedSession() {
  return {
    access_token: "token",
    user: { id: "profile-1", email: "player@example.com", user_metadata: { username: "PlayerOne" } },
  };
}

function emptyBoard() {
  return Array.from({ length: 6 }, () => Array(7).fill(0));
}

function jsonResponse(data, { ok = true, status = 200 } = {}) {
  return { ok, status, text: vi.fn(async () => JSON.stringify(data)) };
}

function createdGame(overrides = {}) {
  return {
    gameId: "game-1",
    playerId: "player-1",
    mode: "ai",
    playerNumber: 1,
    aiNumber: 2,
    currentPlayer: 1,
    board: emptyBoard(),
    status: "playing",
    message: "Your turn",
    difficulty: "medium",
    ...overrides,
  };
}

describe("application behavior", () => {
  beforeEach(() => {
    session = null;
    latestSocket = undefined;
    vi.stubGlobal("fetch", vi.fn());
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

  test("drops a piece by clicking a board cell while retaining numbered controls", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });

    act(() => latestSocket.trigger("game_created", createdGame()));

    expect(await screen.findByRole("button", { name: "Drop in column 1" })).toBeEnabled();
    await user.click(screen.getByRole("gridcell", { name: "Row 6, column 1: Empty" }));
    expect(screen.getByRole("gridcell", { name: "Row 6, column 1: Yellow piece" })).toBeInTheDocument();
    expect(latestSocket.emitted.some(([event, payload]) => event === "player_move" && payload.column === 0)).toBe(true);
  });

  test("shows a recoverable socket error without discarding the current game", async () => {
    session = authenticatedSession();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    act(() => latestSocket.trigger("game_created", createdGame({ gameId: "game-2", playerId: "player-2" })));
    await screen.findByRole("grid", { name: "Connect 4 board" });
    act(() => latestSocket.trigger("connect_error"));
    expect(await screen.findByText(/Flask SocketIO is not responding/)).toBeInTheDocument();
    expect(screen.getByRole("grid", { name: "Connect 4 board" })).toBeInTheDocument();
  });

  test("renders profile statistics and completed games from the authenticated API", async () => {
    session = authenticatedSession();
    fetch.mockResolvedValue(
      jsonResponse({
        games: [
          {
            id: "review-1",
            mode: "ai",
            difficulty: "hard",
            status: "human_win",
            result: "Win",
            playerNumber: 1,
            startedAt: "2026-07-14T20:00:00Z",
            endedAt: "2026-07-14T20:05:00Z",
          },
          {
            id: "review-2",
            mode: "multiplayer",
            difficulty: "fast_connect_30",
            status: "player2_win",
            result: "Loss",
            playerNumber: 1,
            endedAt: "2026-07-14T21:00:00Z",
          },
        ],
      }),
    );

    renderApp("/profiles");
    const stats = await screen.findByRole("region", { name: "Profile statistics" });
    await waitFor(() => expect(within(stats).getByText("Total played").nextElementSibling).toHaveTextContent("2"));
    expect(within(stats).getByText("Wins").nextElementSibling).toHaveTextContent("1");
    expect(within(stats).getByText("Losses").nextElementSibling).toHaveTextContent("1");
    expect(within(stats).getByText("Win rate").nextElementSibling).toHaveTextContent("50%");
    expect(await screen.findByRole("region", { name: "Completed games" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Review Game" })).toHaveLength(2);
    expect(screen.getByText("Fast Connect · 30s")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:5000/api/profile/games",
      expect.objectContaining({ headers: { Authorization: "Bearer token" } }),
    );
  });

  test("renders a profile API error instead of an empty successful state", async () => {
    session = authenticatedSession();
    fetch.mockResolvedValue(
      jsonResponse({ message: "Game history is temporarily unavailable" }, { ok: false, status: 503 }),
    );
    renderApp("/profiles");
    expect(await screen.findByText("Game history is temporarily unavailable")).toHaveClass("profile-error");
    expect(screen.queryByText("No completed games")).not.toBeInTheDocument();
  });

  test("renders completed review navigation, evaluation totals, and the full move log without opening a socket", async () => {
    session = authenticatedSession();
    const firstBoard = emptyBoard();
    firstBoard[5][0] = 1;
    const secondBoard = firstBoard.map((row) => [...row]);
    secondBoard[5][1] = 2;
    const games = [
      {
        id: "review-1",
        mode: "multiplayer",
        difficulty: "fast_connect_60",
        status: "player1_win",
        result: "Win",
        playerNumber: 1,
        playerNames: { 1: "Alice", 2: "Bob" },
        winnerPlayerNumber: 1,
        winnerName: "Alice",
        endedAt: "2026-07-14T20:05:00Z",
      },
    ];
    fetch.mockImplementation(async (url) => {
      if (String(url).endsWith("/moves")) {
        return jsonResponse({
          analysis_status: "complete",
          analysis_available: true,
          moves: [
            {
              move_number: 1,
              player_number: 1,
              column_played: 0,
              board_after: firstBoard,
              move_analysis: [{ feedback: "Great Move" }],
            },
            {
              move_number: 2,
              player_number: 2,
              column_played: 1,
              board_after: secondBoard,
              move_analysis: [{ feedback: "Blunder" }],
            },
          ],
        });
      }
      return jsonResponse({ games });
    });

    renderApp("/game/review-1/review");
    expect(await screen.findByRole("grid", { name: "Board after move 1" })).toBeInTheDocument();
    expect(screen.getByText("Fast Connect · 60s")).toBeInTheDocument();
    expect(latestSocket).toBeUndefined();
    const aliceSummary = screen.getByRole("region", { name: "Alice move evaluation summary" });
    const bobSummary = screen.getByRole("region", { name: "Bob move evaluation summary" });
    expect(within(aliceSummary).getByText("Great Move").nextElementSibling).toHaveTextContent("1");
    expect(within(bobSummary).getByText("Blunder").nextElementSibling).toHaveTextContent("1");
    expect(screen.getByLabelText("Game moves")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Move evaluation table" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Evaluate moves" })).toBeDisabled();

    const next = screen.getByRole("button", { name: "Next move" });
    expect(next).toBeEnabled();
    await userEvent.click(next);
    expect(await screen.findByRole("grid", { name: "Board after move 2" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Show move 2 by Opponent" })).toHaveAttribute("aria-current", "step");
  });

  test("keeps a multiplayer create command through disconnect and reconciles it on reconnect", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("tab", { name: /Standard Connect/ }));
    await user.click(screen.getByRole("button", { name: "Create game" }));
    const pending = JSON.parse(window.sessionStorage.getItem("connect4_pending_game"));
    expect(pending).toMatchObject({ mode: "multiplayer", profileId: "profile-1" });

    act(() => latestSocket.trigger("disconnect"));
    expect(JSON.parse(window.sessionStorage.getItem("connect4_pending_game"))).toMatchObject({
      requestId: pending.requestId,
    });
    act(() => latestSocket.trigger("connect"));
    expect(
      latestSocket.emitted.some(
        ([event, payload]) => event === "reconcile_multiplayer_creation" && payload.requestId === pending.requestId,
      ),
    ).toBe(true);
  });

  test("offers all PvP presets and preserves the selected Fast Connect variant", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp();

    const playerModes = await screen.findByRole("tablist", { name: "Player game mode" });
    const playerModeTabs = within(playerModes).getAllByRole("tab");
    expect(playerModeTabs).toHaveLength(3);
    expect(within(playerModeTabs[0]).getByText("90 seconds per player")).toBeInTheDocument();
    expect(within(playerModeTabs[1]).getByText("60 seconds per player")).toBeInTheDocument();
    expect(within(playerModeTabs[2]).getByText("30 seconds per player")).toBeInTheDocument();

    await user.click(playerModeTabs[2]);
    expect(screen.getByText("Fast Connect · 30s")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create game" }));

    const pendingGame = JSON.parse(window.sessionStorage.getItem("connect4_pending_game"));
    expect(pendingGame).toMatchObject({
      mode: "multiplayer",
      difficulty: "fast_connect_30",
    });
    act(() =>
      latestSocket.respond("reconcile_multiplayer_creation", {
        ok: true,
        status: "not_found",
        requestId: pendingGame.requestId,
      }),
    );
    expect(
      latestSocket.emitted.some(
        ([event, payload]) =>
          event === "create_multiplayer_game" &&
          payload.requestId === pendingGame.requestId &&
          payload.difficulty === "fast_connect_30",
      ),
    ).toBe(true);
  });

  test("shows public rooms and joins the selected room with an authenticated socket payload", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp("/join");
    await waitFor(() => expect(latestSocket.emitted.some(([event]) => event === "list_public_games")).toBe(true));
    act(() =>
      latestSocket.trigger("public_games", {
        games: [{ gameId: "public-1", ownerName: "Alice", difficulty: "fast_connect_60" }],
      }),
    );
    expect(await screen.findByText("Alice's room")).toBeInTheDocument();
    expect(screen.getByText("Fast Connect · 60s")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Join room" }));
    expect(
      latestSocket.emitted.some(
        ([event, payload]) =>
          event === "join_multiplayer_game" && payload.gameId === "public-1" && payload.publicJoin === true,
      ),
    ).toBe(true);
  });

  test("applies authoritative board updates and replaces the displayed multiplayer game id", async () => {
    session = authenticatedSession();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    const joinedSocket = latestSocket;
    act(() =>
      joinedSocket.trigger(
        "multiplayer_game_joined",
        createdGame({
          gameId: "old-room",
          mode: "multiplayer",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      ),
    );
    expect(
      joinedSocket.emitted.some(
        ([event, payload]) =>
          event === "multiplayer_player_ready" && payload.gameId === "old-room" && payload.playerId === "player-1",
      ),
    ).toBe(true);
    await screen.findByRole("grid", { name: "Connect 4 board" });
    const updatedBoard = emptyBoard();
    updatedBoard[5][3] = 2;
    await act(async () =>
      latestSocket.trigger("board_updated", {
        ...createdGame({
          gameId: "new-room",
          playerId: "player-1",
          mode: "multiplayer",
          playerNumber: 1,
          currentPlayer: 1,
          playersConnected: 2,
          board: updatedBoard,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      }),
    );
    const matchDetails = document.querySelector(".match-details");
    expect(within(matchDetails).getAllByText("new-room")).toHaveLength(2);
    expect(screen.getByRole("gridcell", { name: "Row 6, column 4: Red piece" })).toBeInTheDocument();
  });

  test("renders both multiplayer clocks and counts down only the active player", async () => {
    session = authenticatedSession();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-16T20:00:00Z"));

    try {
      act(() =>
        latestSocket.trigger(
          "multiplayer_game_joined",
          createdGame({
            mode: "multiplayer",
            playersConnected: 2,
            playerNames: { 1: "Alice", 2: "Bob" },
            timeBanksMs: { 1: 90_000, 2: 90_000 },
            activeTimerPlayer: 1,
            timerRunning: true,
            serverTimeMs: Date.now(),
          }),
        ),
      );

      expect(screen.getByLabelText("Player 1 time remaining 1:30")).toHaveClass("active");
      expect(screen.getByLabelText("Player 2 time remaining 1:30")).not.toHaveClass("active");
      act(() => vi.advanceTimersByTime(1_000));
      expect(screen.getByLabelText("Player 1 time remaining 1:29")).toBeInTheDocument();
      expect(screen.getByLabelText("Player 2 time remaining 1:30")).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  test("renders the Fast Connect preset and its authoritative 60-second clocks", async () => {
    session = authenticatedSession();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });

    act(() =>
      latestSocket.trigger(
        "multiplayer_game_joined",
        createdGame({
          mode: "multiplayer",
          difficulty: "fast_connect_60",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
          timeBanksMs: { 1: 60_000, 2: 60_000 },
          activeTimerPlayer: 1,
          timerRunning: true,
          serverTimeMs: Date.now(),
        }),
      ),
    );

    expect(screen.getByText("Fast Connect · 60s")).toBeInTheDocument();
    expect(screen.getByLabelText("Player 1 time remaining 1:00")).toBeInTheDocument();
    expect(screen.getByLabelText("Player 2 time remaining 1:00")).toBeInTheDocument();
  });

  test("shows only the human clock against AI and preserves a timer result message", async () => {
    session = authenticatedSession();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    act(() =>
      latestSocket.trigger(
        "game_created",
        createdGame({
          playerNumber: 2,
          aiNumber: 1,
          currentPlayer: 2,
          timeBanksMs: { 2: 90_000 },
          activeTimerPlayer: 2,
          timerRunning: true,
          serverTimeMs: Date.now(),
        }),
      ),
    );

    expect(screen.queryByLabelText(/Player 1 time remaining/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Player 2 time remaining 1:30")).toBeInTheDocument();

    act(() =>
      latestSocket.trigger("board_updated", {
        ...createdGame({
          mode: "multiplayer",
          status: "player1_win",
          message: "Alice won by timer",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
          timeBanksMs: { 1: 12_000, 2: 0 },
          activeTimerPlayer: null,
          timerRunning: false,
          endReason: "timeout",
        }),
      }),
    );
    expect(screen.getByRole("heading", { name: "Alice won by timer" })).toBeInTheDocument();
  });

  test("sanitizes signup inputs and supports password visibility", async () => {
    const user = userEvent.setup();
    renderApp("/signup");
    const username = await screen.findByPlaceholderText("connect4player");
    await user.type(username, "Player 12<script>");
    expect(username).toHaveValue("Player12script");
    const password = screen.getByPlaceholderText("Password");
    await user.type(password, "secret12");
    expect(password).toHaveAttribute("type", "password");
    await user.click(screen.getByRole("button", { name: "Show password" }));
    expect(password).toHaveAttribute("type", "text");
  });

  test("switches theme and renders routed information pages", async () => {
    const user = userEvent.setup();
    const { unmount } = renderApp();
    const themeButton = await screen.findByRole("button", { name: "Switch to dark theme" });
    await user.click(themeButton);
    expect(document.querySelector(".app-shell")).toHaveClass("theme-dark");
    expect(window.localStorage.getItem("connect4_theme")).toBe("dark");
    unmount();

    renderApp("/privacypolicy");
    expect(await screen.findByRole("heading", { name: "Privacy Policy" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Information collected" })).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent("Connect4web by Michael D");
  });

  test("requests move evaluation and reports its queue position", async () => {
    session = authenticatedSession();
    const board = emptyBoard();
    board[5][0] = 1;
    fetch.mockImplementation(async (url, options = {}) => {
      if (options.method === "POST") {
        return jsonResponse({ status: "queued", queuePosition: 2 });
      }
      if (String(url).endsWith("/moves")) {
        return jsonResponse({
          analysis_status: "not_requested",
          analysis_available: true,
          moves: [{ move_number: 1, player_number: 1, column_played: 0, board_after: board }],
        });
      }
      return jsonResponse({
        games: [
          {
            id: "review-queue",
            mode: "ai",
            difficulty: "medium",
            status: "human_win",
            result: "Win",
            playerNumber: 1,
          },
        ],
      });
    });
    const user = userEvent.setup();
    renderApp("/game/review-queue/review");
    await screen.findByRole("grid", { name: "Board after move 1" });
    const evaluate = await screen.findByRole("button", { name: "Evaluate moves" });
    expect(evaluate).toBeEnabled();
    await user.click(evaluate);
    expect(await screen.findByText("Move evaluation queued (position 2).")).toBeInTheDocument();
    expect(evaluate).toBeDisabled();
    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:5000/api/profile/games/review-queue/analysis",
      expect.objectContaining({ method: "POST", headers: { Authorization: "Bearer token" } }),
    );
  });

  test("toggles a waiting multiplayer room public and reflects the server acknowledgement", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    act(() =>
      latestSocket.trigger(
        "multiplayer_game_joined",
        createdGame({
          gameId: "waiting-room",
          mode: "multiplayer",
          status: "waiting",
          message: "Waiting for Player 2",
          playersConnected: 1,
          playerNames: { 1: "Alice" },
        }),
      ),
    );
    const toggle = await screen.findByRole("button", { name: "Make Room Public" });
    expect(toggle).toHaveAttribute("aria-pressed", "false");
    await user.click(toggle);
    expect(
      latestSocket.emitted.some(
        ([event, payload]) =>
          event === "set_room_public" && payload.gameId === "waiting-room" && payload.public === true,
      ),
    ).toBe(true);
    act(() => latestSocket.trigger("room_public_updated", { publicRoom: true, message: "Room is public" }));
    expect(toggle).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByText("Room is public")).toBeInTheDocument();
  });

  test("uses viewer-relative multiplayer winner messaging and has no share-result action", async () => {
    session = authenticatedSession();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    act(() =>
      latestSocket.trigger(
        "multiplayer_game_joined",
        createdGame({
          mode: "multiplayer",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      ),
    );
    act(() =>
      latestSocket.trigger("board_updated", {
        ...createdGame({
          mode: "multiplayer",
          status: "player1_win",
          message: "Player 1 wins",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      }),
    );
    expect(await screen.findByRole("heading", { name: "You Won!" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Share result" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Review match" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Request rematch" })).toBeEnabled();

    act(() =>
      latestSocket.trigger("board_updated", {
        ...createdGame({
          mode: "multiplayer",
          status: "player1_win",
          message: "Player 1 wins by default",
          playersConnected: 1,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      }),
    );
    expect(screen.getByRole("button", { name: "Request rematch" })).toBeDisabled();
  });

  test("keeps the remaining player's room identity until their completed-game leave is acknowledged", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp();
    await screen.findByRole("button", { name: "Create game" });
    act(() =>
      latestSocket.trigger(
        "multiplayer_game_joined",
        createdGame({
          gameId: "finished-room",
          playerId: "remaining-player",
          mode: "multiplayer",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      ),
    );
    act(() =>
      latestSocket.trigger("board_updated", {
        ...createdGame({
          gameId: "finished-room",
          playerId: "remaining-player",
          mode: "multiplayer",
          status: "player2_win",
          playersConnected: 2,
          playerNames: { 1: "Alice", 2: "Bob" },
        }),
      }),
    );

    act(() => latestSocket.trigger("player_left", { gameId: "finished-room", message: "Player 1 left the room" }));
    expect(JSON.parse(window.sessionStorage.getItem("connect4_multiplayer_session"))).toMatchObject({
      gameId: "finished-room",
      playerId: "remaining-player",
    });

    await user.click(
      within(screen.getByRole("dialog", { name: "Player left" })).getByRole("button", { name: "Main menu" }),
    );
    expect(
      latestSocket.emitted.some(
        ([event, payload]) =>
          event === "leave_game" && payload.gameId === "finished-room" && payload.playerId === "remaining-player",
      ),
    ).toBe(true);
  });

  test("clears a terminal multiplayer create command and exposes the rejection", async () => {
    session = authenticatedSession();
    const user = userEvent.setup();
    renderApp();
    await user.click(await screen.findByRole("tab", { name: /Standard Connect/ }));
    await user.click(screen.getByRole("button", { name: "Create game" }));
    const pending = JSON.parse(window.sessionStorage.getItem("connect4_pending_game"));
    act(() =>
      latestSocket.trigger("create_rejected", {
        requestId: pending.requestId,
        message: "Room creation was rejected",
        code: "creation_request_terminal",
      }),
    );
    expect(await screen.findByText("Room creation was rejected")).toBeInTheDocument();
    expect(window.sessionStorage.getItem("connect4_pending_game")).toBeNull();
    expect(screen.getByRole("button", { name: "Create game" })).toBeEnabled();
  });
});
