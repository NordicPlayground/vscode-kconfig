/* Copyright (c) 2021 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-1-Clause
 */

import * as vscode from 'vscode';
import {
	Scope,
	ConfigValueType,
	ConfigEntry,
	IfScope,
	MenuScope,
	ChoiceScope,
	ChoiceEntry,
	Comment,
	RootScope,
} from './kconfig';
import * as kEnv from './env';

type FileInclusion = { range: vscode.Range; path: string; relative: boolean };

export class ParsedFile {
	inclusions: FileInclusion[];
	entries: ConfigEntry[];
	diags: vscode.Diagnostic[];
	root: Scope;
	parsed: boolean;

	constructor(public readonly doc: vscode.TextDocument) {
		this.inclusions = [];
		this.entries = [];
		this.diags = [];
		this.root = new RootScope(this);
		this.parsed = false;
	}

	get uri(): vscode.Uri {
		return this.doc.uri;
	}

	reset() {
		this.diags = [];
		this.entries = [];
		this.inclusions = [];
		this.root = new RootScope(this);
	}

	parse() {
		const text = this.doc.getText();
		this.reset();
		const scopes = [this.root];

		var lines = text.split(/\r?\n/g);
		if (!lines) {
			return;
		}

		const getScope = () => {
			return scopes[scopes.length - 1];
		};

		const setScope = (s: Scope) => {
			scopes[scopes.length - 1].addScope(s);
			scopes.push(s);
		};

		var entry: ConfigEntry | null = null;
		var help = false;
		var helpIndent: string | null = null;
		for (var lineNumber = 0; lineNumber < lines.length; lineNumber++) {
			let line = lines[lineNumber];
			const startLineNumber = lineNumber;

			/* If lines end with \, the line ending should be ignored: */
			while (line.endsWith('\\') && lineNumber < lines.length - 1) {
				line = line.slice(0, line.length - 1) + lines[++lineNumber];
			}

			if (line.length === 0) {
				if (help && entry?.help) {
					entry.help += '\n\n';
				}
				continue;
			}

			var lineRange = new vscode.Range(startLineNumber, 0, lineNumber, line.length);

			if (help) {
				var indent = line.replace(/\t/g, ' '.repeat(8)).match(/^\s*/)![0];
				if (helpIndent === null) {
					helpIndent = indent;
				}
				if (indent.startsWith(helpIndent)) {
					if (entry) {
						entry.help += ' ' + line.trim();
						entry.extend(lineNumber);
					}
				} else {
					help = false;
					if (entry && entry.help) {
						entry.help = entry.help.trim();
					}
				}
			}

			if (help) {
				continue;
			}

			if (line.match(/^\s*(#|$)/)) {
				continue;
			}

			function getString(text: string) {
				const match =
					text.match(/^"(.*?)"\s*(?:$|#.*)/) ?? text.match(/^(\S+)\s*(?:$|#.*)/);
				return match?.[1];
			}

			function getSymbol(text: string) {
				return text.match(/^((?:\w+|\$\([\w-]+\))+)\s*(?:$|#.*)/)?.[1];
			}

			function getNumber(text: string) {
				return text.match(/^[+-]?(0x[a-f\d]+|\d+)\b/)?.[0];
			}

			function getExpression(text: string) {
				if (text.startsWith('"')) {
					return getString(text);
				}

				return (
					getNumber(text) ??
					text.match(
						/^((?:(\|\||&&|!?\s*\(|[)<>]|[!<>]?\=|"[^"]*"|'[^']*'|!?\s*\w+\b|!?\$\(.*\))\s*)+)\s*(?:$|#.*)/
					)?.[1]
				);
			}

			function getIf(text: string): string | undefined | vscode.Diagnostic {
				const ifMatch = text.match(/^\bif\s+(.*)/);
				const ifRange = lineRange.with({
					start: new vscode.Position(lineNumber, line.indexOf('if') + 'if'.length),
				});

				if (ifMatch) {
					const expr = getExpression(ifMatch[1]);
					if (!expr) {
						return new vscode.Diagnostic(
							ifRange,
							'Invalid expression',
							vscode.DiagnosticSeverity.Error
						);
					}

					return expr;
				}

				if (text.startsWith('if')) {
					return new vscode.Diagnostic(
						ifRange,
						'Expected expression',
						vscode.DiagnosticSeverity.Error
					);
				}

				return undefined;
			}

			function getExprIf(text: string) {
				let expr = text;

				match = rest.match(/\sif\s.*/);
				if (match) {
					expr = rest.slice(0, match.index! + 1);
				}

				const condition = getIf(match?.[0] ?? '');
				if (condition instanceof vscode.Diagnostic) {
					return condition;
				}

				if (getExpression(expr) === undefined && getString(expr) === undefined) {
					return new vscode.Diagnostic(
						lineRange,
						`Expected expression`,
						vscode.DiagnosticSeverity.Error
					);
				}

				return [expr, condition] as const;
			}

			/**
			 * Kconfig only contains one statement per line, and each type of statement
			 * starts with a different keyword. Fetch the first word of each line, and use
			 * that as a basis for parsing, to reduce the amount of regex comparisons executed
			 * on each line:
			 */
			const lineMatch = line.match(/^\s*(\w+)(?:\s+(.*)|$)/);
			if (!lineMatch) {
				continue;
			}

			const firstWord = lineMatch[1];
			const rest = lineMatch[2] ?? '';

			let match;
			switch (firstWord) {
				/*
				 * Root level statements:
				 */
				case 'comment':
					match = getString(rest);
					if (match) {
						getScope().children.push(new Comment(match, this, lineNumber));
					}
					break;

				case 'config':
				case 'menuconfig':
					match = getSymbol(rest);
					if (match) {
						entry = new ConfigEntry(match, startLineNumber, this);
						this.entries.push(entry);
						getScope().children.push(entry);
					} else {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected name',
								vscode.DiagnosticSeverity.Error
							)
						);
						entry = null;
					}
					break;

				case 'choice': {
					match = getSymbol(rest);
					const path = vscode.workspace.asRelativePath(this.uri);
					entry = new ChoiceEntry(
						match ?? `<choice at ${path}:${lineNumber + 1}>`,
						lineNumber,
						this
					);
					setScope(new ChoiceScope(entry as ChoiceEntry));
					break;
				}
				case 'source':
				case 'osource':
				case 'orsource':
				case 'rsource': {
					const path = getString(rest);
					if (!path) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected path string',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}
					this.inclusions.push({
						range: new vscode.Range(
							lineNumber,
							line.indexOf('"'),
							lineNumber,
							line.lastIndexOf('"') + 1
						),
						path,
						relative: ['rsource', 'orsource'].includes(firstWord),
					});
					break;
				}

				case 'if':
					entry = null;
					match = getExpression(rest);
					if (match) {
						setScope(new IfScope(match, lineNumber, this));
					} else if (rest.match(/^\s*(#.*)?$/)) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected expression',
								vscode.DiagnosticSeverity.Error
							)
						);
					} else {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Invalid expression',
								vscode.DiagnosticSeverity.Error
							)
						);
					}
					break;

				case 'mainmenu':
					entry = null;
					match = getString(rest);
					if (!match) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected prompt',
								vscode.DiagnosticSeverity.Error
							)
						);
					}
					break;

				case 'menu':
					entry = null;
					match = getString(rest);
					if (match) {
						setScope(new MenuScope(match, lineNumber, this));
					} else {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected prompt',
								vscode.DiagnosticSeverity.Error
							)
						);
					}
					break;

				/**
				 * Config entry attributes:
				 */

				case 'bool':
				case 'tristate':
				case 'string':
				case 'hex':
				case 'int':
					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Unexpected type outside config entry',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					entry.type = firstWord;
					match = getString(rest);
					if (match) {
						entry.prompt = match;
					}

					entry.extend(lineNumber);
					break;

				case 'prompt':
					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Unexpected prompt outside config entry',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}
					match = rest.match(/^"(.*)"\s*(.*)/);
					if (match) {
						const condition = getIf(match[2]);
						if (condition instanceof vscode.Diagnostic) {
							this.diags.push(condition);
							break;
						}

						entry.prompt = match[1];
					} else {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected prompt',
								vscode.DiagnosticSeverity.Error
							)
						);
					}
					entry.extend(lineNumber);
					break;

				case 'imply':
				case 'select': {
					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Unexpected ${firstWord} outside config entry`,
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					match = rest.match(/^(\w+)(?:\s*(.*))?/);
					if (!match) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Expected symbol`,
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					const condition = getIf(match[2] ?? '');
					if (condition instanceof vscode.Diagnostic) {
						this.diags.push(condition);
						break;
					}

					if (firstWord == 'imply') {
						entry.implys.push({ name: match[1], condition });
					} else {
						entry.selects.push({ name: match[1], condition });
					}
					entry.extend(lineNumber);
					break;
				}

				case 'option':
					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Unexpected ${firstWord} outside config entry`,
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					match = rest.match(/^env\=(.*)/);
					if (match) {
						const str = getString(match[1]);
						if (!str) {
							this.diags.push(
								new vscode.Diagnostic(
									lineRange,
									`Expected value after option env`,
									vscode.DiagnosticSeverity.Error
								)
							);
						}
						break;
					}

					match = rest.match(/^(defconfig_list|allnoconfig_y)\s*(#.*)?$/);
					if (match) {
						break;
					}

					match = rest.match(/^modules\s*(#.*)?$/);
					if (match) {
						if (entry.name !== 'MODULES') {
							this.diags.push(
								new vscode.Diagnostic(
									lineRange,
									`Modules option is only permitted on the MODULES entry.`,
									vscode.DiagnosticSeverity.Error
								)
							);
						}
						break;
					}

					match = rest.match(/^\w+/);
					if (match) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Unexpected option "${match[0]}"`,
								vscode.DiagnosticSeverity.Error
							)
						);
					} else {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Expected option.`,
								vscode.DiagnosticSeverity.Error
							)
						);
					}

					break;

				case 'def_bool':
				case 'def_tristate':
				case 'def_int':
				case 'def_hex':
				case 'def_string':
				case 'default': {
					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Unexpected ${firstWord} outside config entry`,
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					const expr = getExprIf(rest);
					if (expr instanceof vscode.Diagnostic) {
						this.diags.push(expr);
						break;
					}

					entry.defaults.push({ value: expr[0], condition: expr[1] });

					// Assign type:
					if (firstWord !== 'default') {
						entry.type = firstWord.substring('def_'.length) as ConfigValueType;
					}
					entry.extend(lineNumber);
					break;
				}

				case 'help':
					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Unexpected ${firstWord} outside config entry`,
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					help = true;
					helpIndent = null;
					entry.help = rest.match(/^(.*?)#?/)?.[1];
					entry.extend(lineNumber);
					break;

				case 'range': {
					match = rest.match(/^([+-]?\w+|\$\(.+\))\s+([+-]?\w+|\$\(.+\))(?:\s+(if.*))?/);
					if (!match) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected range',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					if (!entry) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								`Unexpected ${firstWord} outside config entry`,
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					const condition = getIf(match[3] ?? '');
					if (condition instanceof vscode.Diagnostic) {
						this.diags.push(condition);
						break;
					}

					entry.ranges.push({ min: match[1], max: match[2], condition });
					entry.extend(lineNumber);
					break;
				}

				case 'depends':
					match = rest.match(/^on\s+(.*)/);
					if (match) {
						const scope = getScope();
						if (
							!entry &&
							!(scope instanceof MenuScope) &&
							!(scope.children[scope.children.length - 1] instanceof Comment)
						) {
							this.diags.push(
								new vscode.Diagnostic(
									lineRange,
									'Unexpected "depends on" outside config entry',
									vscode.DiagnosticSeverity.Error
								)
							);
							break;
						}

						const expr = getExprIf(match[1]);
						if (expr instanceof vscode.Diagnostic) {
							this.diags.push(expr);
							break;
						}

						if (entry) {
							entry.dependencies.push({ expr: expr[0], condition: expr[1] });
							entry.extend(lineNumber);
						}
					} else {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected expression',
								vscode.DiagnosticSeverity.Error
							)
						);
					}
					break;

				/**
				 * Scope specific properties:
				 */

				case 'visible': {
					const condition = getIf(rest);
					if (!condition) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'Expected if-expression',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}
					if (condition instanceof vscode.Diagnostic) {
						this.diags.push(condition);
						break;
					}
					const scope = getScope();
					if (!(scope instanceof MenuScope)) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'"visible if" is only valid in menu scopes',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					scope.visible = condition;
					break;
				}

				case 'optional': {
					const scope = getScope();
					if (!(scope instanceof ChoiceScope)) {
						this.diags.push(
							new vscode.Diagnostic(
								lineRange,
								'"optional" is only valid in choice entries',
								vscode.DiagnosticSeverity.Error
							)
						);
						break;
					}

					scope.choice.optional = true;
					break;
				}

				/**
				 * Scope termination:
				 */
				case 'endif':
				case 'endmenu':
				case 'endchoice': {
					entry = null;
					const scope = getScope();
					const expectedScopes = {
						endif: IfScope,
						endmenu: MenuScope,
						endchoice: ChoiceScope,
					};
					if (!(scope instanceof expectedScopes[firstWord])) {
						const diag = new vscode.Diagnostic(
							lineRange,
							`Unexpected ${firstWord}`,
							vscode.DiagnosticSeverity.Error
						);
						if (scope) {
							diag.relatedInformation = [
								new vscode.DiagnosticRelatedInformation(
									new vscode.Location(this.uri, scope.range),
									'Opening scope'
								),
							];
						}
						this.diags.push(diag);
						break;
					}
					scope.lines.end = lineNumber;
					scopes.pop();
					break;
				}

				default:
					// macro
					match = line.match(/^\s*[\w-]+\s*:?\=.*/);
					if (match) {
						break;
					}

					this.diags.push(
						new vscode.Diagnostic(
							lineRange,
							`Invalid token`,
							vscode.DiagnosticSeverity.Error
						)
					);
			}
		}

		if (scopes.length > 1) {
			const s = scopes.pop()!;
			this.diags.push(
				new vscode.Diagnostic(
					new vscode.Range(s.lines.start, 0, s.lines.start, 9999),
					`Unterminated ${s.type}. Expected matching end${s.type} before end of parent scope.`,
					vscode.DiagnosticSeverity.Error
				)
			);
		}

		this.parsed = true;
	}
}
