import type { TemplateInfo } from "../api/types";
import { PencilIcon, SparkIcon, TEMPLATE_ICONS } from "../components/icons";
import { FREE_CHOICE } from "./ids";

interface Props {
  templates: TemplateInfo[];
  selected: string;
  disabled: boolean;
  onSelect: (id: string) => void;
}

function Card({
  title,
  description,
  icon,
  active,
  disabled,
  onClick,
}: {
  title: string;
  description: string;
  icon: React.ReactNode;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      disabled={disabled}
      onClick={onClick}
      className={
        "group flex h-full flex-col gap-3 rounded-3xl border p-5 text-left transition " +
        (active
          ? "border-violet-400/50 bg-violet-500/10 shadow-[0_0_44px_-12px_rgba(139,92,246,0.6)]"
          : "border-white/10 bg-white/[0.03] hover:border-white/20 hover:bg-white/[0.06]")
      }
    >
      <span
        className={
          "flex h-10 w-10 items-center justify-center rounded-2xl text-xl " +
          (active ? "bg-violet-400/20 text-violet-200" : "bg-white/5 text-slate-300")
        }
      >
        {icon}
      </span>
      <span className="text-base font-medium text-slate-100">{title}</span>
      <span className="text-sm leading-relaxed text-slate-400">{description}</span>
    </button>
  );
}

// One card per template, plus a last card for describing something else.
export default function TemplateCards({ templates, selected, disabled, onSelect }: Props) {
  return (
    <div role="group" aria-label="Choose a workflow" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {templates.map((template) => {
        const TemplateIcon = TEMPLATE_ICONS[template.id] ?? SparkIcon;
        return (
          <Card
            key={template.id}
            title={template.name}
            description={template.description}
            icon={<TemplateIcon />}
            active={selected === template.id}
            disabled={disabled}
            onClick={() => onSelect(template.id)}
          />
        );
      })}
      <Card
        title="Write your own"
        description="Describe any workflow in your own words."
        icon={<PencilIcon />}
        active={selected === FREE_CHOICE}
        disabled={disabled}
        onClick={() => onSelect(FREE_CHOICE)}
      />
    </div>
  );
}
