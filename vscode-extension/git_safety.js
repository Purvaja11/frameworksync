"use strict";
/*
 * Git helpers for the migration safety net (no VS Code API, so it can be tested with node).
 *
 * Every command runs inside the file's own folder and names the file by its
 * base name, so it works for any repository layout and on Windows paths.
 * Only the one file being migrated is ever committed or restored.
 */

const cp = require("child_process");
const path = require("path");

function git(args, cwd) {
    return new Promise((resolve) => {
        cp.execFile(
            "git", args,
            { cwd, windowsHide: true, timeout: 30000, maxBuffer: 4 * 1024 * 1024 },
            (error, stdout, stderr) => {
                if (error) {
                    resolve({ ok: false, code: error.code, stdout: String(stdout || ""), stderr: String(stderr || error.message) });
                } else {
                    resolve({ ok: true, stdout: String(stdout), stderr: String(stderr) });
                }
            }
        );
    });
}

const where = (file) => ({ cwd: path.dirname(file), name: path.basename(file) });

/* { ok:true, root } | { ok:false, reason:"git-missing" | "not-a-repo" } */
async function findRepo(file) {
    const { cwd } = where(file);
    const r = await git(["rev-parse", "--show-toplevel"], cwd);
    if (r.ok) return { ok: true, root: r.stdout.trim() };
    if (r.code === "ENOENT" && !/^fatal/.test(r.stderr)) return { ok: false, reason: "git-missing" };
    return { ok: false, reason: "not-a-repo", detail: r.stderr.trim() };
}

/* { state: "clean" | "dirty" | "untracked" | "ignored" | "error" } */
async function fileState(file) {
    const { cwd, name } = where(file);
    const tracked = await git(["ls-files", "--error-unmatch", "--", name], cwd);
    if (!tracked.ok) {
        const ignored = await git(["check-ignore", "-q", "--", name], cwd);
        return { state: ignored.ok ? "ignored" : "untracked" };
    }
    const status = await git(["status", "--porcelain", "--", name], cwd);
    if (!status.ok) return { state: "error", detail: status.stderr.trim() };
    return { state: status.stdout.trim() ? "dirty" : "clean" };
}

async function head(file) {
    const { cwd } = where(file);
    const r = await git(["rev-parse", "--short", "HEAD"], cwd);
    return r.ok ? { ok: true, commit: r.stdout.trim() } : { ok: false, error: r.stderr.trim() };
}

/* Commit ONLY this file (other staged/changed files are left alone). */
async function commitFile(file, message) {
    const { cwd, name } = where(file);
    const add = await git(["add", "--", name], cwd);
    if (!add.ok) return { ok: false, error: add.stderr.trim() };
    const commit = await git(["commit", "-m", message, "--", name], cwd);
    if (!commit.ok) return { ok: false, error: (commit.stderr || commit.stdout).trim() };
    return head(file);
}

/* Put this one file back exactly as it was in `commit`. */
async function restoreFile(file, commit) {
    const { cwd, name } = where(file);
    const r = await git(["checkout", commit, "--", name], cwd);
    return r.ok ? { ok: true } : { ok: false, error: r.stderr.trim() };
}

async function initRepo(dir) {
    const r = await git(["init"], dir);
    return r.ok ? { ok: true } : { ok: false, error: r.stderr.trim() };
}

module.exports = { findRepo, fileState, head, commitFile, restoreFile, initRepo };
