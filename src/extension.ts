/* Copyright (c) 2021 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-1-Clause
 */

import * as vscode from 'vscode';
import * as zephyr from './zephyr';
import * as lsp from './lsp';
import Api from './api';
import { KconfigLangHandler } from './langHandler';

export var langHandler: KconfigLangHandler | undefined;
var context: vscode.ExtensionContext;

export async function startExtension() {
	await zephyr.activate();

	langHandler = new KconfigLangHandler();
	langHandler.activate(context);

	await lsp.activate(context);
}

export function activate(ctx: vscode.ExtensionContext) {
	context = ctx;
	if (!vscode.extensions.getExtension('nordic-semiconductor.nrf-connect')) {
		startExtension();
	}

	return new Api();
}

export function deactivate() {
	langHandler?.deactivate();
	lsp.stop();
}
