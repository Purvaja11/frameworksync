"use strict";
const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");
const cp = require("child_process");
const g = require("../git_safety");

// Git on Windows may rewrite line endings (autocrlf), so compare content, not EOL style.
const read = (f) => fs.readFileSync(f, "utf8").replace(/\r\n/g, "\n");

(async () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "fs-git-"));
    const file = path.join(dir, "a.py");
    const other = path.join(dir, "other.py");
    fs.writeFileSync(file, "x = 1\n");
    fs.writeFileSync(other, "y = 1\n");

    // not a repo yet
    let repo = await g.findRepo(file);
    assert.strictEqual(repo.ok, false);
    assert.strictEqual(repo.reason, "not-a-repo");

    assert.strictEqual((await g.initRepo(dir)).ok, true);
    for (const [k, v] of [["user.email", "t@t.t"], ["user.name", "t"]]) {
        cp.execFileSync("git", ["config", k, v], { cwd: dir });
    }
    assert.strictEqual((await g.findRepo(file)).ok, true);
    assert.strictEqual((await g.fileState(file)).state, "untracked");

    // commit only this file, on a brand-new repo
    const first = await g.commitFile(file, "checkpoint");
    assert.ok(first.ok, first.error);
    assert.strictEqual((await g.fileState(file)).state, "clean");
    assert.strictEqual((await g.fileState(other)).state, "untracked");   // untouched

    // change -> dirty -> commit -> clean
    fs.writeFileSync(file, "x = 2\n");
    assert.strictEqual((await g.fileState(file)).state, "dirty");

    // simulate: checkpoint, then a migration edit, then undo
    const cpoint = await g.commitFile(file, "before migration");
    assert.ok(cpoint.ok);
    fs.writeFileSync(file, "x = 'MIGRATED'\n");
    assert.strictEqual((await g.fileState(file)).state, "dirty");
    const restored = await g.restoreFile(file, cpoint.commit);
    assert.ok(restored.ok, restored.error);
    assert.strictEqual(read(file), "x = 2\n");        // original is back

    // restore also works after the migrated code was committed
    fs.writeFileSync(file, "x = 'MIGRATED'\n");
    assert.ok((await g.commitFile(file, "migrated")).ok);
    assert.ok((await g.restoreFile(file, cpoint.commit)).ok);
    assert.strictEqual(read(file), "x = 2\n");

    // ignored files are reported, not silently skipped
    fs.writeFileSync(path.join(dir, ".gitignore"), "ignored.py\n");
    fs.writeFileSync(path.join(dir, "ignored.py"), "z = 1\n");
    assert.strictEqual((await g.fileState(path.join(dir, "ignored.py"))).state, "ignored");

    // a hook that rejects the commit surfaces a readable error
    const hook = path.join(dir, ".git", "hooks", "pre-commit");
    fs.writeFileSync(hook, "#!/bin/sh\necho 'blocked by hook' >&2\nexit 1\n", { mode: 0o755 });
    fs.writeFileSync(file, "x = 3\n");
    const blocked = await g.commitFile(file, "nope");
    assert.strictEqual(blocked.ok, false);
    assert.match(blocked.error, /blocked by hook/);

    console.log("git_safety: all checks passed");
})().catch((e) => { console.error(e); process.exit(1); });
