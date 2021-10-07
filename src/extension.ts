/* Copyright (c) 2021 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-1-Clause
 */

import * as vscode from 'vscode';
import * as zephyr from './zephyr';
import * as lsp from './lsp';
import Api from './api';
import { KconfigLangHandler } from './langHandler';

export let langHandler: KconfigLangHandler | undefined;
let context: vscode.ExtensionContext;

export async function startExtension(): Promise<void> {
	await zephyr.activate();

	langHandler = new KconfigLangHandler();
	langHandler.activate(context);

	await lsp.activate(context);
}

export function activate(ctx: vscode.ExtensionContext): Api {
	context = ctx;
	if (!vscode.extensions.getExtension('nordic-semiconductor.nrf-connect')) {
		startExtension();
	}

	return new Api();
}

export function deactivate(): void {
	langHandler?.deactivate();
	lsp.stop();
}
