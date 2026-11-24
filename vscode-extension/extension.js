"use strict";
const vscode = require("vscode");
const fs = require("fs");
const path = require("path");
const cp = require("child_process");
const { planEdits } = require("./edits");
const gitSafety = require("./git_safety");

let output;
let extContext;
let statusItem;
let watcherChild = null;
let extensionVersion = "";
const ui = { watcher: "starting", lastChanged: "", counts: { files: 0, suggestions: 0 }, staleFiles: 0, note: "" };

function updateStatus() {
    if (!statusItem) return;
    let text, tooltip, error = false;
    if (ui.watcher === "stopped") {
        text = "$(error) FrameworkSync: watcher stopped";
        tooltip = "The watcher is not running. Click to restart it.";
        error = true;
    } else if (ui.watcher === "foreign") {
        text = "$(warning) FrameworkSync: watcher owned by another process";
        tooltip = (ui.note || "Another watcher is running.") + " Click to restart it (kills the old one).";
    } else if (ui.watcher === "starting") {
        text = "$(sync~spin) FrameworkSync: starting...";
        tooltip = "Starting the watcher";
    } else {
        const last = ui.lastChanged ? ` · ${ui.lastChanged}` : "";
        text = `$(eye) FrameworkSync: ${ui.counts.suggestions} suggestion(s)${last}`;
        tooltip = `FrameworkSync ${extensionVersion} - watcher running. Files with results: ${ui.counts.files}` +
            (ui.staleFiles ? `. ${ui.staleFiles} file(s) waiting for re-analysis.` : "") + " Click to show the log.";
    }
    statusItem.text = text;
    statusItem.tooltip = tooltip;
    statusItem.command = ui.watcher === "running" ? "frameworksync.showLog" : "frameworksync.restartWatcher";
    statusItem.backgroundColor = error ? new vscode.ThemeColor("statusBarItem.errorBackground") : undefined;
    statusItem.show();
}

function log(message) {
    if (output) {
        output.appendLine(`[${new Date().toLocaleTimeString()}] ${message}`);
    }
}

/* ------------------------------------------------------------------ paths */

function getWorkspaceRoot() {
    return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || null;
}

function isProjectRoot(dir) {
    return (
        !!dir &&
        fs.existsSync(path.join(dir, "src", "frameworksync", "watcher.py")) &&
        fs.existsSync(path.join(dir, "pyproject.toml"))
    );
}

/* The FrameworkSync project (where uv/.venv/data live). It does NOT have to be
 * the open workspace: set "frameworksync.projectRoot" to watch any folder. */
function getFrameworkSyncRoot() {
    const configured = vscode.workspace.getConfiguration("frameworksync").get("projectRoot");
    const workspace = getWorkspaceRoot();
    const candidates = [configured, workspace, workspace && path.join(workspace, "frameworksync")];
    for (const candidate of candidates) {
        if (isProjectRoot(candidate)) {
            return candidate;
        }
    }
    return null;
}

function getResultsPath() {
    const configured = vscode.workspace.getConfiguration("frameworksync").get("resultsPath");
    if (configured) {
        return configured;
    }
    const root = getFrameworkSyncRoot() || getWorkspaceRoot();
    return root ? path.join(root, "data", "vscode_results.json") : null;
}

function normalize(filePath) {
    return path.resolve(filePath).replace(/\\/g, "/").toLowerCase();
}

/* ---------------------------------------------------------------- results
 * The last good parse is always kept. A missing / locked / half-written file
 * (which happens briefly on every save on Windows) never wipes anything.
 */

const store = { file: null, mtime: 0, size: 0, data: {}, index: new Map() };

function statResults() {
    const file = getResultsPath();
    if (!file) return null;
    try {
        const st = fs.statSync(file);
        return { file, mtime: st.mtimeMs, size: st.size };
    } catch {
        return null;
    }
}

function resultsChanged() {
    const st = statResults();
    return !!st && (st.file !== store.file || st.mtime !== store.mtime || st.size !== store.size);
}

function readResults() {
    const st = statResults();
    if (!st || !resultsChanged()) {
        return store.data;
    }
    try {
        const parsed = JSON.parse(fs.readFileSync(st.file, "utf8"));
        if (parsed && typeof parsed === "object") {
            store.file = st.file;
            store.mtime = st.mtime;
            store.size = st.size;
            store.data = parsed;
            store.index = new Map();
            for (const [key, entry] of Object.entries(parsed)) {
                store.index.set(normalize(entry.path || key), entry);
            }
        }
    } catch (error) {
        log(`Results file not readable yet (${error.message}); keeping previous results.`);
    }
    return store.data;
}

/* Results are only valid for the version of the file they were made from.
 * If the file was saved since (lines may have shifted), hide them until the
 * watcher publishes the new analysis - never offer a fix for old code. */
function isFresh(entry, filePath) {
    if (typeof entry.file_mtime !== "number") return true;  // results from an older format
    try {
        return Math.abs(fs.statSync(filePath).mtimeMs - entry.file_mtime) < 2;
    } catch {
        return false;
    }
}

function findEntry(filePath) {
    readResults();
    const entry = store.index.get(normalize(filePath)) || null;
    return entry && isFresh(entry, filePath) ? entry : null;
}

/* ------------------------------------------------------------- line helpers */

const clampLine = (document, line) =>
    Math.min(Math.max(0, line), Math.max(0, document.lineCount - 1));

function suggestionLine(s) {
    return Math.max(1, Number(s?.line || s?.signal?.line || s?.proposal?.start_line || 1));
}

function proposalStart(s) {
    const p = s?.proposal;
    return Math.max(1, Number(p?.start_line || p?.line || suggestionLine(s)));
}

function proposalEnd(s) {
    const p = s?.proposal;
    return Math.max(proposalStart(s), Number(p?.end_line || p?.line || suggestionLine(s)));
}

function lineRange(document, startLine, endLine) {
    const s = clampLine(document, startLine - 1);
    const e = clampLine(document, endLine - 1);
    const endChar = document ? document.lineAt(e).text.length : 1000;
    return new vscode.Range(s, 0, Math.max(s, e), endChar);
}

/* -------------------------------------------------------------- diagnostics */

function buildDiagnostics(document, entry) {
    const diagnostics = [];
    const suggestions = entry.suggestions || [];

    for (const suggestion of suggestions) {
        const range = document
            ? lineRange(document, proposalStart(suggestion), proposalEnd(suggestion))
            : new vscode.Range(proposalStart(suggestion) - 1, 0, proposalEnd(suggestion) - 1, 1000);

        const proposal = suggestion.proposal;
        let message = suggestion.description || "FrameworkSync migration suggestion";
        if (proposal?.old_pattern && proposal?.new_pattern) {
            message += `: ${proposal.old_pattern} → ${proposal.new_pattern}`;
        }

        const diagnostic = new vscode.Diagnostic(range, message, vscode.DiagnosticSeverity.Warning);
        diagnostic.source = "FrameworkSync";
        diagnostic.code = `migration:${suggestion.migration_signal || "unknown"}`;
        diagnostics.push(diagnostic);
    }

    for (const issue of entry.issues || []) {
        // A suggestion on the same line for the same symbol already says this.
        const covered = suggestions.some(
            (s) => suggestionLine(s) === Number(issue.line) &&
                   (s.migration?.old_pattern || s.proposal?.old_pattern) === issue.old_pattern
        );
        if (covered) continue;

        const line = Math.max(1, Number(issue.line || 1));
        const range = document
            ? lineRange(document, line, line)
            : new vscode.Range(line - 1, 0, line - 1, 1000);

        const message =
            issue.description ||
            (issue.old_pattern && issue.new_pattern
                ? `"${issue.old_pattern}" is deprecated → use "${issue.new_pattern}"`
                : issue.reason || "FrameworkSync issue");

        const diagnostic = new vscode.Diagnostic(range, message, vscode.DiagnosticSeverity.Warning);
        diagnostic.source = "FrameworkSync";
        diagnostic.code = `issue:${issue.old_pattern || "unknown"}`;
        diagnostics.push(diagnostic);
    }

    return diagnostics;
}

function refreshDiagnostics(collection) {
    const results = readResults();
    const seen = new Set();
    let files = 0, issues = 0, suggestions = 0, staleFiles = 0;

    for (const [key, entry] of Object.entries(results)) {
        const resultPath = entry.path || key;
        if (!resultPath || !fs.existsSync(resultPath)) continue;
        if (!isFresh(entry, resultPath)) {
            staleFiles += 1;  // saved since the analysis: wait for the new one
            continue;
        }

        const uri = vscode.Uri.file(resultPath);
        const document = vscode.workspace.textDocuments.find(
            (d) => normalize(d.uri.fsPath) === normalize(resultPath)
        );

        collection.set(uri, buildDiagnostics(document, entry));
        seen.add(uri.toString());
        files += 1;
        issues += (entry.issues || []).length;
        suggestions += (entry.suggestions || []).length;
    }

    // Only files that really left the results are cleared (no clear-then-set flicker).
    const stale = [];
    collection.forEach((uri) => {
        if (!seen.has(uri.toString())) stale.push(uri);
    });
    stale.forEach((uri) => collection.delete(uri));

    ui.counts = { files, suggestions };
    ui.staleFiles = staleFiles;
    updateStatus();
    log(`Results loaded: files=${files}, issues=${issues}, suggestions=${suggestions}` +
        (staleFiles ? `, waiting for re-analysis=${staleFiles}` : ""));
}

/* -------------------------------------------------------------------- hover */

function escapeMarkdown(value) {
    return String(value)
        .replace(/\\/g, "\\\\").replace(/\*/g, "\\*").replace(/_/g, "\\_")
        .replace(/`/g, "\\`").replace(/\[/g, "\\[").replace(/\]/g, "\\]");
}

function findSuggestionAtPosition(document, position) {
    const entry = findEntry(document.uri.fsPath);
    if (!entry) return null;
    const line = position.line + 1;
    return (
        (entry.suggestions || []).find(
            (s) => line >= proposalStart(s) && line <= proposalEnd(s)
        ) || null
    );
}

function createSuggestionHover(suggestion) {
    const md = new vscode.MarkdownString();
    md.isTrusted = false;
    md.appendMarkdown("### FrameworkSync — Migration suggestion\n\n");

    if (suggestion.description) {
        md.appendMarkdown(`**${escapeMarkdown(suggestion.description)}**\n\n`);
    }

    if (suggestion.explanation) {
        md.appendMarkdown("**Why this migration is recommended**\n\n");
        md.appendMarkdown(`${escapeMarkdown(suggestion.explanation)}\n\n`);
    }

    const proposal = suggestion.proposal;
    if (proposal) {
        if (proposal.old_pattern || proposal.new_pattern) {
            md.appendMarkdown("**Migration**\n\n");
            md.appendMarkdown(
                `\`${escapeMarkdown(proposal.old_pattern || "current pattern")}\` → ` +
                `\`${escapeMarkdown(proposal.new_pattern || "new API")}\`\n\n`
            );
        }
        if (proposal.proposed_code) {
            md.appendMarkdown("**Proposed code**\n\n");
            md.appendCodeblock(proposal.proposed_code, "python");
            md.appendMarkdown("\n");
        }
        if (proposal.description) {
            md.appendMarkdown(`${escapeMarkdown(proposal.description)}\n\n`);
        }
        if (proposal.needs_review === true || proposal.applicable === false) {
            md.appendMarkdown("> ⚠️ FrameworkSync cannot apply this one automatically. Do these steps by hand:\n\n");
            const steps = Array.isArray(proposal.manual_steps) ? proposal.manual_steps : [];
            if (steps.length) {
                steps.forEach((step, i) => md.appendMarkdown(`**${i + 1}.** ${step}\n\n`));
            } else {
                md.appendMarkdown("_No step-by-step instructions are available for this pattern yet; see the sources below._\n\n");
            }
        }
    }

    const names = [];
    for (const source of suggestion.sources || []) {
        const name = typeof source === "string"
            ? source
            : source?.metadata?.source_name || source?.source_name || source?.name;
        if (name && !names.includes(name)) names.push(name);
    }
    if (names.length) {
        md.appendMarkdown("**Sources**\n\n");
        for (const name of names) md.appendMarkdown(`- ${escapeMarkdown(name)}\n`);
        md.appendMarkdown("\n");
    }

    const manual = !proposal || proposal.needs_review === true || proposal.applicable === false || !proposal.proposed_code;
    const hasSteps = Array.isArray(proposal?.manual_steps) && proposal.manual_steps.length > 0;
    if (!manual) {
        md.appendMarkdown("_Press Ctrl+. (or click the lightbulb) and choose the FrameworkSync Quick Fix to apply this automatically. You will be asked to commit the file first, so you can undo it._");
    } else if (hasSteps) {
        md.appendMarkdown("_This one is manual - there is no automatic fix. Follow the steps above (Ctrl+. → \"Show the manual steps\" opens them in a side panel)._");
    } else {
        md.appendMarkdown("_This one is manual - there is no automatic fix._");
    }    return new vscode.Hover(md);
}

function registerHover(context) {
    context.subscriptions.push(
        vscode.languages.registerHoverProvider(
            { scheme: "file", language: "python" },
            {
                provideHover(document, position) {
                    const suggestion = findSuggestionAtPosition(document, position);
                    return suggestion ? createSuggestionHover(suggestion) : null;
                },
            }
        )
    );
}

/* ------------------------------------------------------------- quick fixes */

function registerCodeActions(context) {
    context.subscriptions.push(
        vscode.languages.registerCodeActionsProvider(
            { scheme: "file", language: "python" },
            {
                provideCodeActions(document, range, codeActionContext) {
                    const entry = findEntry(document.uri.fsPath);
                    if (!entry) return [];

                    const first = range.start.line + 1;
                    const last = range.end.line + 1;
                    const actions = [];

                    for (const suggestion of entry.suggestions || []) {
                        const proposal = suggestion.proposal;
                        const hasManualSteps = Array.isArray(proposal?.manual_steps) && proposal.manual_steps.length > 0;
                        if (!proposal || (!proposal.proposed_code && !hasManualSteps)) continue;
                        if (last < proposalStart(suggestion) || first > proposalEnd(suggestion)) continue;

                        const code = `migration:${suggestion.migration_signal || "unknown"}`;
                        const diagnostics = (codeActionContext?.diagnostics || []).filter(
                            (d) => d.source === "FrameworkSync" && d.code === code
                        );

                        const executable =
                            !!proposal.proposed_code &&
                            proposal.applicable !== false &&
                            proposal.needs_review !== true;

                        if (executable) {
                            const accept = new vscode.CodeAction(
                                `FrameworkSync: Accept migration → ${proposal.new_pattern || "new API"}`,
                                vscode.CodeActionKind.QuickFix
                            );
                            accept.diagnostics = diagnostics;
                            accept.isPreferred = true;
                            accept.command = {
                                command: "frameworksync.applySuggestion",
                                title: "Apply FrameworkSync migration",
                                arguments: [document.uri, suggestion],
                            };
                            actions.push(accept);
                        } else {
                            const steps = Array.isArray(proposal.manual_steps) ? proposal.manual_steps : [];
                            const review = new vscode.CodeAction(
                                steps.length
                                    ? "FrameworkSync: Show the manual steps for this migration"
                                    : "FrameworkSync: Migration requires review",
                                vscode.CodeActionKind.QuickFix
                            );
                            review.diagnostics = diagnostics;
                            if (steps.length) {
                                review.command = {
                                    command: "frameworksync.showManualSteps",
                                    title: "Show manual steps",
                                    arguments: [document.uri, suggestion],
                                };
                            } else {
                                review.disabled = {
                                    reason: proposal.description || "This migration requires manual review.",
                                };
                            }
                            actions.push(review);
                        }
                    }
                    return actions;
                },
            },
            { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }
        )
    );
}

/* ------------------------------------------------------------ git safety net
 * No migration is started (automatic OR manual) until the file is committed,
 * so the original can always be restored with "Undo migration".
 */

const CHECKPOINT_KEY = "frameworksync.checkpoints";

function checkpointRequired() {
    return vscode.workspace.getConfiguration("frameworksync").get("requireGitCheckpoint") !== false;
}

function getCheckpoints() {
    return extContext?.workspaceState.get(CHECKPOINT_KEY, {}) || {};
}

async function saveCheckpoint(file, commit, label) {
    const all = getCheckpoints();
    all[normalize(file)] = { file, commit, label, time: Date.now() };
    await extContext?.workspaceState.update(CHECKPOINT_KEY, all);
}

/* Resolves to { commit } (file is committed), { skipped: true } (user knowingly
 * went on without git) or null (cancelled - do NOT migrate). */
async function ensureCheckpoint(document, label) {
    if (!checkpointRequired()) return { skipped: true };

    const file = document.uri.fsPath;
    const name = path.basename(file);

    if (document.isDirty) {
        vscode.window.showWarningMessage(
            `FrameworkSync: ${name} has unsaved changes. Save it (Ctrl+S) first, then try again.`
        );
        return null;
    }

    for (let attempt = 0; attempt < 4; attempt++) {
        const repo = await gitSafety.findRepo(file);

        if (!repo.ok && repo.reason === "git-missing") {
            const pick = await vscode.window.showWarningMessage(
                "Git was not found on this computer, so this migration could not be undone.",
                { modal: true }, "Continue without safety net"
            );
            return pick ? { skipped: true } : null;
        }

        if (!repo.ok) {
            const pick = await vscode.window.showWarningMessage(
                `${name} is not inside a git repository, so a migration could not be undone.`,
                { modal: true }, "Initialize git here", "Continue without safety net"
            );
            if (pick === "Initialize git here") {
                const init = await gitSafety.initRepo(path.dirname(file));
                if (!init.ok) {
                    vscode.window.showErrorMessage(`git init failed: ${init.error}`);
                    return null;
                }
                continue;  // check again, now it is a repo
            }
            return pick ? { skipped: true } : null;
        }

        const status = await gitSafety.fileState(file);

        if (status.state === "clean") {
            const head = await gitSafety.head(file);
            if (!head.ok) {
                vscode.window.showErrorMessage(`FrameworkSync could not read the git checkpoint: ${head.error}`);
                return null;
            }
            await saveCheckpoint(file, head.commit, label);
            log(`Git checkpoint for ${name}: ${head.commit}`);
            return { commit: head.commit };
        }

        if (status.state === "ignored") {
            const pick = await vscode.window.showWarningMessage(
                `${name} is ignored by git (.gitignore), so it cannot be committed and a migration could not be undone.`,
                { modal: true }, "Continue without safety net"
            );
            return pick ? { skipped: true } : null;
        }

        if (status.state === "error") {
            vscode.window.showErrorMessage(`FrameworkSync could not check git status: ${status.detail}`);
            return null;
        }

        // untracked or dirty: the user must commit first
        const why = status.state === "untracked"
            ? `${name} has never been committed.`
            : `${name} has uncommitted changes.`;
        const pick = await vscode.window.showWarningMessage(
            `${why} Commit it first, so you can undo the migration if something goes wrong.`,
            { modal: true }, "Commit this file now"
        );
        if (pick !== "Commit this file now") return null;

        const message = await vscode.window.showInputBox({
            title: `Commit ${name}`,
            prompt: "Commit message (only this file is committed)",
            value: `checkpoint before FrameworkSync migration (${label})`,
        });
        if (message === undefined) return null;

        const commit = await gitSafety.commitFile(file, message.trim() || `checkpoint before FrameworkSync migration (${label})`);
        if (!commit.ok) {
            vscode.window.showErrorMessage(`git commit failed: ${commit.error}`);
            return null;
        }
        // loop once more: re-check that the file is now clean
    }
    vscode.window.showErrorMessage("FrameworkSync could not get the file into a clean git state. Nothing was changed.");
    return null;
}

async function revertMigration(fileArg) {
    const all = getCheckpoints();
    let record = null;

    const active = fileArg || vscode.window.activeTextEditor?.document.uri.fsPath;
    if (active) record = all[normalize(active)] || null;

    if (!record) {
        const items = Object.values(all).sort((a, b) => b.time - a.time);
        if (!items.length) {
            vscode.window.showInformationMessage("FrameworkSync: there is no migration checkpoint to restore yet.");
            return;
        }
        if (items.length === 1) {
            record = items[0];
        } else {
            const pick = await vscode.window.showQuickPick(
                items.map((r) => ({
                    label: path.basename(r.file),
                    description: `${r.commit} · ${new Date(r.time).toLocaleString()}`,
                    detail: r.label,
                    record: r,
                })),
                { title: "Restore which file to its checkpoint?" }
            );
            if (!pick) return;
            record = pick.record;
        }
    }

    const name = path.basename(record.file);
    const document = await vscode.workspace.openTextDocument(vscode.Uri.file(record.file));
    if (document.isDirty) {
        vscode.window.showWarningMessage(`FrameworkSync: ${name} has unsaved changes. Save or discard them first.`);
        return;
    }

    const choice = await vscode.window.showWarningMessage(
        `Restore ${name} to the version committed as ${record.commit}? ` +
        "Everything changed in this file since then, including the migration, will be lost.",
        { modal: true }, "Restore original"
    );
    if (choice !== "Restore original") return;

    const result = await gitSafety.restoreFile(record.file, record.commit);
    if (!result.ok) {
        vscode.window.showErrorMessage(`FrameworkSync could not restore the file: ${result.error}`);
        return;
    }
    log(`Restored ${name} to ${record.commit}`);
    vscode.window.showInformationMessage(`FrameworkSync: ${name} restored to ${record.commit}.`);
}

async function applyProposal(document, suggestion, diagnostics) {
    const proposal = suggestion?.proposal;

    if (!proposal || !proposal.proposed_code) {
        vscode.window.showWarningMessage("FrameworkSync: no executable migration proposal is available.");
        return;
    }
    if (proposal.applicable === false || proposal.needs_review === true) {
        vscode.window.showWarningMessage(
            "FrameworkSync: this migration requires manual review and cannot be auto-applied."
        );
        return;
    }

    const edits =
        Array.isArray(proposal.edits) && proposal.edits.length
            ? proposal.edits
            : [{
                start_line: proposal.start_line || proposal.line,
                end_line: proposal.end_line || proposal.line,
                new_text: proposal.proposed_code,
                old_text: proposal.original_code,
            }];

    const lines = [];
    for (let i = 0; i < document.lineCount; i++) lines.push(document.lineAt(i).text);

    const plan = planEdits(lines, edits);
    if (!plan.ok) {
        log(`Quick fix refused: ${plan.reason}`);
        vscode.window.showWarningMessage(
            `FrameworkSync: ${plan.reason}. Save the file, wait a moment for the new analysis, then try again.`
        );
        refreshDiagnostics(diagnostics);
        return;
    }

    const choice = await vscode.window.showWarningMessage(
        `Apply FrameworkSync migration for ${suggestion.migration_signal || proposal.old_pattern || "this migration"}?`,
        { modal: true },
        "Apply"
    );
    if (choice !== "Apply") return;

    const label = suggestion.migration_signal || proposal.old_pattern || "migration";
    const checkpoint = await ensureCheckpoint(document, label);
    if (!checkpoint) {
        vscode.window.showInformationMessage("FrameworkSync: nothing was changed (no git checkpoint).");
        return;
    }

    // The dialog was open for a while: plan again against the current text.
    const freshLines = [];
    for (let i = 0; i < document.lineCount; i++) freshLines.push(document.lineAt(i).text);
    const freshPlan = planEdits(freshLines, edits);
    if (!freshPlan.ok) {
        vscode.window.showWarningMessage(`FrameworkSync: ${freshPlan.reason}. Nothing was changed.`);
        return;
    }

    const workspaceEdit = new vscode.WorkspaceEdit();
    for (const r of freshPlan.ranges) {
        workspaceEdit.replace(document.uri, new vscode.Range(r.sl, r.sc, r.el, r.ec), r.text);
    }

    if (!(await vscode.workspace.applyEdit(workspaceEdit))) {
        vscode.window.showErrorMessage("FrameworkSync could not apply the migration.");
        return;
    }

    await document.save();
    if (checkpoint.commit) {
        vscode.window.showInformationMessage(
            `FrameworkSync migration applied. Original saved in git as ${checkpoint.commit}.`,
            "Undo migration"
        ).then((pick) => {
            if (pick === "Undo migration") revertMigration(document.uri.fsPath);
        });
    } else {
        vscode.window.showInformationMessage("FrameworkSync migration applied (no git checkpoint).");
    }
    log(`Applied migration: ${suggestion.migration_signal || proposal.old_pattern || "unknown"}`);
}

/* ------------------------------------------------------------------ watcher */

function getPythonCommand(root) {
    const venvPython = path.join(
        root, ".venv", process.platform === "win32" ? "Scripts" : "bin",
        process.platform === "win32" ? "python.exe" : "python"
    );
    if (fs.existsSync(venvPython)) {
        return { command: venvPython, args: ["-u", "-m", "frameworksync.watcher"], cwd: root };
    }
    return {
        command: process.platform === "win32" ? "uv.exe" : "uv",
        args: ["run", "python", "-u", "-m", "frameworksync.watcher"],
        cwd: root,
    };
}

function killTree(child) {
    if (!child || !child.pid) return;
    try {
        if (process.platform === "win32") {
            // child.kill() only ends the launcher on Windows and leaves python running.
            cp.spawn("taskkill", ["/pid", String(child.pid), "/T", "/F"], { windowsHide: true });
        } else {
            child.kill();
        }
    } catch (error) {
        log(`Could not stop watcher: ${error.message}`);
    }
}

function stopWatcher() {
    killTree(watcherChild);
    watcherChild = null;
}

/* A watcher left over from an older window/extension version keeps running
 * old code; its PID is in data/watcher.lock. */
function killLockOwner(root) {
    try {
        const lock = path.join(root, "data", "watcher.lock");
        const pid = parseInt(fs.readFileSync(lock, "utf8").trim(), 10);
        if (pid && pid !== process.pid) {
            killTree({ pid });
            log(`Stopped old watcher process (pid ${pid}).`);
        }
        fs.rmSync(lock, { force: true });
    } catch {
        /* no lock file: nothing to stop */
    }
}

function startWatcher(target) {
    const root = getFrameworkSyncRoot();
    if (!root) {
        log(
            "Could not find the FrameworkSync project (src/frameworksync/watcher.py + pyproject.toml). " +
            'Open that folder, or set "frameworksync.projectRoot". Watcher not started.'
        );
        ui.watcher = "stopped";
        updateStatus();
        return null;
    }

    const python = getPythonCommand(root);
    const args = [...python.args, target, "--vscode"];

    log(`Project: ${root}`);
    log(`Target: ${target}`);
    log(`Command: ${python.command} ${args.join(" ")}`);

    ui.watcher = "starting";
    updateStatus();
    const child = cp.spawn(python.command, args, { cwd: python.cwd, windowsHide: true });

    const pipe = (stream, prefix) =>
        stream?.on("data", (data) => {
            for (const line of data.toString().split(/\r?\n/)) {
                if (!line.trim()) continue;
                log(`${prefix}${line}`);
                if (line.startsWith("[changed]")) {
                    ui.lastChanged = `${path.basename(line.slice(9).trim())} ${new Date().toLocaleTimeString()}`;
                    updateStatus();
                } else if (line.includes("Another FrameworkSync watcher is already running")) {
                    ui.watcher = "foreign";
                    ui.note = line.trim();
                    updateStatus();
                } else if (line.startsWith("Watching ")) {
                    ui.watcher = "running";
                    updateStatus();
                }
            }
        });
    pipe(child.stdout, "WATCHER: ");
    pipe(child.stderr, "WATCHER ERROR: ");

    child.on("error", (error) => {
        log(`Failed to start watcher: ${error.message}`);
        ui.watcher = "stopped";
        updateStatus();
        vscode.window.showErrorMessage(`FrameworkSync watcher failed: ${error.message}`);
    });
    child.on("spawn", () => log(`FrameworkSync watcher process started (pid ${child.pid}).`));
    child.on("exit", (code, signal) => {
        log(`FrameworkSync watcher exited. code=${code}, signal=${signal || "none"}`);
        if (ui.watcher !== "foreign") ui.watcher = "stopped";
        updateStatus();
    });

    return child;
}

/* ---------------------------------------------------------------- lifecycle */

function activate(context) {
    extContext = context;
    output = vscode.window.createOutputChannel("FrameworkSync");
    context.subscriptions.push(output);
    log("FrameworkSync extension activated.");

    const workspace = vscode.workspace.workspaceFolders?.[0];
    if (!workspace) {
        log("No workspace folder is open. FrameworkSync watcher will not start.");
        return;
    }

    const diagnostics = vscode.languages.createDiagnosticCollection("frameworksync");
    context.subscriptions.push(diagnostics);

    registerHover(context);
    registerCodeActions(context);

    extensionVersion = context.extension?.packageJSON?.version || "";
    statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 0);
    context.subscriptions.push(statusItem);
    updateStatus();

    watcherChild = startWatcher(workspace.uri.fsPath);
    context.subscriptions.push({ dispose: stopWatcher });

    refreshDiagnostics(diagnostics);

    let timer;
    const scheduleRefresh = () => {
        clearTimeout(timer);
        timer = setTimeout(() => refreshDiagnostics(diagnostics), 150);
    };

    const resultsPath = getResultsPath();
    if (resultsPath) {
        const fsWatcher = vscode.workspace.createFileSystemWatcher(
            new vscode.RelativePattern(path.dirname(resultsPath), path.basename(resultsPath))
        );
        context.subscriptions.push(
            fsWatcher,
            fsWatcher.onDidChange(scheduleRefresh),
            fsWatcher.onDidCreate(scheduleRefresh),
            fsWatcher.onDidDelete(() => log("Results file briefly missing; keeping current diagnostics."))
        );
    }

    // Safety net: file-system events are not 100% reliable on Windows.
    const poll = setInterval(() => {
        if (resultsChanged()) scheduleRefresh();
    }, 1500);
    context.subscriptions.push({ dispose: () => { clearInterval(poll); clearTimeout(timer); } });

    context.subscriptions.push(
        vscode.workspace.onDidOpenTextDocument(scheduleRefresh),
        // right after a save the old results no longer match the file: hide them at once
        vscode.workspace.onDidSaveTextDocument(scheduleRefresh),
        vscode.commands.registerCommand("frameworksync.refresh", () => refreshDiagnostics(diagnostics)),
        vscode.commands.registerCommand("frameworksync.showManualSteps", async (uri, suggestion) => {
            const proposal = suggestion?.proposal || {};
            const steps = proposal.manual_steps || [];

            // Safety net first: the file must be committed before you start editing by hand.
            const document = await vscode.workspace.openTextDocument(uri);
            const checkpoint = await ensureCheckpoint(document, suggestion?.migration_signal || proposal.old_pattern || "manual migration");
            if (!checkpoint) {
                vscode.window.showInformationMessage(
                    "FrameworkSync: manual steps not opened. Commit the file first so you can undo the migration."
                );
                return;
            }

            const safety = checkpoint.commit
                ? [`> **Safety net:** \`${path.basename(document.uri.fsPath)}\` is committed as \`${checkpoint.commit}\`. ` +
                   "If something goes wrong, run **FrameworkSync: Undo migration (restore git checkpoint)** " +
                   "from the Command Palette to get the original back.", ""]
                : [];
            const parts = [
                `# Manual migration: ${proposal.old_pattern || ""} → ${proposal.new_pattern || ""}`,
                "",
                ...safety,
                proposal.description || "",
                "",
                ...steps.flatMap((step, i) => [`## Step ${i + 1}`, "", step, ""]),
            ];
            const doc = await vscode.workspace.openTextDocument({ language: "markdown", content: parts.join("\n") });
            await vscode.commands.executeCommand("markdown.showPreviewToSide", doc.uri).then(
                undefined,
                () => vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.Beside })
            );
        }),
        vscode.commands.registerCommand("frameworksync.revertMigration", (file) =>
            revertMigration(typeof file === "string" ? file : undefined)
        ),
        vscode.commands.registerCommand("frameworksync.showLog", () => output.show(true)),
        vscode.commands.registerCommand("frameworksync.restartWatcher", () => {
            const root = getFrameworkSyncRoot();
            stopWatcher();
            if (root) killLockOwner(root);
            setTimeout(() => {
                watcherChild = startWatcher(workspace.uri.fsPath);
            }, 800);
        }),
        vscode.commands.registerCommand("frameworksync.applySuggestion", async (uri, suggestion) => {
            const document = await vscode.workspace.openTextDocument(uri);
            return applyProposal(document, suggestion, diagnostics);
        })
    );

    log(`FrameworkSync ready. Watching: ${workspace.uri.fsPath}`);
}

function deactivate() {
    if (output) output.appendLine("FrameworkSync extension deactivated.");
}

module.exports = { activate, deactivate };
