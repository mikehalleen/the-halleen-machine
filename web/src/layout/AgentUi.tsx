import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

/**
 * Session UI state that must outlive a route change and is not a view of its own: the
 * desk rail's open state (persisted).
 * `Shell` remounts between routes, so this lives above the router. The phone's session
 * push is a view, so it lives in the URL instead (`useSessionPanel`).
 */
interface AgentUi {
  railOpen: boolean;
  setRailOpen: (open: boolean) => void;
}

const RAIL_KEY = "thm.railOpen";

function readRailOpen(): boolean {
  try {
    const v = window.localStorage.getItem(RAIL_KEY);
    return v === null ? true : v === "1";
  } catch {
    return true;
  }
}

const AgentUiContext = createContext<AgentUi>({
  railOpen: true,
  setRailOpen: () => {},
});

export function AgentUiProvider({ children }: { children: ReactNode }) {
  const [railOpen, setRailState] = useState(readRailOpen);
  const setRailOpen = useCallback((open: boolean) => {
    setRailState(open);
    try {
      window.localStorage.setItem(RAIL_KEY, open ? "1" : "0");
    } catch {
      /* private mode: the default is fine */
    }
  }, []);
  return (
    <AgentUiContext.Provider value={{ railOpen, setRailOpen }}>{children}</AgentUiContext.Provider>
  );
}

export function useAgentUi(): AgentUi {
  return useContext(AgentUiContext);
}
