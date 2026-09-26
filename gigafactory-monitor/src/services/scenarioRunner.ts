import type { DemoScenario } from '../config/demoScenarios';
import { triggerAlert } from './alertStore';

/**
 * Plays a demo scenario by sending each step through triggerAlert(...) after
 * its delay — the same path a backend event would take. Several scenarios may
 * run at once; CLEAR ALERTS cancels everything still pending.
 */

interface RunningScenario {
  scenarioId: string;
  timers: number[];
}

let running: RunningScenario[] = [];
let runningIds: readonly string[] = [];
const listeners = new Set<() => void>();

function publish(): void {
  runningIds = running.map((r) => r.scenarioId);
  listeners.forEach((listener) => listener());
}

function finish(entry: RunningScenario): void {
  running = running.filter((r) => r !== entry);
  publish();
}

export function runScenario(scenario: DemoScenario): void {
  const entry: RunningScenario = { scenarioId: scenario.id, timers: [] };
  const lastDelay = Math.max(0, ...scenario.steps.map((s) => s.delayMs));
  running = [...running, entry];
  publish();

  for (const step of scenario.steps) {
    if (step.delayMs <= 0) triggerAlert(step.alert);
    else entry.timers.push(window.setTimeout(() => triggerAlert(step.alert), step.delayMs));
  }
  entry.timers.push(window.setTimeout(() => finish(entry), lastDelay + 400));
}

/** Cancels all pending scenario steps (already raised alerts stay). */
export function cancelScenario(): void {
  if (running.length === 0) return;
  running.forEach((r) => r.timers.forEach((t) => window.clearTimeout(t)));
  running = [];
  publish();
}

/** Ids of scenarios that still have pending steps (stable reference). */
export function getRunningScenarioIds(): readonly string[] {
  return runningIds;
}

export function subscribeToScenario(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
