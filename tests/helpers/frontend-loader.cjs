// Exercise the actual native ES module graph in an isolated browser realm.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { spawnSync } = require('node:child_process');
const root = path.resolve(__dirname, '../../tinystudio/ui');

function ensureModuleRuntime() {
  if (vm.SourceTextModule) return;
  const result = spawnSync(process.execPath, ['--experimental-vm-modules', ...process.argv.slice(1)], { stdio: 'inherit', env: { ...process.env, NODE_NO_WARNINGS: '1' } });
  process.exit(result.status ?? 1);
}
ensureModuleRuntime();

async function loadFrontend(context, { transforms = {} } = {}) {
  const modules = new Map();
  async function load(filename) {
    filename = path.resolve(filename);
    if (modules.has(filename)) return modules.get(filename);
    if (!filename.startsWith(root + path.sep)) throw new Error('Module outside frontend: ' + filename);
    const relative = path.relative(root, filename).replaceAll(path.sep, '/');
    let source = fs.readFileSync(filename, 'utf8');
    if (context.ConversationSync && relative === 'assistant/conversation-sync.js') source = 'export const ConversationSync = globalThis.ConversationSync;';
    if (transforms[relative]) source = transforms[relative](source);
    const module = new vm.SourceTextModule(source, { context, identifier: filename });
    modules.set(filename, module);
    return module;
  }
  const application = await load(path.join(root, 'application.js'));
  await application.link((specifier, parent) => load(path.resolve(path.dirname(parent.identifier), specifier.split('?')[0])));
  await application.evaluate();
  const app = application.namespace.createApplication();
  const formats = await load(path.join(root, 'ui/format.js'));
  if (formats.status !== 'evaluated') await formats.evaluate();
  for (const [name, value] of Object.entries(formats.namespace)) context[name] = value;
  const alias = (object, names = Object.keys(object)) => {
    for (const name of names) {
      if (name in context) continue;
      Object.defineProperty(context, name, { configurable: true, get: () => object[name], set: value => { object[name] = value; } });
    }
  };
  context.state = app.state; context.ASSIST = app.assistant.state;
  alias(app.http); alias(app.feedback); alias(app.dialogs); alias(app.shell);
  alias(app.navigation); alias(app.projects); alias(app.overview); alias(app.auth); alias(app.tasks);
  alias(app.assistant.context); alias(app.assistant.conversation); alias(app.assistant.chat); alias(app.assistant.view);
  for (const pages of Object.values(app.pages)) alias(pages);
  return app;
}
module.exports = { loadFrontend, ensureModuleRuntime, root };
