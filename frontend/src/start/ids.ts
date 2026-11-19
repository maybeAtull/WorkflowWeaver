import type { TemplateInfo } from "../api/types";

// The id of a template field's input; also used to move focus to the first problem on submit.
export const fieldId = (template: TemplateInfo, key: string) => `${template.id}-${key}`;

// The input id of the free-text box.
export const FREE_TEXT_ID = "free-text-request";

// The card id for "write your own" (it is not a real template).
export const FREE_CHOICE = "free";
