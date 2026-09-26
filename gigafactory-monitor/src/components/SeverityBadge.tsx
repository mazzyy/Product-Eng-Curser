import { SEVERITY_META } from '../config/severity';
import type { Severity } from '../types/alerts';
import { SeverityIcon } from './icons';

interface SeverityBadgeProps {
  severity: Severity;
  /** solid = filled chip, outline = coloured text on panel, icon = icon only. */
  variant?: 'solid' | 'outline' | 'icon';
  size?: 'sm' | 'md' | 'lg';
  /** Render muted (e.g. acknowledged alerts). */
  muted?: boolean;
}

const ICON_SIZE = { sm: 14, md: 16, lg: 20 } as const;

/** Severity is always shown as colour + icon shape + text label. */
export function SeverityBadge({ severity, variant = 'solid', size = 'md', muted = false }: SeverityBadgeProps) {
  const meta = SEVERITY_META[severity];
  if (variant === 'icon') {
    return (
      <span className="sev-icon" data-severity={severity} data-muted={muted || undefined} role="img" aria-label={meta.title}>
        <SeverityIcon severity={severity} size={ICON_SIZE[size]} />
      </span>
    );
  }
  return (
    <span className="sev-badge" data-severity={severity} data-variant={variant} data-size={size} data-muted={muted || undefined}>
      <SeverityIcon severity={severity} size={ICON_SIZE[size]} />
      <span className="sev-badge__label">{meta.label}</span>
    </span>
  );
}
