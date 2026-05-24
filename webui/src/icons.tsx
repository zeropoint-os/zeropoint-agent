/**
 * Inline SVG icons for the inspector + actions.
 *
 * All icons use `currentColor` so they pick up the theme's text color
 * automatically. Sizing is via CSS / inline `width|height` props.
 *
 * Style: 1.5px stroke, rounded line caps/joins, no fill. Matches the
 * Metro aesthetic — thin, geometric, restrained.
 */

import type { JSX } from 'preact';

type SvgProps = JSX.SVGAttributes<SVGSVGElement>;

const common = {
    width: 16,
    height: 16,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    'stroke-width': 1.5,
    'stroke-linecap': 'round' as const,
    'stroke-linejoin': 'round' as const,
};

/** A chain-link icon — "this value is linked to another node." */
export function LinkIcon(props: SvgProps) {
    return (
        <svg {...common} {...props}>
            <path d="M10 14a3 3 0 0 0 4 0l3-3a3 3 0 0 0-4-4l-1 1" />
            <path d="M14 10a3 3 0 0 0-4 0l-3 3a3 3 0 0 0 4 4l1-1" />
        </svg>
    );
}

/** A broken-chain icon — "break this link." */
export function UnlinkIcon(props: SvgProps) {
    return (
        <svg {...common} {...props}>
            <path d="M10 14a3 3 0 0 0 4 0l1-1" />
            <path d="M14 10a3 3 0 0 0-4 0l-1 1" />
            <path d="M17 7l4-4" />
            <path d="M7 17l-4 4" />
        </svg>
    );
}
