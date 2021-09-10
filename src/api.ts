/*
 * Copyright (c) 2020 Trond Snekvik
 *
 * SPDX-License-Identifier: MIT
 */
import * as vscode from 'vscode';
import * as zephyr from './zephyr';
import { startExtension } from './extension';
import * as lsp from './lspClient';

class Api {
    public version = 3;

    async activate(zephyrBase: vscode.Uri, west: string, env?: typeof process.env): Promise<boolean> {
        await zephyr.setWest(west, env);
        await zephyr.setZephyrBase(zephyrBase);
        return startExtension();
    }

    setConfig(config?: vscode.Uri): void {
        lsp.setMainBuild(config);
    }
}

export default Api;
