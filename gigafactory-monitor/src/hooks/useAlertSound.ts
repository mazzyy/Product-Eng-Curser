import { useCallback, useEffect, useRef, useState } from 'react';
import { severityRank } from '../config/severity';
import type { Severity } from '../types/alerts';

/**
 * Severity sounds synthesised with the Web Audio API (no audio files).
 *
 *   LOW       one quiet sine tone
 *   MEDIUM    two short beeps
 *   HIGH      three stronger beeps
 *   CRITICAL  repeating two-tone alarm for ~3.5 s
 *
 * Browsers block audio until the user interacts with the page, so the
 * AudioContext is only created/resumed inside a real user gesture
 * (pointerdown / keydown / touchend). Alerts that arrive before any
 * interaction are shown silently.
 */

type AudioContextCtor = typeof AudioContext;

interface AudioChain {
  ctx: AudioContext;
  input: GainNode;
}

interface PlayingPattern {
  severity: Severity;
  gain: GainNode;
  endsAt: number;
}

const MASTER_VOLUME = 0.5;

function getAudioContextCtor(): AudioContextCtor | undefined {
  if (typeof window === 'undefined') return undefined;
  const w = window as unknown as { AudioContext?: AudioContextCtor; webkitAudioContext?: AudioContextCtor };
  return w.AudioContext ?? w.webkitAudioContext;
}

function createChain(Ctor: AudioContextCtor): AudioChain {
  const ctx = new Ctor();
  const master = ctx.createGain();
  master.gain.value = MASTER_VOLUME;

  // Soften harsh harmonics and keep peaks under control.
  const lowpass = ctx.createBiquadFilter();
  lowpass.type = 'lowpass';
  lowpass.frequency.value = 3200;

  const compressor = ctx.createDynamicsCompressor();
  compressor.threshold.value = -18;
  compressor.knee.value = 12;
  compressor.ratio.value = 4;

  master.connect(lowpass);
  lowpass.connect(compressor);
  compressor.connect(ctx.destination);
  return { ctx, input: master };
}

function beep(
  ctx: AudioContext,
  destination: AudioNode,
  start: number,
  duration: number,
  frequency: number,
  type: OscillatorType,
  peak: number,
): void {
  const osc = ctx.createOscillator();
  const envelope = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(frequency, start);

  const attackEnd = start + 0.012;
  const releaseStart = start + Math.max(0.03, duration - 0.05);
  envelope.gain.setValueAtTime(0.0001, start);
  envelope.gain.exponentialRampToValueAtTime(peak, attackEnd);
  envelope.gain.setValueAtTime(peak, releaseStart);
  envelope.gain.exponentialRampToValueAtTime(0.0001, start + duration);

  osc.connect(envelope);
  envelope.connect(destination);
  osc.start(start);
  osc.stop(start + duration + 0.02);
}

/** Schedules the pattern and returns its duration in seconds. */
function schedulePattern(ctx: AudioContext, out: AudioNode, severity: Severity, t: number): number {
  switch (severity) {
    case 'low':
      beep(ctx, out, t, 0.16, 880, 'sine', 0.22);
      return 0.2;
    case 'medium':
      for (let i = 0; i < 2; i++) beep(ctx, out, t + i * 0.2, 0.12, 988, 'triangle', 0.3);
      return 0.36;
    case 'high':
      for (let i = 0; i < 3; i++) {
        const s = t + i * 0.2;
        beep(ctx, out, s, 0.14, 1175, 'square', 0.07);
        beep(ctx, out, s, 0.14, 587, 'sine', 0.22);
      }
      return 0.62;
    case 'critical': {
      const cycles = 7; // 7 × 0.5 s ≈ 3.5 s
      for (let i = 0; i < cycles; i++) {
        const s = t + i * 0.5;
        beep(ctx, out, s, 0.22, 932, 'square', 0.075);
        beep(ctx, out, s, 0.22, 466, 'sine', 0.16);
        beep(ctx, out, s + 0.25, 0.22, 698, 'square', 0.075);
        beep(ctx, out, s + 0.25, 0.22, 349, 'sine', 0.16);
      }
      return cycles * 0.5;
    }
  }
}

export interface AlertSound {
  /** Plays the pattern for a severity (ignored when muted or not yet unlocked). */
  play: (severity: Severity) => void;
  /** Fades out whatever is currently playing. */
  stop: () => void;
  /** True once the browser has allowed audio (after the first interaction). */
  unlocked: boolean;
  /** False if the browser has no Web Audio support. */
  supported: boolean;
}

export function useAlertSound(muted: boolean): AlertSound {
  const chainRef = useRef<AudioChain | null>(null);
  const playingRef = useRef<PlayingPattern | null>(null);
  const mutedRef = useRef(muted);
  const [unlocked, setUnlocked] = useState(false);
  const supported = getAudioContextCtor() !== undefined;

  // Create / resume the AudioContext only inside genuine user gestures.
  useEffect(() => {
    const unlock = () => {
      const Ctor = getAudioContextCtor();
      if (!Ctor) return;
      if (!chainRef.current) {
        try {
          chainRef.current = createChain(Ctor);
        } catch (error) {
          console.warn('[sound] Web Audio unavailable', error);
          return;
        }
      }
      const { ctx } = chainRef.current;
      if (ctx.state === 'suspended') {
        ctx
          .resume()
          .then(() => setUnlocked(true))
          .catch(() => undefined);
      } else if (ctx.state === 'running') {
        setUnlocked(true);
      }
    };

    const events = ['pointerdown', 'keydown', 'touchend'] as const;
    events.forEach((type) => window.addEventListener(type, unlock, true));
    return () => events.forEach((type) => window.removeEventListener(type, unlock, true));
  }, []);

  const stop = useCallback(() => {
    const playing = playingRef.current;
    const chain = chainRef.current;
    playingRef.current = null;
    if (!playing || !chain) return;
    const t = chain.ctx.currentTime;
    const param = playing.gain.gain;
    param.cancelScheduledValues(t);
    param.setValueAtTime(param.value, t);
    param.linearRampToValueAtTime(0, t + 0.08);
    window.setTimeout(() => playing.gain.disconnect(), 250);
  }, []);

  const play = useCallback(
    (severity: Severity) => {
      if (mutedRef.current) return;
      const chain = chainRef.current;
      if (!chain || chain.ctx.state === 'closed') return;
      const { ctx } = chain;

      // A more severe pattern that is still playing is not interrupted.
      const current = playingRef.current;
      if (current && ctx.currentTime < current.endsAt && severityRank(current.severity) > severityRank(severity)) {
        return;
      }
      stop();

      const gain = ctx.createGain();
      gain.gain.value = 1;
      gain.connect(chain.input);
      const start = ctx.currentTime + 0.03;
      const duration = schedulePattern(ctx, gain, severity, start);
      playingRef.current = { severity, gain, endsAt: start + duration };
    },
    [stop],
  );

  useEffect(() => {
    mutedRef.current = muted;
    if (muted) stop();
  }, [muted, stop]);

  return { play, stop, unlocked, supported };
}
