/*
 * Copyright (c) 2020 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import { ParsedFile } from './parse';

export type ConfigValue = string | number | boolean;
export type ConfigValueRange = { max: string, min: string, condition?: string };
export type ConfigValueType = 'string' | 'int' | 'hex' | 'bool' | 'tristate';
export type ConfigOverride = { config: Config, value: string, line?: number };
export type ConfigKind = 'config' | 'menuconfig' | 'choice';
export type ConfigDefault = {value: string, condition?: string};
export type ConfigSelect = {name: string, condition?: string};
export type LineRange = {start: number, end: number};

export class Comment {
	file: ParsedFile;
	text: string;
	line: number;

	constructor(text: string, file: ParsedFile, line: number) {
		this.text = text;
		this.file = file;
		this.line = line;
	}
}

export abstract class Scope {
	lines: LineRange;
	private _name: string;
	file: ParsedFile;
	children: (Scope | ConfigEntry | Comment)[];
	symbolKind: vscode.SymbolKind;

	constructor(public type: string, name: string, line: number, file: ParsedFile, symbolKind: vscode.SymbolKind) {
		this._name = name;
		this.lines = {start: line, end: line};
		this.file = file;
		this.symbolKind = symbolKind;
		this.children = [];
	}

	public get name(): string {
		return this._name;
	}
	public set name(value: string) {
		this._name = value;
	}

	addScope(s: Scope): Scope {
		this.children.push(s);
		return s;
	}

	get range(): vscode.Range {
		return new vscode.Range(this.lines.start, 0, this.lines.end, 9999);
	}
}

export class IfScope extends Scope {
	constructor(public expr: string, line: number, file: ParsedFile) {
		super('if', expr, line, file, vscode.SymbolKind.Module);
	}
}

export class MenuScope extends Scope {
	dependencies: string[];
	visible?: string;

	constructor(prompt: string, line: number, file: ParsedFile) {
		super('menu', prompt, line, file, vscode.SymbolKind.Class);
		this.dependencies = [];
	}
}

export class ChoiceScope extends Scope {
	choice: ChoiceEntry;
	constructor(choice: ChoiceEntry) {
		super('choice', choice.config.name, choice.lines.start, choice.file, vscode.SymbolKind.Enum);
		this.choice = choice;
	}

	// Override name property to dynamically get it from the ConfigEntry:
	get name(): string {
		return this.choice.text || this.choice.config.name;
	}

	set name(name: string) {}
}

export class RootScope extends Scope {
	constructor(repo: Repository) {
		super('root', 'ROOT', 0, new ParsedFile(repo, vscode.Uri.parse('commandline://'), {}), vscode.SymbolKind.Class);
	}

	reset() {
		this.children = [];
	}
}

export class ConfigEntry {
	config: Config;
	lines: LineRange;
	file: ParsedFile;
	help?: string;
	ranges: ConfigValueRange[];
	type?: ConfigValueType;
	text?: string;
	prompt: boolean;
	dependencies: string[];
	selects: ConfigSelect[];
	implys: ConfigSelect[];
	defaults: ConfigDefault[];

	constructor(config: Config, line: number, file: ParsedFile) {
		this.config = config;
		this.lines = {start: line, end: line};
		this.file = file;
		this.ranges = [];
		this.dependencies = [];
		this.selects = [];
		this.implys = [];
		this.defaults = [];
		this.prompt = false;
		this.config.entries.push(this);
	}

	extend(lineNumber: number)  {
		if (lineNumber < this.lines.start) {
			throw new Error("Extending upwards, shouldn't be possible.");
		}
		if (lineNumber <= this.lines.end) {
			return;
		}

		this.lines.end = lineNumber;
	}

	get loc(): vscode.Location {
		return new vscode.Location(this.file.uri, new vscode.Range(this.lines.start, 0, this.lines.end, 99999));
	}
}

export class Config {
	name: string;
	kind: ConfigKind;
	entries: ConfigEntry[];

	constructor(name: string, kind: ConfigKind) {
		this.name = name;
		this.kind = kind;
		this.entries = [];
	}

	get type(): ConfigValueType | undefined {
		return this.entries.find(e => e.type)?.type;
	}

	get help(): string {
		return this.entries.filter(e => e.help).map(e => e.help).join('\n\n');
	}

	get text(): string | undefined {
		return this.entries.find(e => e.text)?.text;
	}

	get defaults(): ConfigDefault[] {
		var defaults: ConfigDefault[] = [];
		this.entries.forEach(e => defaults.push(...e.defaults));
		return defaults;
	}

	get ranges(): ConfigValueRange[] {
		var ranges: ConfigValueRange[] = [];
		this.entries.forEach(e => ranges.push(...e.ranges));
		return ranges;
	}

	get implys(): ConfigSelect[] {
		var implys: ConfigSelect[] = [];
		this.entries.forEach(e => implys.push(...e.implys));
		return implys;
	}

	get mainEntry(): ConfigEntry | undefined {
		return this.entries.find(e => e.text);
	}

	get dependencies(): string[] {
		var dependencies: string[] = [];
		this.entries.forEach(e => dependencies.push(...e.dependencies));
		return dependencies;
	}

	get selects(): ConfigSelect[] {
		const selects: ConfigSelect[] = [];
		this.entries.forEach((e) => selects.push(...e.selects));
		return selects;
	}

	removeEntry(entry: ConfigEntry) {
		var i = this.entries.indexOf(entry);
		this.entries.splice(i, 1);
	}

	toValueString(value: ConfigValue): string {
		switch (this.type) {
			case 'bool':
			case 'tristate':
				return value ? 'y' : 'n';
			case 'int':
				return value.toString(10);
			case 'hex':
				return '0x' + value.toString(16);
			case 'string':
				return `"${value}"`;
			default:
				return 'n';
		}
	}

	symbolKind(): vscode.SymbolKind {
		switch (this.kind) {
			case "choice":
				return vscode.SymbolKind.Enum;
			case "menuconfig":
				return vscode.SymbolKind.Class;
			case "config":
				switch (this.type) {
					case "bool": return vscode.SymbolKind.Property;
					case "tristate": return vscode.SymbolKind.EnumMember;
					case "int": return vscode.SymbolKind.Number;
					case "hex": return vscode.SymbolKind.Number;
					case "string": return vscode.SymbolKind.String;
				}
				/* Intentionall fall-through: Want undefined types to be handled like undefined kinds */
			case undefined:
				return vscode.SymbolKind.Null;
		}
	}

	completionKind(): vscode.CompletionItemKind {
		switch (this.kind) {
			case "choice":
				return vscode.CompletionItemKind.Class;
			case "menuconfig":
				return vscode.CompletionItemKind.Field;
			case "config":
				switch (this.type) {
					case "bool": return vscode.CompletionItemKind.Field;
					case "tristate": return vscode.CompletionItemKind.Field;
					case "int": return vscode.CompletionItemKind.Property;
					case "hex": return vscode.CompletionItemKind.Property;
					case "string": return vscode.CompletionItemKind.Keyword;
				}
				/* Intentional fall-through: Want undefined types to be handled like undefined kinds */
			case undefined:
				return vscode.CompletionItemKind.Property;
		}
	}

	toString(): string {
		return `Config(${this.name})`;
	}
}

export class ChoiceEntry extends ConfigEntry {
	choices: ConfigEntry[];
	optional = false;

	constructor(name: string, line: number, file: ParsedFile) {
		super(new Config(name, 'choice'), line, file);
		this.choices = [];
	}
}

export class Repository {
	configs: {[name: string]: Config};

	private cachedConfigList?: Config[];
	root?: ParsedFile;
	rootScope: RootScope;
	diags: vscode.DiagnosticCollection;
	openEditors: vscode.Uri[];

	constructor(diags: vscode.DiagnosticCollection) {
		this.configs = {};
		this.diags = diags;
		this.openEditors = vscode.window.visibleTextEditors.filter(e => e.document.languageId === "kconfig").map(e => e.document.uri);
		this.openEditors.forEach(uri => this.setDiags(uri));
		this.cachedConfigList = [];
		this.rootScope = new RootScope(this);
	}

	activate(context: vscode.ExtensionContext) {
		context.subscriptions.push(vscode.window.onDidChangeVisibleTextEditors(e => {
			e = e.filter(e => e.document.languageId === 'kconfig');
			var newUris = e.map(e => e.document.uri);
			var removed = this.openEditors.filter(old => !newUris.some(uri => uri.fsPath === old.fsPath));
			var added = newUris.filter(newUri => !this.openEditors.some(uri => uri.fsPath === newUri.fsPath));

			removed.forEach(removed => this.diags.delete(removed));
			added.forEach(add => this.setDiags(add));

			this.openEditors = newUris;
		}));
	}

	get configList() {
		if (this.cachedConfigList === undefined) {
			this.cachedConfigList = Object.values(this.configs);
		}

		return this.cachedConfigList;
	}

	setRoot(uri: vscode.Uri) {
		this.configs = {};
		this.rootScope.reset();
		this.root = new ParsedFile(this, uri, {});
	}

	parse() {
		this.cachedConfigList = undefined;
		this.root?.parse();
		this.openEditors.forEach(uri => this.setDiags(uri));
		this.printStats();
	}

	reset() {
		this.rootScope.reset();
		this.configs = {};
		this.cachedConfigList = undefined;
	}

	removeEntry(e: ConfigEntry) {
		const config = e.config;

		config.removeEntry(e);
		if (config.entries.length === 0) {
			delete this.configs[config.name];
			this.cachedConfigList = undefined;
		}
	}

	get files(): ParsedFile[] { // TODO: optimize to a managed dict?
		if (!this.root) {
			return [];
		}

		return [this.root, ...this.root.children()];
	}

	setDiags(uri: vscode.Uri) {
		this.diags.set(uri,
			this.files
				.filter(f => f.uri.fsPath === uri.fsPath)
				.map(f => f.diags)
				.reduce((sum, diags) => sum.concat(diags.filter(d => !sum.some(existing => existing.range.start.line === d.range.start.line))), []));
	}

	onDidChange(uri: vscode.Uri, change?: vscode.TextDocumentChangeEvent) {
		if (change && change.contentChanges.length === 0) {
			return;
		}

		var hrTime = process.hrtime();

		var files = this.files.filter(f => f.uri.fsPath === uri.fsPath);
		if (!files.length) {
			return;
		}

		this.cachedConfigList = undefined;
		files.forEach(f => f.onDidChange(change));
		hrTime = process.hrtime(hrTime);

		this.openEditors.forEach(uri => this.setDiags(uri));
		if (vscode.debug.activeDebugSession) {
			console.log(`Handled changes to ${files.length} versions of ${uri.fsPath} in ${hrTime[0] * 1000 + hrTime[1] / 1000000} ms.`);
			this.printStats();
		}
	}

	printStats() {
		console.log(`\tFiles: ${this.files.length}`);
		console.log(`\tConfigs: ${this.configList.length}`);
		console.log(`\tEmpty configs: ${this.configList.filter(c => c.entries.length === 0).length}`);
		var entriesC = this.configList.map(c => c.entries).reduce((sum, num) => [...sum, ...num], []);
		console.log(`\tEntries: ${entriesC.length}`);

		var scopeEntries = (s: Scope) : ConfigEntry[] => {
			return s.children.map(c => (c instanceof Comment) ? [] : (c instanceof Scope) ? scopeEntries(c) : (c.config.kind === 'choice') ? [] : [c]).reduce((sum, num) => [...sum, ...num], []);
		};
		var entriesS = scopeEntries(this.rootScope);
		console.log(`\tEntries from scopes: ${entriesS.length}`);

		// console.log(`\tMissing Scope entries: ${entriesC.filter(e => !entriesS.includes(e)).map(e => e.config.name)}`);
		// console.log(`\tMissing Config entries: ${entriesS.filter(e => !entriesC.includes(e)).map(e => e.config.name)}`);
	}
}
