import { inputBase, inputInvalid } from "../components/ui";
import { FREE_TEXT_LIMIT, counterState } from "./formModel";
import { FREE_TEXT_ID } from "./ids";

interface Props {
  text: string;
  error: string | null;
  disabled: boolean;
  onChange: (text: string) => void;
  onBlur: () => void;
}

// For anything the templates do not cover: the user simply describes what they want.
export default function FreeTextForm({ text, error, disabled, onChange, onBlur }: Props) {
  const counter = counterState(text.length, FREE_TEXT_LIMIT);
  return (
    <div>
      <label htmlFor={FREE_TEXT_ID} className="mb-1.5 block text-sm font-medium text-slate-200">
        Describe your workflow
      </label>
      <textarea
        id={FREE_TEXT_ID}
        rows={9}
        value={text}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        aria-describedby={`${FREE_TEXT_ID}-help${error ? ` ${FREE_TEXT_ID}-error` : ""}`}
        className={`${inputBase} ${error ? inputInvalid : ""}`}
        placeholder="For example: Put these notes in Notion with the action items, save a copy in Google Drive, and tell the team in chat."
        onChange={(e) => onChange(e.target.value)}
        onBlur={onBlur}
      />
      <div className="mt-1.5 flex items-start justify-between gap-4 text-xs">
        <div>
          <p id={`${FREE_TEXT_ID}-help`} className="text-slate-500">
            Say what to create and where, and paste any notes the agent should use.
          </p>
          {error && (
            <p id={`${FREE_TEXT_ID}-error`} role="alert" className="mt-1 text-rose-300">
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
