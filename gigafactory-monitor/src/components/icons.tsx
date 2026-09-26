import type { ReactNode } from 'react';
import type { Severity } from '../types/alerts';

/**
 * Inline SVG icons (no icon font / external assets).
 * Severity icons use a distinct SHAPE per level so severity is never
 * communicated by colour alone:
 *   LOW = circle "i", MEDIUM = triangle, HIGH = diamond, CRITICAL = octagon.
 */

interface IconProps {
  size?: number;
  className?: string;
}

const GLYPH = 'var(--glyph, #16181b)';

export function SeverityIcon({ severity, size = 18, className }: IconProps & { severity: Severity }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 20 20"
      aria-hidden="true"
      focusable="false"
      data-severity-icon={severity}
    >
      {severity === 'low' && (
        <>
          <circle cx="10" cy="10" r="8.6" fill="currentColor" />
          <circle cx="10" cy="6.1" r="1.25" fill={GLYPH} />
          <rect x="9" y="8.4" width="2" height="6.6" rx="1" fill={GLYPH} />
        </>
      )}
      {severity === 'medium' && (
        <>
          <path d="M10 1.6 L19.2 17.8 H0.8 Z" fill="currentColor" strokeLinejoin="round" />
          <rect x="9" y="7" width="2" height="5.8" rx="1" fill={GLYPH} />
          <circle cx="10" cy="15.1" r="1.2" fill={GLYPH} />
        </>
      )}
      {severity === 'high' && (
        <>
          <path d="M10 0.8 L19.2 10 L10 19.2 L0.8 10 Z" fill="currentColor" />
          <rect x="9" y="5.2" width="2" height="6.4" rx="1" fill={GLYPH} />
          <circle cx="10" cy="14" r="1.2" fill={GLYPH} />
        </>
      )}
      {severity === 'critical' && (
        <>
          <path d="M6.3 1 H13.7 L19 6.3 V13.7 L13.7 19 H6.3 L1 13.7 V6.3 Z" fill="currentColor" />
          <rect x="9" y="4.8" width="2" height="6.8" rx="1" fill={GLYPH} />
          <circle cx="10" cy="14.4" r="1.25" fill={GLYPH} />
        </>
      )}
    </svg>
  );
}

function Svg({ size = 20, className, children }: IconProps & { children: ReactNode }) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

export function IconPlus(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M12 5v14M5 12h14" />
    </Svg>
  );
}

export function IconMinus(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 12h14" />
    </Svg>
  );
}

/** Four corner brackets: "fit whole plant". */
export function IconOverview(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5" />
      <rect x="9" y="9" width="6" height="6" rx="0.5" />
    </Svg>
  );
}

export function IconSpeaker(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      <path d="M15.5 9a4.5 4.5 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11" />
    </Svg>
  );
}

export function IconSpeakerOff(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 9.5h3.5L12 5.5v13l-4.5-4H4z" />
      <path d="M16 9.5l5 5M21 9.5l-5 5" />
    </Svg>
  );
}

export function IconChevron({ direction = 'down', ...props }: IconProps & { direction?: 'up' | 'down' | 'left' | 'right' }) {
  const d = {
    down: 'M6 9l6 6 6-6',
    up: 'M6 15l6-6 6 6',
    left: 'M15 6l-6 6 6 6',
    right: 'M9 6l6 6-6 6',
  }[direction];
  return (
    <Svg {...props}>
      <path d={d} />
    </Svg>
  );
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 6l12 12M18 6L6 18" />
    </Svg>
  );
}

export function IconCheck(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </Svg>
  );
}

export function IconPlay(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 5.5v13l10-6.5z" fill="currentColor" />
    </Svg>
  );
}

export function IconTrash(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 7h16M9 7V4.5h6V7M6.5 7l1 12.5h9l1-12.5" />
    </Svg>
  );
}

export function IconBolt(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13 3L5 13.5h6L10 21l8-10.5h-6z" />
    </Svg>
  );
}

export function IconEdit(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 20h4L19 9l-4-4L4 16z" />
      <path d="M13.5 6.5l4 4" />
    </Svg>
  );
}

export function IconCopy(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="8.5" y="8.5" width="11" height="11" rx="1.5" />
      <path d="M15.5 8.5V5.5a1 1 0 0 0-1-1h-9a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h3" />
    </Svg>
  );
}

export function IconUndo(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M9 7L4.5 11.5 9 16" />
      <path d="M5 11.5h9a5 5 0 0 1 0 10h-2" />
    </Svg>
  );
}

export function IconTarget(props: IconProps) {
  return (
    <Svg {...props}>
      <circle cx="12" cy="12" r="7.5" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    </Svg>
  );
}

export function IconDetails(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="4" y="4" width="16" height="16" rx="1.5" />
      <path d="M8 9h8M8 12.5h8M8 16h5" />
    </Svg>
  );
}

/** Product mark: a stylised factory hall grid. */
export function IconPlantMark(props: IconProps) {
  return (
    <svg
      className={props.className}
      width={props.size ?? 26}
      height={props.size ?? 26}
      viewBox="0 0 26 26"
      aria-hidden="true"
      focusable="false"
    >
      <rect x="1" y="1" width="24" height="24" rx="3" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <rect x="5" y="5" width="7" height="5" fill="currentColor" opacity="0.9" />
      <rect x="14" y="5" width="7" height="5" fill="currentColor" opacity="0.55" />
      <rect x="5" y="12" width="16" height="3" fill="currentColor" opacity="0.75" />
      <rect x="5" y="17" width="10" height="4" fill="currentColor" opacity="0.4" />
      <rect x="17" y="17" width="4" height="4" fill="var(--sev-high)" />
    </svg>
  );
}
