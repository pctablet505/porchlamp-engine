// fill.js — copy templates filled with a page's values, and the hooks the verifier reads them back by.
//
// A value a view prints from its data sits inside `<span data-v="<id>">` (`hook`): scripts/verify_static_export.py
// parses the written page, reads every hook and compares it with the value it works out itself from the API.
// A copy.js template names its values as `{slot}`s (`fill`), so the words stay in copy.js and the numbers stay
// hooked; the template's own text is escaped, the slots are markup the caller built (a hook, a link).

import { escapeHtml } from "./table.js";

/** `text` as the value of the hook `id`. */
export function hook(id, text) {
  return `<span data-v="${escapeHtml(id)}">${escapeHtml(text)}</span>`;
}

/** `template` with each `{slot}` replaced by `slots[slot]` (markup); a slot the caller did not give is an error. */
export function fill(template, slots) {
  return String(template)
    .split(/(\{[a-z][a-z0-9_]*\})/)
    .map((part) => {
      const slot = /^\{([a-z][a-z0-9_]*)\}$/.exec(part);
      if (!slot) return escapeHtml(part);
      if (!Object.prototype.hasOwnProperty.call(slots, slot[1])) throw new Error(`no value for the slot {${slot[1]}}`);
      return slots[slot[1]];
    })
    .join("");
}
