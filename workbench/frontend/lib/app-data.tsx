"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { api } from "./api";
import type {
  ConversationInfo,
  ModelSelection,
  ProviderInfo,
  ToolInfo,
} from "./types";
import { selectionKey } from "./types";

interface AppContextValue {
  // 模型
  providers: ProviderInfo[];
  providersLoading: boolean;
  refreshProviders: () => Promise<void>;
  selection: ModelSelection | null;
  setSelection: (s: ModelSelection) => void;
  // 会话
  conversations: ConversationInfo[];
  refreshConversations: () => Promise<void>;
  // 工具（agent/skill）
  tools: ToolInfo[];
  refreshTools: () => Promise<void>;
  activeTool: ToolInfo | null;
  setActiveTool: (t: ToolInfo | null) => void;
}

const AppContext = createContext<AppContextValue | null>(null);

const MODEL_KEY = "wb-model-selection";

export function AppDataProvider({ children }: { children: ReactNode }) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [providersLoading, setProvidersLoading] = useState(true);
  const [conversations, setConversations] = useState<ConversationInfo[]>([]);
  const [selection, setSelectionState] = useState<ModelSelection | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [activeTool, setActiveTool] = useState<ToolInfo | null>(null);

  const refreshTools = useCallback(async () => {
    try {
      setTools(await api<ToolInfo[]>("/api/tools"));
    } catch {
      setTools([]);
    }
  }, []);

  const refreshProviders = useCallback(async () => {
    setProvidersLoading(true);
    try {
      const list = await api<ProviderInfo[]>("/api/providers");
      setProviders(list);
      setSelectionState((prev) => {
        // 恢复上次选择；失效则取第一个可用模型
        if (prev && list.some((p) => p.id === prev.providerId && p.models.some((m) => m.id === prev.modelId))) {
          return prev;
        }
        const saved = typeof window !== "undefined" ? localStorage.getItem(MODEL_KEY) : null;
        if (saved) {
          try {
            const s = JSON.parse(saved) as ModelSelection;
            if (list.some((p) => p.id === s.providerId && p.models.some((m) => m.id === s.modelId))) {
              return s;
            }
          } catch {
            /* ignore */
          }
        }
        const p = list.find((x) => x.models.length > 0);
        if (!p) return null;
        return {
          providerId: p.id,
          providerName: p.name,
          modelId: p.models[0].id,
          modelLabel: p.models[0].label,
        };
      });
    } catch {
      setProviders([]);
    } finally {
      setProvidersLoading(false);
    }
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api<ConversationInfo[]>("/api/conversations"));
    } catch {
      setConversations([]);
    }
  }, []);

  useEffect(() => {
    refreshProviders();
    refreshConversations();
    refreshTools();
  }, [refreshProviders, refreshConversations, refreshTools]);

  const setSelection = useCallback((s: ModelSelection) => {
    setSelectionState(s);
    localStorage.setItem(MODEL_KEY, JSON.stringify(s));
    window.dispatchEvent(new Event("wb-model-changed"));
  }, []);

  // 跨组件同步（多页面实例共享 localStorage 时保持一致）
  useEffect(() => {
    const onModel = () => {
      const saved = localStorage.getItem(MODEL_KEY);
      if (saved) {
        try {
          setSelectionState(JSON.parse(saved));
        } catch {
          /* ignore */
        }
      }
    };
    window.addEventListener("wb-model-changed", onModel);
    return () => window.removeEventListener("wb-model-changed", onModel);
  }, []);

  return (
    <AppContext.Provider
      value={{
        providers,
        providersLoading,
        refreshProviders,
        selection,
        setSelection,
        conversations,
        refreshConversations,
        tools,
        refreshTools,
        activeTool,
        setActiveTool,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useAppData() {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useAppData 必须在 AppDataProvider 内使用");
  return ctx;
}

export { selectionKey };
