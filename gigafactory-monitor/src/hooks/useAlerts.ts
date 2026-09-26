import { useSyncExternalStore } from 'react';
import { getAlerts, subscribeToAlerts } from '../services/alertStore';
import type { FactoryAlert } from '../types/alerts';

/** React view of the alert store (re-renders only when alerts change). */
export function useAlerts(): readonly FactoryAlert[] {
  return useSyncExternalStore(subscribeToAlerts, getAlerts, getAlerts);
}
