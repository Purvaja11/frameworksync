"use strict";
/*
 * Pure edit planning (no VS Code API) so it can be unit-tested with node.
 *
 * Input : `lines` = the document's lines (no line breaks),
 *         `edits` = [{start_line, end_line, new_text, old_text}] (1-based, inclusive).
 * Output: { ok, reason?, ranges: [{sl, sc, el, ec, text}] } (0-based positions).
 *
 * Guarantees:
 *   - an edit is refused if the lines no longer match `old_text` (file changed);
 *   - replacing text never swallows the line break after it;
 *   - deleting lines removes whole lines, including their line break;
 *   - overlapping edits are refused.
 */

const norm = (s) => String(s).replace(/\r\n/g, "\n");

function planEdits(lines, edits) {
    const n = lines.length;
    const ranges = [];

    for (const e of edits) {
        const start = Math.max(1, Number(e.start_line || 1));
        const end = Math.max(start, Number(e.end_line || start));

        if (end > n) {
            return { ok: false, reason: `lines ${start}-${end} are outside the file (${n} lines)` };
        }

        if (typeof e.old_text === "string") {
            const actual = lines.slice(start - 1, end).join("\n");
            if (norm(actual) !== norm(e.old_text)) {
                return { ok: false, reason: `lines ${start}-${end} changed since the file was analysed` };
            }
        }

        const text = norm(e.new_text ?? "");

        if (text !== "") {
            ranges.push({ sl: start - 1, sc: 0, el: end - 1, ec: lines[end - 1].length, text });
        } else if (end < n) {
            ranges.push({ sl: start - 1, sc: 0, el: end, ec: 0, text: "" });
        } else if (start > 1) {
            ranges.push({ sl: start - 2, sc: lines[start - 2].length, el: end - 1, ec: lines[end - 1].length, text: "" });
        } else {
            ranges.push({ sl: 0, sc: 0, el: end - 1, ec: lines[end - 1].length, text: "" });
        }
    }

    const sorted = [...ranges].sort((a, b) => a.sl - b.sl || a.sc - b.sc);
    for (let i = 1; i < sorted.length; i++) {
        const a = sorted[i - 1];
        const b = sorted[i];
        if (b.sl < a.el || (b.sl === a.el && b.sc < a.ec)) {
            return { ok: false, reason: "two edits overlap" };
        }
    }

    return { ok: true, ranges };
}

/* Apply planned ranges to a plain string (used by tests / CLI). */
function applyToString(source, ranges) {
    const lines = source.split("\n");
    const offset = (l, c) => lines.slice(0, l).reduce((sum, t) => sum + t.length + 1, 0) + c;
    let text = source;
    for (const r of [...ranges].sort((a, b) => b.sl - a.sl || b.sc - a.sc)) {
        text = text.slice(0, offset(r.sl, r.sc)) + r.text + text.slice(offset(r.el, r.ec));
    }
    return text;
}

module.exports = { planEdits, applyToString };
