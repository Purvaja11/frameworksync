// stdin: {"source": "...", "edits": [...]}  ->  stdout: {"ok":bool,"reason":..,"result":..}
const { planEdits, applyToString } = require("../edits");
let input = "";
process.stdin.on("data", (d) => (input += d));
process.stdin.on("end", () => {
    const { source, edits } = JSON.parse(input);
    const lines = source.split("\n");
    const plan = planEdits(lines, edits);
    if (!plan.ok) return console.log(JSON.stringify({ ok: false, reason: plan.reason }));
    console.log(JSON.stringify({ ok: true, result: applyToString(source, plan.ranges) }));
});
