import { createContext, useContext } from "react";

export const EventsCtx = createContext({ events: [], refresh: () => {}, health: {} });
export const useEvents = () => useContext(EventsCtx);
