// A few small icons, drawn inline so the app needs no icon library. All use the current text colour,
// so they follow whatever they sit in, and are hidden from screen readers (the text next to them
// carries the meaning).
import type { SVGProps } from "react";

function Icon(props: SVGProps<SVGSVGElement> & { children: React.ReactNode }) {
  const { children, ...rest } = props;
  return (
    <svg
      viewBox="0 0 24 24"
      width="1em"
      height="1em"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {children}
    </svg>
  );
}

type IconProps = SVGProps<SVGSVGElement>;

// The brand mark: two stitched strokes, like a thread being woven.
export const LogoIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 7c4 0 4 5 8 5s4-5 8-5" />
    <path d="M4 17c4 0 4-5 8-5s4 5 8 5" />
  </Icon>
);

export const NotesIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M6 3h9l4 4v14H6z" />
    <path d="M15 3v4h4M9 12h7M9 16h5" />
  </Icon>
);

export const SprintIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 21V4M5 4h11l-2 4 2 4H5" />
  </Icon>
);

export const IncidentIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3 2.5 20h19z" />
    <path d="M12 10v4M12 17.2v.1" />
  </Icon>
);

export const PencilIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M4 20l1-4L16.5 4.5a2 2 0 0 1 3 3L8 19z" />
    <path d="M14 7l3 3" />
  </Icon>
);

export const SparkIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M18 6l-2.5 2.5M8.5 15.5 6 18" />
  </Icon>
);

export const ArrowRightIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Icon>
);

export const ChevronIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 6l6 6-6 6" />
  </Icon>
);

export const AlertIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7.5v5M12 16v.1" />
  </Icon>
);

export const CheckIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M5 12.5l4.5 4.5L19 7.5" />
  </Icon>
);

export const HistoryIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Icon>
);

export const DownloadIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12 4v11M7 11l5 5 5-5M5 20h14" />
  </Icon>
);

export const CircleIcon =(p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="8" />
  </Icon>
);

// A quarter ring; the caller makes it spin (and stops that for people who prefer less motion).
export const SpinnerIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="12" cy="12" r="9" opacity={0.25} />
    <path d="M21 12a9 9 0 0 0-9-9" />
  </Icon>
);

export const RetryIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M20 12a8 8 0 1 1-2.4-5.7M20 4v5h-5" />
  </Icon>
);

export const UndoIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 14 4 9l5-5M4 9h10a6 6 0 0 1 0 12h-3" />
  </Icon>
);

export const ExternalIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </Icon>
);

// Which icon belongs to which template (anything unknown gets the spark).
export const TEMPLATE_ICONS: Record<string, (p: IconProps) => React.JSX.Element> = {
  meeting_to_tasks: NotesIcon,
  sprint_planning: SprintIcon,
  incident_response: IncidentIcon,
};
