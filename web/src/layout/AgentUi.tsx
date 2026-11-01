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
  /** The sequence row that is open (its bin id), so a note from the panel is about that item. */
  openItem: string | null;
  setOpenItem: (bin: string | null) => void;
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
  openItem: null,
  setOpenItem: () => {},
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
  const [openItem, setOpenItem] = useState<string | null>(null);
  return <AgentUiContext.Provider value={{ railOpen, setRailOpen, openItem, setOpenItem }}>{children}</AgentUiContext.Provider>;
}

export function useAgentUi(): AgentUi {
  return useContext(AgentUiContext);
}
