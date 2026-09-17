import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { fetchJSON } from "./api.js";

/** The agent catalog, fetched once and shared.
 *
 * `/api/agents` returns each agent's input schema with its select options
 * already populated from the data store, so the forms are built from this and
 * nothing else. `refresh()` exists because adding an employee changes the
 * employee dropdowns on every other agent.
 */
const AgentsContext = createContext({ agents: [], loading: true, error: null, refresh: () => {} });

export function AgentsProvider({ children }) {
  const [agents, setAgents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const data = await fetchJSON("/api/agents");
      setAgents(data.agents || []);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const value = useMemo(() => ({ agents, loading, error, refresh }), [agents, loading, error, refresh]);
  return <AgentsContext.Provider value={value}>{children}</AgentsContext.Provider>;
}

export const useAgents = () => useContext(AgentsContext);

export function useAgent(agentId) {
  const { agents, loading, error } = useAgents();
  return { agent: agents.find((a) => a.id === agentId) || null, loading, error };
}
