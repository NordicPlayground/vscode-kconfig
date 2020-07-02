// The module 'vscode' contains the VS Code extensibility API
// Import the module and reference it with the alias vscode in your code below
import * as vscode from 'vscode';
import * as fuzzy from "fuzzysort";
import { Operator } from './evaluate';
import { Config, ConfigOverride, ConfigEntry, Repository, IfScope, Scope, Comment, EvalContext } from "./kconfig";
import * as kEnv from './env';
import * as zephyr from './zephyr';
import { PropFile } from './propfile';
import * as fs from 'fs';
import * as path from 'path';

class KconfigLangHandler
	implements
		vscode.DefinitionProvider,
		vscode.HoverProvider,
		vscode.CompletionItemProvider,
		vscode.DocumentLinkProvider,
		vscode.ReferenceProvider,
		vscode.CodeActionProvider,
		vscode.DocumentSymbolProvider,
		vscode.WorkspaceSymbolProvider {
	diags: vscode.DiagnosticCollection;
	fileDiags: {[uri: string]: vscode.Diagnostic[]};
	propFiles: { [uri: string]: PropFile };
	operatorCompletions: vscode.CompletionItem[];
	repo: Repository;
	conf: ConfigOverride[];
	temporaryRoot=false;
	rootChangeIgnore = new Array<string>();
	propfileRefreshTimer?: NodeJS.Timeout;
	constructor() {
		this.operatorCompletions = [
			new vscode.CompletionItem('if', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('optional', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('endif', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('endchoice', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('endmenu', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('bool', vscode.CompletionItemKind.TypeParameter),
			new vscode.CompletionItem('int', vscode.CompletionItemKind.TypeParameter),
			new vscode.CompletionItem('hex', vscode.CompletionItemKind.TypeParameter),
			new vscode.CompletionItem('tristate', vscode.CompletionItemKind.TypeParameter),
			new vscode.CompletionItem('string', vscode.CompletionItemKind.TypeParameter),
			new vscode.CompletionItem('config', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('menu', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('menuconfig', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('choice', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('depends on', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('visible if', vscode.CompletionItemKind.Keyword),
			new vscode.CompletionItem('default', vscode.CompletionItemKind.Keyword),
		];

		var range = new vscode.CompletionItem('range', vscode.CompletionItemKind.Keyword);
		range.insertText = new vscode.SnippetString('range ');
		range.insertText.appendPlaceholder('min');
		range.insertText.appendText(' ');
		range.insertText.appendPlaceholder('max');
		this.operatorCompletions.push(range);

		var help = new vscode.CompletionItem('help', vscode.CompletionItemKind.Keyword);
		help.insertText = new vscode.SnippetString('help\n  ');
		help.insertText.appendTabstop();
		help.commitCharacters = [' ', '\t', '\n'];
		this.operatorCompletions.push(help);

		this.fileDiags = {};
		this.propFiles = {};
		this.diags = vscode.languages.createDiagnosticCollection('kconfig');
		this.repo = new Repository(this.diags);
		zephyr.setRepo(this.repo);
		this.conf = [];
	}

	private suggestKconfigRoot(propFile: PropFile) {
		// hint at Kconfig root file
		let kconfigRoot = path.resolve(path.dirname(propFile.uri.fsPath), 'Kconfig');
		if (!this.rootChangeIgnore.includes(kconfigRoot) && kconfigRoot !== this.repo.root?.uri.fsPath && fs.existsSync(kconfigRoot)) {
			vscode.window.showInformationMessage(`A Kconfig file exists in this directory.\nChange the Kconfig root file?`, 'Temporarily', 'Permanently', 'Never').then(t => {
				if (t === 'Temporarily') {
					this.repo.setRoot(vscode.Uri.file(kconfigRoot));
					this.rescan(false);
					this.temporaryRoot = true;
				} else if (t === 'Permanently') {
					kEnv.setConfig('root', kconfigRoot);
				} else {
					this.rootChangeIgnore.push(kconfigRoot);
				}
			});
		}
	}

	registerHandlers(context: vscode.ExtensionContext) {
		var disposable: vscode.Disposable;

		disposable = vscode.workspace.onDidChangeTextDocument(async e => {
			if (e.document.languageId === 'kconfig') {
				this.repo.onDidChange(e.document.uri, e);
			} else if (e.document.languageId === 'properties' && e.contentChanges.length > 0) {
				var file = this.propFile(e.document.uri);
				file.onChange(e);
			}
		});
		context.subscriptions.push(disposable);

		// Watch changes to files that aren't opened in vscode.
		// Handles git checkouts and similar out-of-editor events
		var watcher = vscode.workspace.createFileSystemWatcher('**/Kconfig*', true, false, true);
		watcher.onDidChange(uri => {
			if (!vscode.workspace.textDocuments.some(d => d.uri.fsPath === uri.fsPath)) {
				this.repo.onDidChange(uri);

				if (this.propfileRefreshTimer) {
					clearTimeout(this.propfileRefreshTimer);
				}

				this.propfileRefreshTimer = setTimeout(() => this.refreshOpenPropfiles(), 1000);
			}
		});
		context.subscriptions.push(watcher);

		disposable = vscode.window.onDidChangeActiveTextEditor(e => {
			if (e?.document.languageId === 'properties') {
				var file;
				if (this.temporaryRoot) {
					this.temporaryRoot = false;
					this.rescan();
					file = this.propFile(e.document.uri);
				} else {
					file = this.propFile(e.document.uri);
					file.reparse(e.document);
				}

				this.suggestKconfigRoot(file);
			}
		});
		context.subscriptions.push(disposable);

		disposable = vscode.workspace.onDidSaveTextDocument(d => {
			if (d.languageId === 'properties') {
				var file = this.propFile(d.uri);
				file.onSave(d);
			}
		});
		context.subscriptions.push(disposable);

		disposable = vscode.workspace.onDidOpenTextDocument(d => {
			if (d.languageId === 'properties') {
				var file;
				if (this.temporaryRoot) {
					this.temporaryRoot = false;
					this.rescan();
					file = this.propFile(d.uri);
				} else {
					file = this.propFile(d.uri);
					file.onOpen(d);
				}

				this.suggestKconfigRoot(file);
			}
		});
		context.subscriptions.push(disposable);

		disposable = vscode.workspace.onDidChangeConfiguration(e => {
			if (e.affectsConfiguration('kconfig')) {
				kEnv.update();
				this.rescan();
			}
		});
		context.subscriptions.push(disposable);

		var selector = [{ language: 'kconfig', scheme: 'file' }, { language: 'properties', scheme: 'file' }];

		disposable = vscode.languages.registerDefinitionProvider(selector.concat([{language: 'c', scheme: 'file'}]), this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerHoverProvider(selector.concat([{language: 'c', scheme: 'file'}]), this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerCompletionItemProvider(selector, this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerDocumentLinkProvider({ language: 'kconfig', scheme: 'file' }, this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerCodeActionsProvider({ language: 'properties', scheme: 'file' }, this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerDocumentSymbolProvider(selector, this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerWorkspaceSymbolProvider(this);
		context.subscriptions.push(disposable);
		disposable = vscode.languages.registerReferenceProvider({ language: 'kconfig', scheme: 'file' }, this);
		context.subscriptions.push(disposable);
	}

	propFile(uri: vscode.Uri): PropFile {
		if (!(uri.fsPath in this.propFiles)) {
			this.propFiles[uri.fsPath] = new PropFile(uri, this.repo, this.conf, this.diags);
		}

		return this.propFiles[uri.fsPath];
	}

	rescan(updateRoot=true) {
		this.propFiles = {};
		this.diags.clear();
		this.repo.reset();

		return this.doScan(updateRoot);
	}

	refreshOpenPropfiles() {
		vscode.window.visibleTextEditors
			.filter(e => e.document.languageId === 'properties')
			.forEach(e => this.propFile(e.document.uri).reparse(e.document));
	}

	activate(context: vscode.ExtensionContext) {
		this.registerHandlers(context);
		this.doScan();

		if (vscode.window.activeTextEditor?.document.languageId === 'properties') {
			this.suggestKconfigRoot(this.propFile(vscode.window.activeTextEditor.document.uri));
		}
	}

	private doScan(updateRoot=true) {
		var hrTime = process.hrtime();

		if (updateRoot) {
			var root = kEnv.getRootFile();
			if (root) {
				this.repo.setRoot(root);
				this.repo.parse();
			}
		} else {
			this.repo.parse();
		}

		hrTime = process.hrtime(hrTime);

		this.conf = this.loadConfOptions();

		this.refreshOpenPropfiles();

		var time_ms = Math.round(hrTime[0] * 1000 + hrTime[1] / 1000000);
		vscode.window.setStatusBarMessage(`Kconfig: ${Object.keys(this.repo.configs).length} entries, ${time_ms} ms`, 5000);
	}

	loadConfOptions(): ConfigOverride[] {
		var conf: { [config: string]: string | boolean | number } = kEnv.getConfig('conf');
		var entries: ConfigOverride[] = [];
		Object.keys(conf).forEach(c => {
			var e = this.repo.configs[c];
			if (e) {
				var value;
				if (value === true) {
					value = 'y';
				} else if (value === false) {
					value = 'n';
				} else {
					value = conf[c].toString();
				}
				entries.push({ config: e, value: value });
			}
		});

		var conf_files: string[] = kEnv.getConfig('conf_files');
		if (conf_files) {
			conf_files.forEach(f => {
				try {
					var text = kEnv.readFile(vscode.Uri.file(kEnv.pathReplace(f)));
				} catch (e) {
					if (e instanceof Error) {
						if ('code' in e && e['code'] === 'ENOENT') {
							vscode.window.showWarningMessage(`File "${f}" not found`);
						} else {
							vscode.window.showWarningMessage(`Error while reading conf file ${f}: ${e.message}`);
						}
					}
					return;
				}

				var file = new PropFile(vscode.Uri.file(f), this.repo, [], this.diags);
				file.parse(text);
				entries.push(...file.conf);
			});
		}

		return entries;
	}

	getSymbolName(document: vscode.TextDocument, position: vscode.Position) {
		var range = document.getWordRangeAtPosition(position);
		var word = document.getText(range);
		switch (document.languageId) {
			case 'kconfig':
				return word;
			default:
				if (word.startsWith('CONFIG_')) {
					return word.slice('CONFIG_'.length);
				}
		}
		return '';
	}

	provideDefinition(document: vscode.TextDocument, position: vscode.Position, token: vscode.CancellationToken): vscode.ProviderResult<vscode.Location | vscode.Location[] | vscode.LocationLink[]> {
		if (document.languageId === 'c' && !(kEnv.getConfig('kconfig.cfiles') ?? true)) {
			return null;
		}


		var config = this.repo.configs[this.getSymbolName(document, position)];
		if (config) {
			return ((config.entries.length === 1) ?
				config.entries :
				config.entries.filter(e => e.file.uri.fsPath !== document.uri.fsPath || position.line < e.lines.start || position.line > e.lines.end))
				.map(e => e.loc);
		}
		return null;
	}

	provideHover(document: vscode.TextDocument, position: vscode.Position, token: vscode.CancellationToken): vscode.ProviderResult<vscode.Hover> {
		if (document.languageId === 'c' && !(kEnv.getConfig('kconfig.cfiles') ?? true)) {
			return null;
		}

		var entry = this.repo.configs[this.getSymbolName(document, position)];
		if (!entry) {
			return null;
		}
		var text = new Array<vscode.MarkdownString>();
		text.push(new vscode.MarkdownString(`${entry.text || entry.name}`));
		if (entry.type) {
			var typeLine = new vscode.MarkdownString(`\`${entry.type}\``);
			if (entry.ranges.length === 1) {
				typeLine.appendMarkdown(`\t\tRange: \`${entry.ranges[0].min}\`-\`${entry.ranges[0].max}\``);
			}
			text.push(typeLine);
		}
		if (entry.help) {
			text.push(new vscode.MarkdownString(entry.help));
		}
		return new vscode.Hover(text, document.getWordRangeAtPosition(position));
	}

	provideCompletionItems(document: vscode.TextDocument, position: vscode.Position, token: vscode.CancellationToken, context: vscode.CompletionContext): vscode.ProviderResult<vscode.CompletionItem[] | vscode.CompletionList> {
		var line = document.lineAt(position.line);
		var wordRange = document.getWordRangeAtPosition(position);

		var wordBase = wordRange ? document.getText(wordRange).slice(0, position.character).replace(/^CONFIG_/, '') : '';

		var isProperties = (document.languageId === 'properties');
		var items: vscode.CompletionItem[];

		if (!isProperties && !line.text.match(/(if|depends\s+on|select|default|def_bool|def_tristate|def_int|def_hex|range)/)) {
			return this.operatorCompletions;
		}

		const maxCount = 4324324234500;
		var entries: Config[];
		var count: number;
		if (false && wordBase.length > 0) {
			var result = fuzzy.go(wordBase,
				this.repo.configList,
				{ key: 'name', limit: maxCount, allowTypo: true });

			entries = result.map(r => r.obj);
			count = result.total;
		} else {
			entries = this.repo.configList;
			count = entries.length;
			entries = entries.slice(0, maxCount);
		}


		if (isProperties) {
			var lineRange = new vscode.Range(position.line, 0, position.line, 999999);
			var lineText = document.getText(lineRange);
			var replaceText = lineText.replace(/\s*#.*$/, '');
		}

		const kinds = {
			'config': vscode.CompletionItemKind.Variable,
			'menuconfig': vscode.CompletionItemKind.Class,
			'choice': vscode.CompletionItemKind.Enum,
		};

		items = entries.map(e => {
			var item = new vscode.CompletionItem(isProperties ? `CONFIG_${e.name}` : e.name, (e.kind ? kinds[e.kind] : vscode.CompletionItemKind.Text));
			item.sortText = e.name;
			item.detail = e.text;
			if (isProperties) {
				if (replaceText.length > 0) {
					item.range = new vscode.Range(position.line, 0, position.line, replaceText.length);
				}

				item.insertText = new vscode.SnippetString(`${item.label}=`);
				switch (e.type) {
					case 'bool':
						if (e.defaults.length > 0 && e.defaults[0].value === 'y') {
							item.insertText.appendPlaceholder('n');
						} else {
							item.insertText.appendPlaceholder('y');
						}
						break;
					case 'tristate':
						item.insertText.appendPlaceholder('y');
						break;
					case 'int':
					case 'string':
						if (e.defaults.length > 0) {
							item.insertText.appendPlaceholder(e.defaults[0].value);
						} else {
							item.insertText.appendTabstop();
						}
						break;
					case 'hex':
						if (e.defaults.length > 0) {
							item.insertText.appendPlaceholder(e.defaults[0].value);
						} else {
							item.insertText.appendText('0x');
							item.insertText.appendTabstop();
						}
						break;
					default:
						break;
				}
			}

			return item;
		});

		if (!isProperties) {
			items.push(new vscode.CompletionItem('if', vscode.CompletionItemKind.Keyword));
		}

		return { isIncomplete: (count >= maxCount), items: items };
	}

	resolveCompletionItem(item: vscode.CompletionItem, token: vscode.CancellationToken): vscode.ProviderResult<vscode.CompletionItem> {
		if (!item.sortText) {
			return item;
		}
		var e = this.repo.configs[item.sortText];
		if (!e) {
			return item;
		}
		var doc = new vscode.MarkdownString(`\`${e.type}\``);
		if (e.ranges.length === 1) {
			doc.appendMarkdown(`\t\tRange: \`${e.ranges[0].min}\`-\`${e.ranges[0].max}\``);
		}
		if (e.help) {
			doc.appendText('\n\n');
			doc.appendMarkdown(e.help);
		}
		if (e.defaults.length > 0) {
			if (e.defaults.length > 1) {
				doc.appendMarkdown('\n\n### Defaults:\n');
			} else {
				doc.appendMarkdown('\n\n**Default:** ');
			}
			e.defaults.forEach(dflt => {
				doc.appendMarkdown(`\`${dflt.value}\``);
				if (dflt.condition) {
					doc.appendMarkdown(` if \`${dflt.condition}\``);
				}
				doc.appendMarkdown('\n\n');
			});
		}
		item.documentation = doc;
		return item;
	}

	provideDocumentLinks(document: vscode.TextDocument, token: vscode.CancellationToken): vscode.DocumentLink[] {
		var file = this.repo.files.find(f => f.uri.fsPath === document.uri.fsPath);
		return file?.links ?? [];
	}

	provideReferences(document: vscode.TextDocument,
		position: vscode.Position,
		context: vscode.ReferenceContext,
		token: vscode.CancellationToken): vscode.ProviderResult<vscode.Location[]> {
		var entry = this.repo.configs[this.getSymbolName(document, position)];
		if (!entry || !entry.type || !['bool', 'tristate'].includes(entry.type)) {
			return null;
		}
		return this.repo.configList
			.filter(config => (
				config.allSelects(entry.name).length > 0 ||
				config.hasDependency(entry!.name)))
			.map(config => config.entries[0].loc); // TODO: return the entries instead?
	}

	provideCodeActions(document: vscode.TextDocument,
		range: vscode.Range | vscode.Selection,
		context: vscode.CodeActionContext,
		token: vscode.CancellationToken): vscode.ProviderResult<vscode.CodeAction[]> {
		if (document.uri.fsPath in this.propFiles) {
			return this.propFiles[document.uri.fsPath].actions
				.filter(a => (!context.only || context.only === a.kind) && a.diagnostics?.[0].range.intersection(range));
		}
	}

	provideDocumentSymbols(document: vscode.TextDocument, token: vscode.CancellationToken): vscode.ProviderResult<vscode.DocumentSymbol[]> {
		if (document.languageId === 'properties') {
			return this.propFile(document.uri)
				.overrides.filter(o => o.line !== undefined)
				.map(
					o =>
						new vscode.DocumentSymbol(
							o.config.name,
							o.config.text ?? "",
							o.config.symbolKind(),
							new vscode.Range(o.line!, 0, o.line!, 99999),
							new vscode.Range(o.line!, 0, o.line!, 99999)
						)
				);
		}
		var file = this.repo.files.find(f => f.uri.fsPath === document.uri.fsPath);
		if (!file) {
			return [];
		}

		var addScope = (scope: Scope): vscode.DocumentSymbol => {
			var name: string = scope.name;
			if ((scope instanceof IfScope) && (scope.expr?.operator === Operator.VAR)) {
				var config = this.repo.configs[scope.expr.var!.value];
				name = config?.text ?? config?.name ?? scope.name;
			}

			var symbol = new vscode.DocumentSymbol(name, '',
				scope.symbolKind,
				scope.range,
				new vscode.Range(scope.lines.start, 0, scope.lines.start, 9999));

			symbol.children = (scope.children.filter(c => !(c instanceof Comment) && c.file === file) as (Scope | ConfigEntry)[])
			.map(c =>
				(c instanceof Scope)
					? addScope(c)
					: new vscode.DocumentSymbol(
							c.config.text ?? c.config.name,
							c.config.text ? "" : c.config.name,
							c.config.symbolKind(),
							new vscode.Range(c.lines.start, 0, c.lines.end, 9999),
							new vscode.Range(c.lines.start, 0, c.lines.start, 9999)
					  )
			)
			.reduce((prev, curr) => {
				if (prev.length > 0 && curr.name === prev[prev.length - 1].name) {
					prev[prev.length - 1].children.push(...curr.children);
					prev[prev.length - 1].range = prev[prev.length - 1].range.union(curr.range);
					return prev;
				}
				return [...prev, curr];
			}, new Array<vscode.DocumentSymbol>());

			return symbol;
		};

		return addScope(file.scope).children;
	}

	provideWorkspaceSymbols(query: string, token: vscode.CancellationToken): vscode.ProviderResult<vscode.SymbolInformation[]> {
		var entries = fuzzy.go(query.replace(/^CONFIG_/, ''), this.repo.configList, {key: 'name'});

		return entries.map(result => new vscode.SymbolInformation(
			result.obj.name,
			vscode.SymbolKind.Property,
			result.obj.entries[0].scope?.name ?? '',
			result.obj.entries[0].loc));
	}

}

export async function activate(context: vscode.ExtensionContext) {

	await zephyr.activate();
	kEnv.update();

	if (!kEnv.isActive()) {
		return;
	}

	var langHandler = new KconfigLangHandler();

	langHandler.activate(context);
}

export function deactivate() {}
