const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const appPath = path.join(__dirname, "..", "src", "ebook_to_audio", "static", "app.js");
const source = fs.readFileSync(appPath, "utf8");

const sandbox = {
  console,
  document: {
    addEventListener() {},
    querySelector() {
      return null;
    },
    querySelectorAll() {
      return [];
    },
  },
  fetch: async () => {
    throw new Error("fetch should not be called by helper tests");
  },
  FormData: class FormData {},
  window: {},
};
sandbox.window = sandbox;

vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: appPath });

assert.strictEqual(sandbox.window.EBookToAudio.formatCount(12345), "12,345");
assert.strictEqual(
  sandbox.window.EBookToAudio.progressPercent({ total_units: 4, completed_units: 1 }),
  25,
);
assert.strictEqual(sandbox.window.EBookToAudio.statusLabel("completed_with_errors"), "部分完成");
