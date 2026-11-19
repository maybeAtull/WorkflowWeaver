import type { TemplateField, TemplateInfo } from "../api/types";
import { inputBase, inputInvalid } from "../components/ui";
import { counterState } from "./formModel";
import { fieldId } from "./ids";

interface Props {
  template: TemplateInfo;
  values: Record<string, string>;
  // Only the problems that should be shown now (a field's problem appears once it has been touched).
  errors: Record<string, string>;
  disabled: boolean;
  onChange: (key: string, value: string) => void;
  onBlur: (key: string) => void;
}

function Field({
  template,
  field,
  value,
  error,
  disabled,
  onChange,
  onBlur,
}: {
  template: TemplateInfo;
  field: TemplateField;
  value: string;
  error: string | undefined;
  disabled: boolean;
  onChange: (value: string) => void;
  onBlur: () => void;
}) {
  const id = fieldId(template, field.key);
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const counter = counterState(value.length, field.max_length);
  const common = {
    id,
    value,
    disabled,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": [field.help ? helpId : "", error ? errorId : ""].join(" ").trim() || undefined,
    className: `${inputBase} ${error ? inputInvalid : ""}`,
    onBlur,
  };

  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-200">
        {field.label}
        {!field.required && <span className="ml-1.5 font-normal text-slate-500">(optional)</span>}
      </label>
      {field.kind === "longtext" ? (
        <textarea {...common} rows={7} onChange={(e) => onChange(e.target.value)} />
      ) : field.kind === "choice" ? (
        <select {...common} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option>
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      ) : (
        <input {...common} type="text" onChange={(e) => onChange(e.target.value)} />
      )}
      <div className="mt-1.5 flex items-start justify-between gap-4 text-xs">
        <div>
          {field.help && (
            <p id={helpId} className="text-slate-500">
              {field.help}
            </p>
          )}
          {error && (
            <p id={errorId} role="alert" className="mt-1 text-rose-300">
              {error}
            </p>
          )}
        </div>
        {counter.visible && (
          <span className={counter.over ? "text-rose-300" : "text-slate-500"}>{counter.text}</span>
        )}
      </div>
    </div>
  );
}

// The form for one template, built from its list of fields.
export default function TemplateForm({ template, values, errors, disabled, onChange, onBlur }: Props) {
  return (
    <div className="space-y-5">
      {template.fields.map((field) => (
        <Field
          key={field.key}
          template={template}
          field={field}
          value={values[field.key] ?? ""}
          error={errors[field.key]}
          disabled={disabled}
          onChange={(value) => onChange(field.key, value)}
          onBlur={() => onBlur(field.key)}
        />
      ))}
    </div>
  );
}
