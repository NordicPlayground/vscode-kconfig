/*
 * Copyright (c) 2020 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import * as zephyr from './zephyr';
import * as lsp from './lsp';
import Api from './api';
import { KconfigLangHandler } from './langHandler';

class TreeViewProvider implements vscode.TreeDataProvider<lsp.Node> {
	activate(ctx: vscode.ExtensionContext) {
		ctx.subscriptions.push(vscode.window.registerTreeDataProvider('kconfig', this));
	}

	// onDidChangeTreeData?: vscode.Event<void | lsp.Node | null | undefined> | undefined;
	getTreeItem(element: lsp.Node): vscode.TreeItem {
		const item = new vscode.TreeItem(
			element.prompt ?? 'anonymous',
			element.hasChildren ? vscode.TreeItemCollapsibleState.Collapsed : undefined
		);

		switch (element.kind) {
			case 'symbol':
				if (element.val === 'n') {
					item.collapsibleState = undefined;
				}
				item.description = element.val;
				item.tooltip = element.help;
				break;
			case 'comment':
				item.label = '';
				item.description = element.prompt;
				break;
			case 'choice':
				item.description = element.val;
				break;
		}

		return item;
	}

	async getChildren(element?: lsp.Node): Promise<lsp.Node[]> {
		const menu = await lsp.getMenu(undefined, element?.id);
		const items = menu?.items ?? [];
		if (items.length === 0) {
			return [
				{
					id: 'N/A',
					hasChildren: false,
					isMenu: false,
					kind: 'comment',
					depth: 0,
					visible: true,
					prompt: 'No visible symbols.',
				},
			];
		}

		return items;
	}
}

export var langHandler: KconfigLangHandler | undefined;
var context: vscode.ExtensionContext;

export async function startExtension() {
	await zephyr.activate();

	langHandler = new KconfigLangHandler();
	langHandler.activate(context);

	await lsp.activate(context);

	new TreeViewProvider().activate(context);
}

export function activate(ctx: vscode.ExtensionContext) {
	context = ctx;
	if (!vscode.extensions.getExtension('nordic-semiconductor.nrf-connect')) {
	}
	startExtension();

	return new Api();
}

export function deactivate() {
	langHandler?.deactivate();
	lsp.stop();
}
